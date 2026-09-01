# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — lead-time backtest v3 (semantic subdivision)
# MAGIC
# MAGIC **v3 changes exactly one thing versus v2: the detector's grouping key.**
# MAGIC
# MAGIC | | grouping | measured |
# MAGIC |---|---|---|
# MAGIC | v2 | `(make, model, comp_top)` | 16.0% real vs 11.1% placebo — 1.44× |
# MAGIC | v3 | `(make, model, comp_top, semantic_sub)` | this notebook |
# MAGIC
# MAGIC Same sustained-run detector, same thresholds, same 24-month window, same volume-matched
# MAGIC placebo, same 777 investigations. Any difference is attributable to the subdivision and
# MAGIC nothing else. Every threshold below is copied from v2 unchanged — **do not tune them
# MAGIC here**, or the comparison stops meaning anything.
# MAGIC
# MAGIC ## Both arms are recomputed on the same data
# MAGIC
# MAGIC v3 runs on `gold_backtest_embedding` (the 37-month embedded set), not on all of
# MAGIC `silver_complaint`. So the v2 numbers are **recomputed here on that same set** rather
# MAGIC than quoted from the published run. A published-vs-recomputed comparison would confound
# MAGIC the grouping change with a change of data extent. The recomputed v2 figure is also
# MAGIC reported against the published 16.0% as a sanity check.
# MAGIC
# MAGIC ## Why the working set is 37 months
# MAGIC
# MAGIC The detector's baseline is the 12 months preceding each candidate month
# MAGIC (`base_months >= 6` to fire). A 24-month working set would leave the earliest candidate
# MAGIC months with no baseline — they could never fire, silently deleting the long-lead
# MAGIC detections that matter most. 37 = 24 lookback + 12 baseline + 1.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

# All five copied verbatim from 03_lead_time_backtest_v2.py.
MIN_COUNT = 5
MIN_Z = 3.0
MIN_BASE_MONTHS = 6
LOOKBACK_MONTHS = 24
MIN_RUN = 2

# COMMAND ----------

# MAGIC %md
# MAGIC ## Monthly series under both groupings
# MAGIC
# MAGIC One view, two keys, so the arms cannot diverge through a copy-paste error.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW base_complaint AS
SELECT e.complaint_id, e.make, e.model, e.comp_top,
       s.series_key_v2, s.series_key_v3,
       DATE_TRUNC('MONTH', e.received_date) AS month
FROM gold_backtest_embedding e
JOIN gold_backtest_subcluster s USING (complaint_id)
WHERE e.embedding IS NOT NULL
""")


def build_detector(key_col: str, suffix: str):
    """Materialise anomaly runs for one grouping key. Identical logic for both arms."""
    spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW series_{suffix} AS
    SELECT {key_col} AS series_key, month, COUNT(*) AS n
    FROM base_complaint GROUP BY {key_col}, month
    """)

    spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW anomaly_{suffix} AS
    SELECT series_key, month, n,
           ( n >= {MIN_COUNT} AND base_months >= {MIN_BASE_MONTHS}
             AND base_sd > 0 AND (n - base_mean) / base_sd >= {MIN_Z} ) AS fired,
           (n - base_mean) / NULLIF(base_sd, 0) AS z
    FROM (
      SELECT series_key, month, n,
             AVG(n) OVER w AS base_mean, STDDEV_POP(n) OVER w AS base_sd,
             COUNT(*) OVER w AS base_months
      FROM series_{suffix}
      WINDOW w AS (PARTITION BY series_key ORDER BY month
                   ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING)
    )
    """)

    # Gaps-and-islands: month_index - row_number() is constant within a consecutive run.
    spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW run_{suffix} AS
    SELECT series_key, MIN(month) AS run_start, MAX(month) AS run_end,
           COUNT(*) AS run_len, MAX(z) AS run_max_z
    FROM (
      SELECT series_key, month, z,
             CAST(MONTHS_BETWEEN(month, DATE'1970-01-01') AS INT)
               - ROW_NUMBER() OVER (PARTITION BY series_key ORDER BY month) AS grp
      FROM anomaly_{suffix} WHERE fired
    )
    GROUP BY series_key, grp
    HAVING COUNT(*) >= {MIN_RUN}
    """)


build_detector("series_key_v2", "v2")
build_detector("series_key_v3", "v3")
print("detectors built for both groupings")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Map each investigation to the series it could have been detected on
# MAGIC
# MAGIC A v2 series maps to one investigation; a v3 series is a subdivision of it, so an
# MAGIC investigation may now be reachable through several sub-series. **The detection date is
# MAGIC the run nearest the open date across all of them** — the same "nearest run" rule v2
# MAGIC used, applied to a larger candidate set. Taking the *earliest* run instead is what
# MAGIC produced v1's discarded 409-day artefact (I-027).

# COMMAND ----------


def build_hits(suffix: str, key_expr: str):
    spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW scope_keys_{suffix} AS
    SELECT DISTINCT k.arm, k.action_number, k.open_date, {key_expr} AS series_key
    FROM gold_backtest_scope k
    JOIN gold_backtest_subcluster s
      ON s.make = k.make AND s.model = k.model AND s.comp_top = k.comp_top
    """)

    spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW hits_{suffix} AS
    SELECT arm, action_number, open_date, run_start, run_end, run_len, run_max_z,
           DATEDIFF(open_date, run_start) AS lead_days
    FROM (
      SELECT t.arm, t.action_number, t.open_date,
             r.run_start, r.run_end, r.run_len, r.run_max_z,
             ROW_NUMBER() OVER (PARTITION BY t.arm, t.action_number
                                ORDER BY r.run_end DESC) AS rn
      FROM scope_keys_{suffix} t
      JOIN run_{suffix} r ON r.series_key = t.series_key
      WHERE r.run_start <  t.open_date
        AND r.run_start >= ADD_MONTHS(t.open_date, -{LOOKBACK_MONTHS})
    )
    WHERE rn = 1
    """)


build_hits("v2", "s.series_key_v2")
build_hits("v3", "s.series_key_v3")

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_lead_time_v3
COMMENT 'Lead-time backtest v3. Both groupings evaluated on the identical 37-month embedded working set, so the only difference between them is the detector grouping key.'
AS
WITH pop AS (
  SELECT DISTINCT arm, action_number, open_date FROM gold_backtest_scope
)
SELECT p.arm, p.action_number, p.open_date,
       (h2.action_number IS NOT NULL) AS detected_v2,
       h2.lead_days AS lead_days_v2,
       (h3.action_number IS NOT NULL) AS detected_v3,
       h3.lead_days AS lead_days_v3
FROM pop p
LEFT JOIN hits_v2 h2 ON h2.arm = p.arm AND h2.action_number = p.action_number
LEFT JOIN hits_v3 h3 ON h3.arm = p.arm AND h3.action_number = p.action_number
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Result

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_lead_time_v3_summary
COMMENT 'v2 vs v3 detection and lead time, per arm. The GAP between arms is the evidence; a single arm alone is not.'
AS
SELECT arm, 'v2  (component grouping)' AS grouping, COUNT(*) AS n,
       SUM(CASE WHEN detected_v2 THEN 1 ELSE 0 END) AS detected,
       ROUND(100.0 * AVG(CASE WHEN detected_v2 THEN 1 ELSE 0 END), 1) AS detect_rate_pct,
       PERCENTILE(lead_days_v2, 0.50) AS median_lead_days
FROM gold_lead_time_v3 GROUP BY arm
UNION ALL
SELECT arm, 'v3  (semantic subdivision)', COUNT(*),
       SUM(CASE WHEN detected_v3 THEN 1 ELSE 0 END),
       ROUND(100.0 * AVG(CASE WHEN detected_v3 THEN 1 ELSE 0 END), 1),
       PERCENTILE(lead_days_v3, 0.50)
FROM gold_lead_time_v3 GROUP BY arm
""")

display(spark.sql("SELECT * FROM gold_lead_time_v3_summary ORDER BY grouping, arm"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The number that matters: lift over the placebo
# MAGIC
# MAGIC A detection rate on the real arm alone is not evidence — the placebo arm is what
# MAGIC separates a defect signal from a detector that fires on any busy series.

# COMMAND ----------

display(
    spark.sql("""
    WITH s AS (SELECT * FROM gold_lead_time_v3_summary)
    SELECT r.grouping,
           r.detect_rate_pct AS real_pct,
           p.detect_rate_pct AS placebo_pct,
           ROUND(r.detect_rate_pct / NULLIF(p.detect_rate_pct, 0), 2) AS lift,
           r.median_lead_days AS real_median_lead
    FROM s r JOIN s p ON p.grouping = r.grouping AND p.arm = 'PLACEBO'
    WHERE r.arm = 'REAL' ORDER BY r.grouping
""")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Paired view — where v3 changed the outcome
# MAGIC
# MAGIC Aggregate rates hide whether v3 found *different* investigations or merely the same
# MAGIC ones. On the real arm, `v3_only` is what subdivision bought; `v2_only` is what
# MAGIC fragmentation cost. A wash in the aggregate can still be a large churn underneath, and
# MAGIC that distinction changes what to do next.

# COMMAND ----------

display(
    spark.sql("""
    SELECT arm,
           SUM(CASE WHEN detected_v2 AND detected_v3 THEN 1 ELSE 0 END) AS both,
           SUM(CASE WHEN detected_v2 AND NOT detected_v3 THEN 1 ELSE 0 END) AS v2_only,
           SUM(CASE WHEN NOT detected_v2 AND detected_v3 THEN 1 ELSE 0 END) AS v3_only,
           SUM(CASE WHEN NOT detected_v2 AND NOT detected_v3 THEN 1 ELSE 0 END) AS neither,
           PERCENTILE(CASE WHEN detected_v2 AND detected_v3
                           THEN lead_days_v3 - lead_days_v2 END, 0.50) AS median_lead_gain_days
    FROM gold_lead_time_v3 GROUP BY arm ORDER BY arm
""")
)
