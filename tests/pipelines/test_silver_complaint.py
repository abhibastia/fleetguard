"""`stg_complaint` (src/pipelines/silver/silver_complaint.sql) — the transformation behind
`silver_complaint` / `silver_complaint_quarantine`. Covers the three things this file's own
comment flags as deliberate and previously wrong: dedup must never key on ODINO (I-023, not
directly testable at this row-level grain, but the identity columns are asserted so a
regression toward using odi_number as a key would be visible), the PROD_TYPE scope filter
running before quality rules, and Y/N case normalisation (I-022's sibling bug).
"""

from __future__ import annotations

from sql_extract import staging_view_sql

RELATIVE_PATH = "silver/silver_complaint.sql"


def _bronze_row(**over) -> dict:
    row = {
        "CMPLID": "C1",
        "ODINO": "O1",
        "PROD_TYPE": "v",
        "MFR_NAME": "ram",
        "MAKETXT": "ram",
        "MODELTXT": "2500",
        "YEARTXT": "2024",
        "COMPDESC": "brakes",
        "VIN": "1FT1234",
        "LDATE": "20260101",
        "FAILDATE": "20251215",
        "CRASH": "N",
        "FIRE": "N",
        "MEDICAL_ATTN": "N",
        "POLICE_RPT_YN": "N",
        "VEHICLES_TOWED_YN": "N",
        "INJURED": "0",
        "DEATHS": "0",
        "TIRE_SIZE": None,
        "DOT": None,
        "LOC_OF_TIRE": None,
        "TIRE_FAIL_TYPE": None,
        "CDESCR": "brakes felt soft",
        "MILES": "12000",
        "CMPL_TYPE": "EVOQ",
        "STATE_OF_INCIDENT": "OH",
        "_source_file": "FLAT_CMPL.txt",
        "_ingested_at": "2026-01-01T00:00:00",
    }
    row.update(over)
    return row


def _run(spark, register, rows):
    register("bronze_complaints", rows)
    spark.sql(staging_view_sql(RELATIVE_PATH, "stg_complaint"))
    return spark.sql("SELECT * FROM stg_complaint").collect()


def test_a_clean_vehicle_row_has_no_failures(spark, register):
    (row,) = _run(spark, register, [_bronze_row()])
    assert row["_dq_failures"] == ""
    assert row["complaint_id"] == "C1"
    assert row["odi_number"] == "O1"


def test_tire_only_rows_pass_the_scope_filter(spark, register):
    (row,) = _run(spark, register, [_bronze_row(PROD_TYPE="T")])
    assert row["product_type"] == "T"


def test_rows_outside_v_and_t_scope_are_excluded_entirely(spark, register):
    """The scope filter runs in the same subquery as normalisation, before quality rules —
    an out-of-scope row must not appear at all, not even in quarantine."""
    rows = _run(spark, register, [_bronze_row(PROD_TYPE="E")])
    assert rows == []


def test_crash_yn_is_parsed_to_a_real_boolean_not_a_string(spark, register):
    (row,) = _run(spark, register, [_bronze_row(CRASH="Y")])
    assert row["crash"] is True


def test_an_unanswered_yn_field_is_null_not_false(spark, register):
    """§4.2: an unanswered field and a reported 'no' are different claims."""
    (row,) = _run(spark, register, [_bronze_row(CRASH="")])
    assert row["crash"] is None


def test_missing_complaint_id_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(CMPLID=None)])
    assert "missing_complaint_id" in row["_dq_failures"]


def test_missing_make_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(MAKETXT=None)])
    assert "missing_make" in row["_dq_failures"]


def test_unparseable_received_date_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(LDATE="garbage")])
    assert "unparseable_ldate" in row["_dq_failures"]


def test_incident_after_received_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(LDATE="20260101", FAILDATE="20260201")])
    assert "incident_after_received" in row["_dq_failures"]


def test_incident_before_received_is_not_flagged(spark, register):
    (row,) = _run(spark, register, [_bronze_row(LDATE="20260101", FAILDATE="20251231")])
    assert row["_dq_failures"] == ""


def test_negative_injured_count_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(INJURED="-1")])
    assert "negative_harm_count" in row["_dq_failures"]


def test_negative_deaths_count_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(DEATHS="-1")])
    assert "negative_harm_count" in row["_dq_failures"]


def test_zero_harm_counts_are_not_flagged(spark, register):
    (row,) = _run(spark, register, [_bronze_row(INJURED="0", DEATHS="0")])
    assert row["_dq_failures"] == ""
