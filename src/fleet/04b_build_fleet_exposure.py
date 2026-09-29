# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 2b: fleet recall exposure (`gold_fleet_exposure`)
# MAGIC
# MAGIC **This table previously had no source file.** It exists live in `abhi`
# MAGIC (989,042 rows, matching the proposal's measured figures) but was built ad hoc and
# MAGIC never committed — see `docs/ISSUES.md` I-059. Reconstructed here from the live
# MAGIC schema and the matching rule I-059 recovered by inspection: `match_basis` is `EXACT`
# MAGIC when `model = recall_model` after make and year (manufacture-window) agreement,
# MAGIC `MODEL_VARIANT` otherwise. This is a genuine reconstruction, not a byte-for-byte
# MAGIC replica of the original ad hoc SQL — treat row counts as approximate, not a fixed
# MAGIC target to match exactly.
# MAGIC
# MAGIC **Tiering matches the rest of the codebase by construction, not by convention.**
# MAGIC The EXACT/MODEL_VARIANT predicate below is the same word-boundary variant test
# MAGIC (`... LIKE ... || ' %'`, bidirectional) already used in
# MAGIC `src/backtest/10_emerging_signals.py` and `src/search/27_build_chunk_index_source.py`
# MAGIC — NHTSA and vPIC disagree on spelling for the same vehicle (`F-250` vs `F-250 SD`,
# MAGIC `PROMASTER` vs `PROMASTER 1500`), and every previous place in this project that used
# MAGIC exact-only matching had to be fixed later (I-030, I-075, I-079, I-111, I-115) once
# MAGIC that was discovered the hard way. Do not rewrite this predicate independently.
# MAGIC
# MAGIC **"Manufacture-window agreement"** is implemented as `vehicle.model_year =
# MAGIC recall.model_year` — the only year `gold_fleet_vehicle` carries. Where a campaign's
# MAGIC `manufacture_start`/`manufacture_end` are populated, the vehicle's model year must
# MAGIC also fall inside that window; where the window is null, year equality alone governs.
# MAGIC Needs `silver_recall` (bronze/silver pipeline) and `gold_fleet_vehicle`
# MAGIC (`04_build_fleet_registry.py`) to exist first.

# COMMAND ----------

dbutils.widgets.text("catalog", "bootcamp_students")
dbutils.widgets.text("schema", "fleetguard")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
spark.sql(f"USE {CATALOG}.{SCHEMA}")

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_fleet_exposure
CLUSTER BY (make, model_year)
COMMENT 'Fleet vehicle x recall campaign exposure. One row per (vin, campaign_number) pair
where make, year (manufacture-window), and model (EXACT or MODEL_VARIANT) agree. Nested
tiers, not disjoint: EXACT wins when it exists, mirroring gold_emerging_signal and
silver_complaint_chunk_indexed. See docs/ISSUES.md I-059 -- reconstructed from the live
schema, no original source file existed.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS
WITH candidates AS (
  SELECT
    v.vin, v.depot_id, v.make, v.model, v.model_year, v.segment,
    r.campaign_number, r.model AS recall_model, r.component,
    r.manufacturer, r.recall_type, r.do_not_drive, r.park_it,
    r.report_received_date, r.manufacture_start, r.manufacture_end,
    CASE WHEN r.model = v.model THEN 'EXACT' ELSE 'MODEL_VARIANT' END AS match_basis
  FROM gold_fleet_vehicle v
  JOIN silver_recall r
    ON r.make = v.make
   AND v.model_year = r.model_year
   AND (r.manufacture_start IS NULL OR r.manufacture_end IS NULL
        OR MAKE_DATE(v.model_year, 1, 1) BETWEEN
             DATE_TRUNC('YEAR', r.manufacture_start) AND DATE_TRUNC('YEAR', r.manufacture_end))
   AND (r.model = v.model
        OR r.model LIKE v.model || ' %'
        OR v.model LIKE r.model || ' %')
)
SELECT vin, depot_id, make, model, model_year, segment,
       campaign_number, recall_model, component, manufacturer, recall_type,
       do_not_drive, park_it, report_received_date, match_basis,
       current_timestamp() AS matched_at
FROM candidates
""")

spark.sql("""
COMMENT ON TABLE gold_fleet_exposure IS
'Fleet vehicle x recall campaign exposure, tiered EXACT/MODEL_VARIANT. Reconstructed
2026-09-25 (I-059) -- no original source file existed for this table.'
""")

# COMMAND ----------

n = spark.table("gold_fleet_exposure").count()
tiers = {
    r["match_basis"]: r["n"]
    for r in spark.sql(
        "SELECT match_basis, COUNT(*) AS n FROM gold_fleet_exposure GROUP BY match_basis"
    ).collect()
}
combos = spark.sql(
    "SELECT COUNT(DISTINCT make, model, model_year) AS n FROM gold_fleet_exposure"
).collect()[0]["n"]
print(f"gold_fleet_exposure: {n:,} rows, {combos} distinct fleet make/model/year combos")
print(f"  EXACT:         {tiers.get('EXACT', 0):,}")
print(f"  MODEL_VARIANT: {tiers.get('MODEL_VARIANT', 0):,}")
