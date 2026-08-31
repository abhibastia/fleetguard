# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — baseline lead-time backtest
# MAGIC
# MAGIC Measures the interval the project actually claims: **complaint-pattern detection →
# MAGIC ODI investigation open date**. This is interval (1) of the three in proposal §3, and
# MAGIC the only one that is a FleetGuard result. It is *not* the 118-day
# MAGIC investigation→recall figure, which is regulatory latency.
# MAGIC
# MAGIC **This is the volume-anomaly half of Model A only** — no embeddings, no HDBSCAN.
# MAGIC §4.3 defines Model A as volume anomaly *combined with* semantic clustering, so this
# MAGIC is a floor, not the final number. It exists now because it needs nothing that isn't
# MAGIC already built, and it answers the question the whole project rests on.
# MAGIC
# MAGIC **No leakage:** the detector only ever reads complaints received strictly before the
# MAGIC month being scored, and a detection only counts if it fires before the open date.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

# Detector parameters. Deliberately conservative — the point is a defensible floor.
MIN_COUNT = 5  # absolute complaints in the month; stops 0->2 firing as an anomaly
MIN_Z = 3.0  # standard deviations above the trailing baseline
MIN_BASE_MONTHS = 6  # trailing months required before the detector is allowed to fire
LOOKBACK_MONTHS = 24  # how far before an open date a detection may count

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Monthly complaint series per (make, model, top-level component)
# MAGIC
# MAGIC Component is truncated to its top-level segment. Investigations use deep paths
# MAGIC (`AIR BAGS:FRONTAL:DRIVER SIDE:INFLATOR MODULE`) where complaints commonly use the
# MAGIC root (`AIR BAGS`); 97.6% of investigation components exist verbatim in the complaint
# MAGIC vocabulary, so the taxonomy is shared and only the depth differs.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW complaint_series AS
SELECT
  make,
  model,
  SPLIT(component, ':')[0]              AS comp_top,
  DATE_TRUNC('MONTH', received_date)    AS month,
  COUNT(*)                              AS n
FROM silver_complaint
WHERE make IS NOT NULL AND component IS NOT NULL AND received_date IS NOT NULL
GROUP BY 1, 2, 3, 4
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Anomaly flags
# MAGIC
# MAGIC Trailing 12-month mean and standard deviation, evaluated over months strictly
# MAGIC *before* the month being scored (`ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING`).
# MAGIC
# MAGIC *Known limitation:* months with zero complaints do not appear as rows, so the
# MAGIC trailing baseline is computed over non-zero months only and is biased slightly high.
# MAGIC That makes the detector more conservative, not less — it under-detects rather than
# MAGIC over-detects, which is the right direction for a floor.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW complaint_anomaly AS
SELECT
  make, model, comp_top, month, n,
  base_mean, base_sd, base_months,
  CASE WHEN base_sd IS NULL OR base_sd = 0 THEN NULL
       ELSE (n - base_mean) / base_sd END AS z,
  (
    n >= {MIN_COUNT}
    AND base_months >= {MIN_BASE_MONTHS}
    AND base_sd IS NOT NULL AND base_sd > 0
    AND (n - base_mean) / base_sd >= {MIN_Z}
  ) AS fired
FROM (
  SELECT
    make, model, comp_top, month, n,
    AVG(n)         OVER w AS base_mean,
    STDDEV_POP(n)  OVER w AS base_sd,
    COUNT(*)       OVER w AS base_months
  FROM complaint_series
  WINDOW w AS (
    PARTITION BY make, model, comp_top
    ORDER BY month
    ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING
  )
)
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Investigation targets (post-2010) and their matching keys

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW inv_target AS
SELECT DISTINCT
  i.action_number,
  c.open_date,
  c.led_to_recall,
  i.make,
  i.model,
  SPLIT(i.component, ':')[0] AS comp_top
FROM silver_investigation i
JOIN silver_investigation_case c USING (action_number)
WHERE YEAR(c.open_date) >= 2010
  AND i.make IS NOT NULL AND i.component IS NOT NULL
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Earliest qualifying detection per investigation
# MAGIC
# MAGIC A detection counts only if it fires strictly before the open date and within the
# MAGIC lookback window. Taking the earliest such firing maximises measured lead time, so
# MAGIC the result is reported alongside the more conservative latest-firing view.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE gold_lead_time_backtest
COMMENT 'Baseline lead-time backtest: volume-anomaly detection date vs ODI investigation open date. Volume-anomaly half of Model A only — no embeddings. A floor, not the final figure.'
AS
WITH hits AS (
  SELECT
    t.action_number,
    t.open_date,
    t.led_to_recall,
    MIN(a.month)  AS first_detection,
    MAX(a.month)  AS last_detection,
    COUNT(*)      AS detection_months,
    MAX(a.z)      AS max_z
  FROM inv_target t
  JOIN complaint_anomaly a
    ON  a.make     = t.make
    AND a.model    = t.model
    AND a.comp_top = t.comp_top
    AND a.fired
    AND a.month <  t.open_date
    AND a.month >= ADD_MONTHS(t.open_date, -{LOOKBACK_MONTHS})
  GROUP BY 1, 2, 3
)
SELECT
  c.action_number,
  c.open_date,
  c.led_to_recall,
  h.first_detection,
  h.last_detection,
  h.detection_months,
  h.max_z,
  DATEDIFF(c.open_date, h.first_detection) AS lead_days_earliest,
  DATEDIFF(c.open_date, h.last_detection)  AS lead_days_latest,
  (h.action_number IS NOT NULL)            AS detected
FROM (SELECT DISTINCT action_number, open_date, led_to_recall FROM inv_target) c
LEFT JOIN hits h USING (action_number, open_date, led_to_recall)
""")

display(spark.sql("SELECT COUNT(*) AS investigations FROM gold_lead_time_backtest"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Result

# COMMAND ----------

display(
    spark.sql("""
SELECT
  COUNT(*)                                              AS investigations,
  SUM(CASE WHEN detected THEN 1 ELSE 0 END)             AS detected,
  ROUND(100.0 * AVG(CASE WHEN detected THEN 1 ELSE 0 END), 1) AS detection_rate_pct,
  ROUND(AVG(lead_days_earliest))                        AS mean_lead_days,
  PERCENTILE(lead_days_earliest, 0.10)                  AS p10,
  PERCENTILE(lead_days_earliest, 0.25)                  AS p25,
  PERCENTILE(lead_days_earliest, 0.50)                  AS median_lead_days,
  PERCENTILE(lead_days_earliest, 0.75)                  AS p75,
  PERCENTILE(lead_days_earliest, 0.90)                  AS p90,
  MAX(lead_days_earliest)                               AS max_lead_days
FROM gold_lead_time_backtest
""")
)

# COMMAND ----------

display(
    spark.sql("""
SELECT
  led_to_recall,
  COUNT(*)                                   AS investigations,
  SUM(CASE WHEN detected THEN 1 ELSE 0 END)  AS detected,
  PERCENTILE(lead_days_earliest, 0.50)       AS median_lead_days,
  PERCENTILE(lead_days_latest,   0.50)       AS median_lead_days_conservative
FROM gold_lead_time_backtest
GROUP BY led_to_recall
""")
)
