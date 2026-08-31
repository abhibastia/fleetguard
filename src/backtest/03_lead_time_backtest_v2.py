# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — lead-time backtest v2 (sustained runs + placebo control)
# MAGIC
# MAGIC v1 reported a 409-day median and it was an artifact. Taking the *earliest* anomaly
# MAGIC in a 24-month window produced a nearly flat lead-time distribution with as many
# MAGIC detections at the window edge (630–719 days) as near the open date (0–89), and the
# MAGIC earliest-vs-latest medians differed 4.6× (409 vs 89). That is the signature of a
# MAGIC detector firing sporadically on background variance, not of one tracking a defect ramp.
# MAGIC
# MAGIC v2 changes two things:
# MAGIC
# MAGIC 1. **Sustained runs.** A detection is a run of >= `MIN_RUN` consecutive firing
# MAGIC    months. Detection date is the *onset of the run nearest the open date* — the
# MAGIC    ramp that was still building when the regulator acted, not an unrelated spike
# MAGIC    two years earlier.
# MAGIC 2. **Placebo control.** The same detector is evaluated against (make, model,
# MAGIC    component) series that never had an investigation, at matched dates. If the
# MAGIC    placebo detection rate matches the real one, the result is noise and must be
# MAGIC    reported as such.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

MIN_COUNT = 5
MIN_Z = 3.0
MIN_BASE_MONTHS = 6
LOOKBACK_MONTHS = 24
MIN_RUN = 2  # consecutive firing months required to count as a detection

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW complaint_series AS
SELECT make, model, SPLIT(component, ':')[0] AS comp_top,
       DATE_TRUNC('MONTH', received_date) AS month, COUNT(*) AS n
FROM silver_complaint
WHERE make IS NOT NULL AND component IS NOT NULL AND received_date IS NOT NULL
GROUP BY 1, 2, 3, 4
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW complaint_anomaly AS
SELECT make, model, comp_top, month, n, base_mean, base_sd,
       (n - base_mean) / NULLIF(base_sd, 0) AS z,
       ( n >= {MIN_COUNT} AND base_months >= {MIN_BASE_MONTHS}
         AND base_sd > 0 AND (n - base_mean) / base_sd >= {MIN_Z} ) AS fired
FROM (
  SELECT make, model, comp_top, month, n,
         AVG(n) OVER w AS base_mean, STDDEV_POP(n) OVER w AS base_sd,
         COUNT(*) OVER w AS base_months
  FROM complaint_series
  WINDOW w AS (PARTITION BY make, model, comp_top ORDER BY month
               ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING)
)
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Consecutive firing months grouped into runs
# MAGIC
# MAGIC Standard gaps-and-islands: for firing months only, `month_index - row_number()` is
# MAGIC constant within a consecutive run.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW anomaly_run AS
SELECT make, model, comp_top,
       MIN(month)  AS run_start,
       MAX(month)  AS run_end,
       COUNT(*)    AS run_len,
       MAX(z)      AS run_max_z
FROM (
  SELECT make, model, comp_top, month, z,
         CAST(MONTHS_BETWEEN(month, DATE'1970-01-01') AS INT)
           - ROW_NUMBER() OVER (PARTITION BY make, model, comp_top ORDER BY month) AS grp
  FROM complaint_anomaly
  WHERE fired
)
GROUP BY make, model, comp_top, grp
HAVING COUNT(*) >= {MIN_RUN}
""")

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW inv_target AS
SELECT DISTINCT i.action_number, c.open_date, c.led_to_recall,
       i.make, i.model, SPLIT(i.component, ':')[0] AS comp_top
FROM silver_investigation i
JOIN silver_investigation_case c USING (action_number)
WHERE YEAR(c.open_date) >= 2010 AND i.make IS NOT NULL AND i.component IS NOT NULL
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Real detections — run nearest the open date

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW real_hits AS
SELECT action_number, open_date, led_to_recall, run_start, run_end, run_len, run_max_z,
       DATEDIFF(open_date, run_start) AS lead_days
FROM (
  SELECT t.action_number, t.open_date, t.led_to_recall,
         r.run_start, r.run_end, r.run_len, r.run_max_z,
         ROW_NUMBER() OVER (PARTITION BY t.action_number ORDER BY r.run_end DESC) AS rn
  FROM inv_target t
  JOIN anomaly_run r
    ON r.make = t.make AND r.model = t.model AND r.comp_top = t.comp_top
   AND r.run_start <  t.open_date
   AND r.run_start >= ADD_MONTHS(t.open_date, -{LOOKBACK_MONTHS})
)
WHERE rn = 1
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Placebo control
# MAGIC
# MAGIC Each investigation is paired with a (make, model, comp_top) series that never had an
# MAGIC investigation, keeping the same open date. Same detector, same window. Any detection
# MAGIC here is a false positive by construction.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW series_volume AS
SELECT make, model, comp_top, SUM(n) AS total_complaints,
       FLOOR(LOG10(GREATEST(SUM(n), 1)) * 2) AS vol_bucket
FROM complaint_series
GROUP BY make, model, comp_top
""")

# VOLUME-MATCHED placebo. An unmatched placebo is not a fair control: investigated series
# carry a median of 32 complaints against 2 for never-investigated ones, so the control arm
# fills with series too small to ever trip MIN_COUNT. That inflated the first result to an
# apparent 160x separation. Each investigated series is paired with a never-investigated
# series in the SAME half-decade volume bucket, carrying the same open date.
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW placebo_target AS
WITH investigated AS (
  SELECT DISTINCT make, model, SPLIT(component, ':')[0] AS comp_top FROM silver_investigation
),
clean AS (
  SELECT v.*,
         ROW_NUMBER() OVER (PARTITION BY v.vol_bucket ORDER BY v.make, v.model, v.comp_top) AS rn,
         COUNT(*)    OVER (PARTITION BY v.vol_bucket) AS bucket_size
  FROM series_volume v
  LEFT ANTI JOIN investigated i
    ON i.make = v.make AND i.model = v.model AND i.comp_top = v.comp_top
),
inv_with_bucket AS (
  SELECT t.action_number, t.open_date, v.vol_bucket,
         ROW_NUMBER() OVER (PARTITION BY v.vol_bucket ORDER BY t.action_number) AS rn
  FROM (SELECT DISTINCT action_number, open_date, make, model, comp_top FROM inv_target) t
  JOIN series_volume v
    ON v.make = t.make AND v.model = t.model AND v.comp_top = t.comp_top
)
SELECT i.action_number, i.open_date, c.make, c.model, c.comp_top, i.vol_bucket
FROM inv_with_bucket i
JOIN clean c
  ON c.vol_bucket = i.vol_bucket
 AND c.rn = MOD(i.rn * 7919, c.bucket_size) + 1
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW placebo_hits AS
SELECT action_number, open_date, run_start, DATEDIFF(open_date, run_start) AS lead_days
FROM (
  SELECT p.action_number, p.open_date, r.run_start,
         ROW_NUMBER() OVER (PARTITION BY p.action_number ORDER BY r.run_end DESC) AS rn
  FROM placebo_target p
  JOIN anomaly_run r
    ON r.make = p.make AND r.model = p.model AND r.comp_top = p.comp_top
   AND r.run_start <  p.open_date
   AND r.run_start >= ADD_MONTHS(p.open_date, -{LOOKBACK_MONTHS})
)
WHERE rn = 1
""")

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_lead_time_backtest
COMMENT 'Lead-time backtest v2: sustained-run volume-anomaly detection vs ODI investigation open date, with placebo control. Volume-anomaly half of Model A only — no embeddings.'
AS
SELECT c.action_number, c.open_date, c.led_to_recall,
       h.run_start AS detection_date, h.run_end, h.run_len, h.run_max_z, h.lead_days,
       (h.action_number IS NOT NULL) AS detected
FROM (SELECT DISTINCT action_number, open_date, led_to_recall FROM inv_target) c
LEFT JOIN real_hits h USING (action_number, open_date, led_to_recall)
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Result — real vs placebo

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_lead_time_control
COMMENT 'Placebo arm: same detector, same window, on (make, model, component) series that never had an investigation. Any detection here is a false positive by construction.'
AS SELECT * FROM placebo_hits
""")

spark.sql("""
CREATE OR REPLACE TABLE gold_lead_time_summary
COMMENT 'Real vs placebo comparison. The GAP between arms is the evidence; the real arm alone is not.'
AS
SELECT 'REAL (investigated series)' AS arm,
       (SELECT COUNT(*) FROM gold_lead_time_backtest) AS n,
       (SELECT SUM(CASE WHEN detected THEN 1 ELSE 0 END) FROM gold_lead_time_backtest) AS detected,
       (SELECT ROUND(100.0*AVG(CASE WHEN detected THEN 1 ELSE 0 END),1) FROM gold_lead_time_backtest) AS detect_rate_pct,
       (SELECT PERCENTILE(lead_days,0.50) FROM gold_lead_time_backtest) AS median_lead_days
UNION ALL
SELECT 'PLACEBO (never investigated)',
       (SELECT COUNT(DISTINCT action_number) FROM placebo_target),
       (SELECT COUNT(*) FROM gold_lead_time_control),
       (SELECT ROUND(100.0*COUNT(*)/(SELECT COUNT(DISTINCT action_number) FROM placebo_target),1) FROM gold_lead_time_control),
       (SELECT PERCENTILE(lead_days,0.50) FROM gold_lead_time_control)
""")

display(spark.sql("SELECT * FROM gold_lead_time_summary"))

display(
    spark.sql("""
SELECT 'REAL (investigated series)' AS arm,
       COUNT(*) AS n,
       SUM(CASE WHEN detected THEN 1 ELSE 0 END) AS detected,
       ROUND(100.0*AVG(CASE WHEN detected THEN 1 ELSE 0 END),1) AS detect_rate_pct,
       PERCENTILE(lead_days,0.25) AS p25,
       PERCENTILE(lead_days,0.50) AS median_lead_days,
       PERCENTILE(lead_days,0.75) AS p75
FROM gold_lead_time_backtest
UNION ALL
SELECT 'PLACEBO (never investigated)',
       (SELECT COUNT(DISTINCT action_number) FROM placebo_target),
       COUNT(*),
       ROUND(100.0*COUNT(*)/(SELECT COUNT(DISTINCT action_number) FROM placebo_target),1),
       PERCENTILE(lead_days,0.25),
       PERCENTILE(lead_days,0.50),
       PERCENTILE(lead_days,0.75)
FROM placebo_hits
""")
)
