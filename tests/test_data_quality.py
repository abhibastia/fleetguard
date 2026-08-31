"""Live data-quality assertions against the Databricks workspace.

Skipped by default — these hit `bootcamp_students.fleetguard` and cost warehouse time.
Run with:

    pytest -m integration --run-integration

**These deliberately do not trust `_rescued_data`.** I-012 established that it stays 0
while the parse is silently wrong: Spark's CSV reader treats `"` as a quote character on
files that have no quoting convention, shifting fields on 143 complaint rows without
rescuing anything. Every assertion below therefore checks a *known cardinality* measured
independently, not an absence of errors.
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.integration

SCHEMA = "bootcamp_students.fleetguard"
PROFILE = "abhi"


def sql(query: str) -> list[dict]:
    """Run a query through the Databricks CLI and return parsed rows."""
    out = subprocess.run(
        [
            "databricks",
            "experimental",
            "aitools",
            "tools",
            "query",
            "--profile",
            PROFILE,
            query,
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:400])
    return json.loads(out.stdout)


def scalar(query: str):
    rows = sql(query)
    return next(iter(rows[0].values())) if rows else None


# --------------------------------------------------------------------------------------
# Corpus cardinalities — measured offline, must survive ingest unchanged
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("bronze_complaints", 2_240_289),
        ("bronze_recalls", 244_925),
        ("bronze_investigations", 154_367),
        ("bronze_tsbs", 5_801_279),
    ],
)
def test_bronze_row_counts_match_the_source_files(table, expected):
    assert int(scalar(f"SELECT COUNT(*) FROM {SCHEMA}.{table}")) == expected


def test_complaint_parse_is_correct_by_cardinality_not_by_rescued_data():
    """The check that would have caught I-012.

    With Spark's default quote handling these come out as 2,168,077 / 162 instead —
    while `_rescued_data` reads 0 either way.
    """
    row = sql(f"""
        SELECT
          SUM(CASE WHEN PROD_TYPE = 'V' THEN 1 ELSE 0 END) AS v,
          SUM(CASE WHEN PROD_TYPE = 'T' THEN 1 ELSE 0 END) AS t,
          SUM(CASE WHEN PROD_TYPE IS NULL THEN 1 ELSE 0 END) AS nulls
        FROM {SCHEMA}.bronze_complaints
    """)[0]
    assert int(row["v"]) == 2_168_220
    assert int(row["t"]) == 41_475
    assert int(row["nulls"]) == 19


def test_distinct_investigations_not_row_count():
    """154,367 rows are only 5,344 investigations (I-010). Quoting the row count as the
    backtest population overstates the evidence base ~29x."""
    assert (
        int(
            scalar(
                f"SELECT COUNT(DISTINCT NHTSA_ACTION_NUMBER) FROM {SCHEMA}.bronze_investigations"
            )
        )
        == 5_344
    )


def test_distinct_tsb_bulletins_not_row_count():
    """5.8M rows are 258,438 bulletins (I-024)."""
    assert int(scalar(f"SELECT COUNT(*) FROM {SCHEMA}.silver_tsb_bulletin")) == 258_438


# --------------------------------------------------------------------------------------
# Silver invariants
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("silver", "quarantine", "bronze_pred"),
    [
        (
            "silver_complaint",
            "silver_complaint_quarantine",
            "SELECT COUNT(*) FROM {s}.bronze_complaints WHERE UPPER(TRIM(PROD_TYPE)) IN ('V','T')",
        ),
        ("silver_recall", "silver_recall_quarantine", "SELECT COUNT(*) FROM {s}.bronze_recalls"),
        (
            "silver_investigation",
            "silver_investigation_quarantine",
            "SELECT COUNT(*) FROM {s}.bronze_investigations",
        ),
    ],
)
def test_nothing_is_dropped_silently(silver, quarantine, bronze_pred):
    """§6 requires quarantine routing with no silent drops.

    bronze = silver + quarantine, exactly. A shortfall means rows vanished.
    """
    src = int(scalar(bronze_pred.format(s=SCHEMA)))
    kept = int(scalar(f"SELECT COUNT(*) FROM {SCHEMA}.{silver}"))
    quarantined = int(scalar(f"SELECT COUNT(*) FROM {SCHEMA}.{quarantine}"))
    assert kept + quarantined == src, f"{src - kept - quarantined} rows unaccounted for"


def test_odino_is_not_used_as_a_dedup_key():
    """I-023: deduping on ODINO would discard 27.9% of the corpus.

    silver_complaint must hold materially more rows than it has distinct ODINOs.
    """
    row = sql(f"""
        SELECT COUNT(*) AS rows, COUNT(DISTINCT odi_number) AS odinos
        FROM {SCHEMA}.silver_complaint
    """)[0]
    rows, odinos = int(row["rows"]), int(row["odinos"])
    assert rows > odinos, "ODINO appears to have been used as a dedup key"
    assert (rows - odinos) > 500_000, f"only {rows - odinos:,} sibling rows — suspiciously few"


def test_product_type_scope_isolation():
    """Tire-only columns must be null on every vehicle row (§3)."""
    leaked = int(
        scalar(f"""
        SELECT COUNT(*) FROM {SCHEMA}.silver_complaint
        WHERE product_type = 'V'
          AND (tire_size IS NOT NULL OR tire_dot_id IS NOT NULL
               OR tire_location IS NOT NULL OR tire_failure_type IS NOT NULL)
    """)
    )
    assert leaked == 0


def test_harm_nulls_are_distinct_from_zeros():
    """§4.2: an unanswered field and a reported zero are different claims."""
    row = sql(f"""
        SELECT
          SUM(CASE WHEN injured IS NULL THEN 1 ELSE 0 END) AS null_injured,
          SUM(CASE WHEN injured = 0 THEN 1 ELSE 0 END)     AS zero_injured
        FROM {SCHEMA}.silver_complaint
    """)[0]
    # This corpus always populates the field, so zeros must dominate and the column
    # must not have been coalesced into zeros somewhere upstream.
    assert int(row["zero_injured"]) > 2_000_000


def test_park_it_flag_is_case_normalised():
    """I-022: DO_NOT_DRIVE is stored title-case 'Yes'.

    A case-sensitive comparison returns zero rows and reads as "no Park It recalls exist"
    rather than as a bug.
    """
    assert (
        int(
            scalar(
                f"SELECT COUNT(DISTINCT campaign_number) FROM {SCHEMA}.silver_recall WHERE do_not_drive"
            )
        )
        == 211
    )


# --------------------------------------------------------------------------------------
# Gold / fleet
# --------------------------------------------------------------------------------------


def test_every_fleet_vin_is_structurally_valid():
    """Checks shape and alphabet in SQL; the checksum itself is unit-tested off platform."""
    bad = int(
        scalar(f"""
        SELECT COUNT(*) FROM {SCHEMA}.gold_fleet_vehicle
        WHERE vin NOT RLIKE '^[A-HJ-NPR-Z0-9]{{17}}$'
    """)
    )
    assert bad == 0


def test_fleet_vins_are_unique():
    row = sql(f"""
        SELECT COUNT(*) AS n, COUNT(DISTINCT vin) AS d FROM {SCHEMA}.gold_fleet_vehicle
    """)[0]
    assert int(row["n"]) == int(row["d"]) == 20_000


def test_fleet_spans_light_through_class_8():
    """§3 claims light-vehicle-through-Class-8 scope; the first roster build was 100%
    pickups and no Class 8 at all (I-029)."""
    segments = {
        r["segment"]: int(r["n"])
        for r in sql(
            f"SELECT segment, COUNT(*) AS n FROM {SCHEMA}.gold_fleet_vehicle GROUP BY segment"
        )
    }
    assert set(segments) >= {"VAN", "PICKUP", "HEAVY"}
    assert segments["HEAVY"] > 1_000, "no meaningful Class 7/8 population"
    assert segments["VAN"] > 5_000, "a last-mile delivery fleet needs vans"


def test_exposure_records_match_basis():
    """I-030: variant matches outnumber exact ~3:1, and §7's deterministic guarantee
    only covers the EXACT tier — so the tier must be recorded per row."""
    tiers = {
        r["match_basis"]: int(r["n"])
        for r in sql(
            f"SELECT match_basis, COUNT(*) AS n FROM {SCHEMA}.gold_fleet_exposure GROUP BY match_basis"
        )
    }
    assert set(tiers) == {"EXACT", "MODEL_VARIANT"}
    assert tiers["MODEL_VARIANT"] > tiers["EXACT"]


# --------------------------------------------------------------------------------------
# Backtest integrity
# --------------------------------------------------------------------------------------


def test_backtest_population_is_the_post_2010_investigation_count():
    assert int(scalar(f"SELECT COUNT(*) FROM {SCHEMA}.gold_lead_time_backtest")) == 777


def test_backtest_has_a_control_arm():
    """A detection rate without a control is not evidence (I-027). The first placebo was
    itself broken — unmatched on volume — and showed a fake 160x separation."""
    arms = {r["arm"] for r in sql(f"SELECT arm FROM {SCHEMA}.gold_lead_time_summary")}
    assert len(arms) == 2, "backtest summary must report both real and placebo arms"


def test_no_detection_leaks_past_the_open_date():
    """A detection dated on or after the investigation opened is leakage, not lead time."""
    leaks = int(
        scalar(f"""
        SELECT COUNT(*) FROM {SCHEMA}.gold_lead_time_backtest
        WHERE detected AND detection_date >= open_date
    """)
    )
    assert leaks == 0
