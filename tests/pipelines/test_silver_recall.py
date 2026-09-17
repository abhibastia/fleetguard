"""`stg_recall` (src/pipelines/silver/silver_recall.sql) — the transformation logic behind
`silver_recall` / `silver_recall_quarantine`. Covers the two things this file's own comment
flags as deliberate and previously wrong: the Yes/No case normalisation (I-022) and the
inverted-manufacture-window quarantine rule. A row lands in silver iff `_dq_failures == ''`
— that column is what both downstream tables filter on, so asserting it here is asserting
the actual routing decision.
"""

from __future__ import annotations

from sql_extract import staging_view_sql

RELATIVE_PATH = "silver/silver_recall.sql"


def _bronze_row(**over) -> dict:
    row = {
        "RECORD_ID": "R1",
        "CAMPNO": "24V123",
        "MAKETXT": "ram",
        "MODELTXT": "2500",
        "YEARTXT": "2024",
        "COMPNAME": "brakes",
        "MFGNAME": "fca us llc",
        "RCLTYPECD": "v",
        "INFLUENCED_BY": "mfr",
        "BGMAN": "20240101",
        "ENDMAN": "20240601",
        "POTAFF": "1500",
        "RCDATE": "20240701",
        "ODATE": "20240705",
        "DO_NOT_DRIVE": "No",
        "PARK_OUTSIDE": "No",
        "DESC_DEFECT": "brake pad wear",
        "CONEQUENCE_DEFECT": "reduced stopping power",
        "CORRECTIVE_ACTION": "replace pads",
        "FMVSS": "105",
        "_source_file": "FLAT_RCL_POST_2010.txt",
        "_ingested_at": "2026-01-01T00:00:00",
    }
    row.update(over)
    return row


def _run(spark, register, rows):
    register("bronze_recalls", rows)
    spark.sql(staging_view_sql(RELATIVE_PATH, "stg_recall"))
    return spark.sql("SELECT * FROM stg_recall").collect()


def test_a_clean_row_has_no_failures_and_is_normalised_upper(spark, register):
    (row,) = _run(spark, register, [_bronze_row()])
    assert row["_dq_failures"] == ""
    assert row["make"] == "RAM"
    assert row["model"] == "2500"
    assert row["component"] == "BRAKES"


def test_do_not_drive_yes_is_parsed_case_insensitively(spark, register):
    """I-022: DO_NOT_DRIVE is stored title-case 'Yes' in the real files. A case-sensitive
    comparison against 'YES' would silently read every Park It recall as false."""
    (row,) = _run(spark, register, [_bronze_row(DO_NOT_DRIVE="Yes")])
    assert row["do_not_drive"] is True
    assert row["park_it"] is True


def test_park_outside_yes_alone_also_sets_park_it(spark, register):
    (row,) = _run(spark, register, [_bronze_row(DO_NOT_DRIVE="No", PARK_OUTSIDE="Yes")])
    assert row["do_not_drive"] is False
    assert row["park_it"] is True


def test_neither_flag_set_means_not_park_it(spark, register):
    (row,) = _run(spark, register, [_bronze_row(DO_NOT_DRIVE="No", PARK_OUTSIDE="No")])
    assert row["park_it"] is False


def test_missing_campaign_number_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(CAMPNO=None)])
    assert "missing_campaign_no" in row["_dq_failures"]


def test_missing_record_id_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(RECORD_ID=None)])
    assert "missing_record_id" in row["_dq_failures"]


def test_unparseable_report_date_is_quarantined(spark, register):
    (row,) = _run(spark, register, [_bronze_row(RCDATE="not-a-date")])
    assert "unparseable_rcdate" in row["_dq_failures"]


def test_inverted_manufacture_window_is_quarantined(spark, register):
    """224 real rows have BGMAN after ENDMAN — a scope defect that would silently
    under-match a fleet if kept rather than flagged."""
    (row,) = _run(spark, register, [_bronze_row(BGMAN="20240601", ENDMAN="20240101")])
    assert "inverted_manufacture_window" in row["_dq_failures"]


def test_equal_manufacture_start_and_end_is_not_inverted(spark, register):
    """A one-day manufacture window is unusual but not invalid — the rule is strictly
    greater-than, not greater-than-or-equal."""
    (row,) = _run(spark, register, [_bronze_row(BGMAN="20240101", ENDMAN="20240101")])
    assert "inverted_manufacture_window" not in row["_dq_failures"]


def test_a_missing_manufacture_window_is_not_flagged_as_inverted(spark, register):
    """No window at all is a different, unflagged condition here — only a window that
    exists and is backwards is a defect this rule catches."""
    (row,) = _run(spark, register, [_bronze_row(BGMAN=None, ENDMAN=None)])
    assert row["_dq_failures"] == ""


def test_multiple_failures_are_concatenated(spark, register):
    (row,) = _run(spark, register, [_bronze_row(RECORD_ID=None, CAMPNO=None)])
    assert "missing_record_id" in row["_dq_failures"]
    assert "missing_campaign_no" in row["_dq_failures"]


def test_year_9999_is_treated_as_missing_not_literal(spark, register):
    (row,) = _run(spark, register, [_bronze_row(YEARTXT="9999")])
    assert row["model_year"] is None
