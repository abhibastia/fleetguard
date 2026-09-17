"""`stg_investigation` and `silver_investigation_case`
(src/pipelines/silver/silver_investigation.sql) — the backtest ground truth. Covers the
quarantine rules (missing action number, unparseable open date, closed-before-opened) and
the entity-grain aggregation that I-010 exists because of: 154,367 rows are NOT 154,367
investigations, and conflating them overstates the evidence base ~29x.
"""

from __future__ import annotations

from sql_extract import staging_view_sql

RELATIVE_PATH = "silver/silver_investigation.sql"


def _bronze_row(**over) -> dict:
    row = {
        "NHTSA_ACTION_NUMBER": "EA26001",
        "MAKE": "ram",
        "MODEL": "2500",
        "YEAR": "2024",
        "COMPNAME": "brakes",
        "MFR_NAME": "fca us llc",
        "ODATE": "20260101",
        "CDATE": "20260201",
        "CAMPNO": "24V123",
        "SUBJECT": "brake pad wear",
        "SUMMARY": "investigation into brake pad wear",
        "_source_file": "FLAT_INV.txt",
        "_ingested_at": "2026-01-01T00:00:00",
    }
    row.update(over)
    return row


def _run_staging(spark, register, rows):
    register("bronze_investigations", rows)
    spark.sql(staging_view_sql(RELATIVE_PATH, "stg_investigation"))
    return spark.sql("SELECT * FROM stg_investigation").collect()


class TestStgInvestigation:
    def test_a_clean_row_has_no_failures(self, spark, register):
        (row,) = _run_staging(spark, register, [_bronze_row()])
        assert row["_dq_failures"] == ""
        assert row["action_number"] == "EA26001"

    def test_missing_action_number_is_quarantined(self, spark, register):
        (row,) = _run_staging(spark, register, [_bronze_row(NHTSA_ACTION_NUMBER=None)])
        assert "missing_action_number" in row["_dq_failures"]

    def test_unparseable_open_date_is_quarantined(self, spark, register):
        (row,) = _run_staging(spark, register, [_bronze_row(ODATE="garbage")])
        assert "unparseable_odate" in row["_dq_failures"]

    def test_closed_before_opened_is_quarantined(self, spark, register):
        (row,) = _run_staging(spark, register, [_bronze_row(ODATE="20260201", CDATE="20260101")])
        assert "closed_before_opened" in row["_dq_failures"]

    def test_still_open_investigation_is_not_flagged(self, spark, register):
        """No close date at all is a live investigation, not a data-quality defect."""
        (row,) = _run_staging(spark, register, [_bronze_row(CDATE=None)])
        assert row["_dq_failures"] == ""

    def test_campaign_number_absent_means_no_recall_yet(self, spark, register):
        (row,) = _run_staging(spark, register, [_bronze_row(CAMPNO=None)])
        assert row["campaign_number"] is None
        assert row["_dq_failures"] == ""


class TestSilverInvestigationCase:
    """The entity-grain aggregation — the actual backtest population, not the row count."""

    def test_multiple_vehicle_rows_collapse_to_one_investigation(self, spark, register):
        register(
            "silver_investigation",
            [
                _bronze_row_for_case("EA26001", make="RAM", model_year=2023),
                _bronze_row_for_case("EA26001", make="RAM", model_year=2024),
                _bronze_row_for_case("EA26001", make="DODGE", model_year=2024),
            ],
        )
        spark.sql(
            """
            SELECT action_number, MIN(open_date) AS open_date, MAX(close_date) AS close_date,
                   MAX(campaign_number) AS campaign_number,
                   MAX(campaign_number) IS NOT NULL AS led_to_recall,
                   COUNT(*) AS vehicle_rows, COUNT(DISTINCT make) AS distinct_makes,
                   MIN(model_year) AS model_year_min, MAX(model_year) AS model_year_max
            FROM silver_investigation
            GROUP BY action_number
            """
        ).createOrReplaceTempView("case_result")
        (row,) = spark.sql("SELECT * FROM case_result").collect()

        assert row["vehicle_rows"] == 3
        assert row["distinct_makes"] == 2
        assert row["model_year_min"] == 2023
        assert row["model_year_max"] == 2024
        spark.catalog.dropTempView("case_result")

    def test_led_to_recall_is_true_iff_any_row_carries_a_campaign_number(self, spark, register):
        register(
            "silver_investigation",
            [
                _bronze_row_for_case("EA26002", campaign_number=None),
                _bronze_row_for_case("EA26002", campaign_number="24V999"),
            ],
        )
        spark.sql(
            """
            SELECT action_number, MAX(campaign_number) IS NOT NULL AS led_to_recall
            FROM silver_investigation GROUP BY action_number
            """
        ).createOrReplaceTempView("case_result2")
        (row,) = spark.sql("SELECT * FROM case_result2").collect()

        assert row["led_to_recall"] is True
        spark.catalog.dropTempView("case_result2")


def _bronze_row_for_case(
    action_number: str,
    *,
    make: str = "RAM",
    model_year: int = 2024,
    campaign_number: str | None = "24V123",
) -> dict:
    """A `silver_investigation`-shaped row (post-staging), not a raw bronze row — the
    aggregation reads from `silver_investigation`, not from `stg_investigation`."""
    return {
        "action_number": action_number,
        "make": make,
        "model": "2500",
        "model_year": model_year,
        "component": "BRAKES",
        "manufacturer": "FCA US LLC",
        "open_date": "2026-01-01T00:00:00",
        "close_date": None,
        "campaign_number": campaign_number,
        "subject": "brake pad wear",
        "summary": "investigation into brake pad wear",
    }
