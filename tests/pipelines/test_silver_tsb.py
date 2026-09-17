"""`silver_tsb` (src/pipelines/silver/silver_tsb.sql) — no separate staging view, so this
tests the `SELECT` body directly via `select_body` rather than a named temp view. This file
uses `EXPECT ... ON VIOLATION DROP ROW` instead of the quarantine-table pattern the other
silver files use, so there's no `_dq_failures` column to assert on — the constraints
themselves aren't runnable locally (pipeline-only syntax), so what's tested here is the
normalisation the SELECT performs before any constraint would apply.
"""

from __future__ import annotations

from sql_extract import select_body

RELATIVE_PATH = "silver/silver_tsb.sql"


def _bronze_row(**over) -> dict:
    row = {
        "NHTSA_ID": "tb26001",
        "TSB_DOCUMENT_ID": "doc-1",
        "MAKE": "ram",
        "MODEL": "2500",
        "YEARTXT": "2024",
        "COMMUNICATION_TYPE": "service bulletin",
        "NHTSA_COMPONENTS": "brakes",
        "MFR_COMPONENT_SYSTEM": "brake system",
        "MFR_COMPONENT_SUBSYSTEM": "pads",
        "MFR_COMM_DATE": "20260101",
        "DATEA": "20260102",
        "MFR_CAMPAIGN_ID": None,
        "SUMMARY": "brake pad wear bulletin",
        "_source_file": "TSBS_RECEIVED_2025-2026.txt",
        "_ingested_at": "2026-01-01T00:00:00",
    }
    row.update(over)
    return row


def _run(spark, register, rows):
    register("bronze_tsbs", rows)
    spark.sql(
        f"SELECT * FROM ({select_body(RELATIVE_PATH, 'silver_tsb')})"
    ).createOrReplaceTempView("silver_tsb_result")
    result = spark.sql("SELECT * FROM silver_tsb_result").collect()
    spark.catalog.dropTempView("silver_tsb_result")
    return result


def test_fields_are_uppercased_and_trimmed(spark, register):
    (row,) = _run(spark, register, [_bronze_row(MAKE=" ram ", MODEL=" 2500 ")])
    assert row["make"] == "RAM"
    assert row["model"] == "2500"


def test_year_9999_is_treated_as_missing(spark, register):
    (row,) = _run(spark, register, [_bronze_row(YEARTXT="9999")])
    assert row["model_year"] is None


def test_empty_campaign_id_becomes_null_not_empty_string(spark, register):
    (row,) = _run(spark, register, [_bronze_row(MFR_CAMPAIGN_ID="")])
    assert row["mfr_campaign_id"] is None


def test_communication_date_is_parsed(spark, register):
    (row,) = _run(spark, register, [_bronze_row(MFR_COMM_DATE="20260315")])
    assert str(row["communication_date"]).startswith("2026-03-15")
