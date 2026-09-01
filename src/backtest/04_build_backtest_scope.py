# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 9: materialise the backtest scope
# MAGIC
# MAGIC The v2 backtest built its real and placebo arms as temp views inside one notebook.
# MAGIC The semantic (HDBSCAN) arm needs the **same** population, and re-deriving it in a
# MAGIC second notebook would let the two drift — at which point any difference between the
# MAGIC volume and semantic results could be a population artefact rather than a real effect.
# MAGIC So the scope is materialised once, here, and every downstream step reads it.
# MAGIC
# MAGIC **The comparison this sets up.** v3 changes exactly one thing versus v2: the grouping
# MAGIC key. v2 detects a sustained volume anomaly per `(make, model, comp_top)`; v3 detects
# MAGIC one per `(make, model, semantic_cluster)`. Same detector, same window, same placebo,
# MAGIC same investigations. Any lift is attributable to the clustering and nothing else.
# MAGIC
# MAGIC Placebo construction is **volume-matched**, copied verbatim from v2 — an unmatched
# MAGIC placebo fills with series too small to trip `MIN_COUNT` and manufactured an apparent
# MAGIC 160× separation (I-027).

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

LOOKBACK_MONTHS = 24  # detection window: how far before open_date a run may start

# The working set must extend FURTHER BACK than the detection window. The detector's
# baseline is `ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING` with `base_months >= 6`, so a
# candidate month at the far edge of the lookback still needs 12 months of history behind
# it. v2 reads all of silver_complaint and gets that for free. If the semantic arm embedded
# only the 24-month window, its earliest months would have no baseline, could never reach
# `base_months >= 6`, and so could never fire — silently deleting exactly the long-lead
# detections this project exists to find, and making v3 look worse for a reason that has
# nothing to do with semantics.
EMBED_MONTHS = 37  # 24 lookback + 12 baseline + 1
MIN_NARRATIVE_CHARS = 20  # matches fleetguard.chunking.MIN_NARRATIVE_CHARS

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW complaint_series AS
SELECT make, model, SPLIT(component, ':')[0] AS comp_top,
       DATE_TRUNC('MONTH', received_date) AS month, COUNT(*) AS n
FROM silver_complaint
WHERE make IS NOT NULL AND component IS NOT NULL AND received_date IS NOT NULL
GROUP BY 1, 2, 3, 4
""")

spark.sql("""
CREATE OR REPLACE TEMP VIEW inv_target AS
SELECT DISTINCT i.action_number, c.open_date, c.led_to_recall,
       i.make, i.model, SPLIT(i.component, ':')[0] AS comp_top
FROM silver_investigation i
JOIN silver_investigation_case c USING (action_number)
WHERE YEAR(c.open_date) >= 2010 AND i.make IS NOT NULL AND i.component IS NOT NULL
""")

spark.sql("""
CREATE OR REPLACE TEMP VIEW series_volume AS
SELECT make, model, comp_top, SUM(n) AS total_complaints,
       FLOOR(LOG10(GREATEST(SUM(n), 1)) * 2) AS vol_bucket
FROM complaint_series
GROUP BY make, model, comp_top
""")

# Volume-matched placebo — verbatim from v2 (see I-027 for why matching is mandatory).
spark.sql("""
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

# COMMAND ----------

# MAGIC %md
# MAGIC ## The scope table
# MAGIC
# MAGIC One row per (arm, investigation, series). The placebo arm reuses the real arm's
# MAGIC `action_number` and `open_date` deliberately — it is the same investigation's clock
# MAGIC applied to a series that never had one, which is what makes the arms comparable.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_backtest_scope
COMMENT 'Backtest population for Phase 9. One row per (arm, investigation, series). REAL = the investigated series; PLACEBO = a volume-matched never-investigated series carrying the same open_date. Shared by the volume (v2) and semantic (v3) arms so the two cannot drift.'
AS
SELECT 'REAL' AS arm, action_number, open_date, led_to_recall, make, model, comp_top
FROM inv_target
UNION ALL
SELECT 'PLACEBO', p.action_number, p.open_date, NULL, p.make, p.model, p.comp_top
FROM placebo_target p
""")

display(
    spark.sql("""
    SELECT arm, COUNT(*) AS series_rows, COUNT(DISTINCT action_number) AS investigations
    FROM gold_backtest_scope GROUP BY arm ORDER BY arm
""")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## The complaint working set
# MAGIC
# MAGIC Every complaint falling inside any scope row's lookback window. A complaint can serve
# MAGIC several investigations, so this is deduplicated to one row per `complaint_id` — that
# MAGIC is the unit that gets embedded, and embedding the same narrative twice would be
# MAGIC wasted spend.
# MAGIC
# MAGIC Narratives shorter than 20 characters are excluded: they carry no retrievable signal
# MAGIC and would land as noise in the clustering.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE gold_backtest_complaint
COMMENT 'Deduplicated complaints inside any backtest lookback window. One row per complaint_id — the unit of embedding for the semantic arm.'
CLUSTER BY (make, model)
AS
SELECT DISTINCT
       s.complaint_id, s.make, s.model, SPLIT(s.component, ':')[0] AS comp_top,
       s.component, s.received_date, s.narrative, s.narrative_length,
       s.crash, s.fire, s.injured, s.deaths
FROM silver_complaint s
JOIN gold_backtest_scope k
  ON k.make = s.make AND k.model = s.model
 AND k.comp_top = SPLIT(s.component, ':')[0]
WHERE s.received_date <  k.open_date
  AND s.received_date >= ADD_MONTHS(k.open_date, -{EMBED_MONTHS})
  AND s.narrative IS NOT NULL
  AND LENGTH(TRIM(s.narrative)) >= {MIN_NARRATIVE_CHARS}
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify
# MAGIC
# MAGIC The real arm must still be the **777 post-2010 investigations** the v2 result was
# MAGIC measured on. If that number moves, the new result is not comparable with the old one
# MAGIC and the whole point of a shared scope is lost.

# COMMAND ----------

checks = spark.sql("""
SELECT
  (SELECT COUNT(DISTINCT action_number) FROM gold_backtest_scope WHERE arm='REAL')    AS real_investigations,
  (SELECT COUNT(DISTINCT action_number) FROM gold_backtest_scope WHERE arm='PLACEBO') AS placebo_investigations,
  (SELECT COUNT(*) FROM gold_backtest_complaint)                                      AS complaints_to_embed,
  (SELECT COUNT(DISTINCT complaint_id) FROM gold_backtest_complaint)                  AS distinct_complaints,
  (SELECT ROUND(AVG(narrative_length)) FROM gold_backtest_complaint)                  AS mean_narrative_chars,
  (SELECT SUM(narrative_length) FROM gold_backtest_complaint)                         AS total_chars
""").collect()[0]

for k, v in checks.asDict().items():
    print(f"  {k:<26} {v:,}" if isinstance(v, int | float) else f"  {k:<26} {v}")

assert checks["real_investigations"] == 777, (
    f"real arm has {checks['real_investigations']} investigations, expected 777 — "
    "the backtest population changed and results are no longer comparable with v2"
)
assert checks["complaints_to_embed"] == checks["distinct_complaints"], (
    "gold_backtest_complaint is not one row per complaint_id — embedding spend would double"
)

# ~4 chars/token is the usual rule of thumb for English; this is an estimate, not a quote.
est_tokens = checks["total_chars"] / 4
print(
    f"\n  estimated embedding tokens: {est_tokens:,.0f}  (~${est_tokens / 1e6 * 0.13:,.2f} at gte-large rates)"
)
print("  NOTE: token estimate is 4 chars/token heuristic — treat as an order of magnitude.")
