# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — live emerging-defect signals
# MAGIC
# MAGIC The backtest proves the detector works *historically*. This runs the **same rule** over
# MAGIC the current corpus and writes the series that are firing **now**, so the proactive claim
# MAGIC has a live surface rather than only a static evidence page.
# MAGIC
# MAGIC ## What this replaces
# MAGIC
# MAGIC `gold_emerging_cluster` was specified as a *cluster*-grained table fed by HDBSCAN.
# MAGIC HDBSCAN was abandoned (85% noise, I-048) and semantic subdivision was falsified
# MAGIC (I-049), so that table is descoped — not deferred. This is its replacement at the grain
# MAGIC the shipping detector actually uses: `(make, model, comp_top)`.
# MAGIC
# MAGIC ## The rule is copied, not reinvented
# MAGIC
# MAGIC Thresholds and the sustained-run definition are **identical** to
# MAGIC `09_lead_time_backtest_v3` (v2 arm). If they diverged, the measured 16.0% / 11.1% would
# MAGIC no longer describe the thing in production, and the evidence page would be quoting a
# MAGIC different system than the one on screen.
# MAGIC
# MAGIC ## Harm is descriptive here, NOT part of firing
# MAGIC
# MAGIC The measured detector is **pure volume anomaly** — there is no harm term in it (I-051).
# MAGIC `harm_share` is attached so an operator can triage, and is explicitly not an input to
# MAGIC detection. Folding it in would invalidate the backtest that justifies the whole claim.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

# Identical to the measured detector — do not tune these without re-running the backtest.
MIN_COUNT = 5
MIN_Z = 3.0
MIN_BASE_MONTHS = 6
MIN_RUN = 2

# Two windows, because they answer different questions.
#   LIVE   — still firing at the corpus edge. "What is emerging right now."
#   RECENT — fired within the last year. Kept because fleet-relevant signals are RARER than
#            consumer ones (NHTSA complaint volume is overwhelmingly passenger vehicles), and
#            a panel that only ever shows non-fleet makes is a panel an operator stops opening.
# Measured 2026-09-02: 9 series live at the corpus edge, **none** touching the fleet; fleet
# series have fired 54 times historically, most recently 2026-01. So the honest operator
# answer is "N emerging across NHTSA, M affecting you" — which is the product's actual claim.
LIVE_WITHIN_MONTHS = 2
RECENT_WITHIN_MONTHS = 12

# COMMAND ----------

# MAGIC %md
# MAGIC ## Monthly series over the full corpus
# MAGIC
# MAGIC The backtest ran on the 37-month embedded subset because the semantic arm needed
# MAGIC embeddings. This needs none, so it runs on all of `silver_complaint` — a longer trailing
# MAGIC baseline, the same 12-month window.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW live_series AS
SELECT make, model, SPLIT(component, ':')[0] AS comp_top,
       DATE_TRUNC('MONTH', received_date) AS month,
       COUNT(*) AS n,
       SUM(CASE WHEN crash OR fire OR COALESCE(injured,0) > 0 OR COALESCE(deaths,0) > 0
                THEN 1 ELSE 0 END) AS n_harm
FROM silver_complaint
WHERE received_date IS NOT NULL AND make IS NOT NULL AND component IS NOT NULL
GROUP BY 1,2,3,4
""")

AS_OF = spark.sql("SELECT MAX(month) AS m FROM live_series").collect()[0]["m"]
print(f"corpus edge (as_of): {AS_OF}")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW live_anomaly AS
SELECT make, model, comp_top, month, n, n_harm,
       ( n >= {MIN_COUNT} AND base_months >= {MIN_BASE_MONTHS}
         AND base_sd > 0 AND (n - base_mean) / base_sd >= {MIN_Z} ) AS fired,
       (n - base_mean) / NULLIF(base_sd, 0) AS z
FROM (
  SELECT make, model, comp_top, month, n, n_harm,
         AVG(n) OVER w AS base_mean, STDDEV_POP(n) OVER w AS base_sd,
         COUNT(*) OVER w AS base_months
  FROM live_series
  WINDOW w AS (PARTITION BY make, model, comp_top ORDER BY month
               ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING)
)
""")

# Gaps-and-islands: month_index - row_number() is constant within a consecutive run.
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW live_run AS
SELECT make, model, comp_top,
       MIN(month) AS run_start, MAX(month) AS run_end,
       COUNT(*) AS run_len, MAX(z) AS run_max_z,
       SUM(n) AS complaints_in_run, SUM(n_harm) AS harm_in_run
FROM (
  SELECT make, model, comp_top, month, n, n_harm, z,
         CAST(MONTHS_BETWEEN(month, DATE'1970-01-01') AS INT)
           - ROW_NUMBER() OVER (PARTITION BY make, model, comp_top ORDER BY month) AS grp
  FROM live_anomaly WHERE fired
)
GROUP BY make, model, comp_top, grp
HAVING COUNT(*) >= {MIN_RUN}
""")

print("total sustained runs in corpus history:", spark.table("live_run").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Keep only the runs that are still live

# COMMAND ----------

# Fleet relevance is a LEFT JOIN on make/model, not an inner one: a signal that touches no
# fleet vehicle is still worth showing — "nothing emerging affects you" is a real answer, and
# suppressing it would make an empty panel indistinguishable from a broken one.
spark.sql(f"""
CREATE OR REPLACE TABLE gold_emerging_signal
CLUSTER BY (comp_top)
AS
SELECT
  CONCAT_WS('|', r.make, r.model, r.comp_top) AS series_key,
  r.make, r.model, r.comp_top,
  r.run_start, r.run_end, r.run_len,
  ROUND(r.run_max_z, 2) AS max_z,
  r.complaints_in_run,
  r.harm_in_run,
  ROUND(r.harm_in_run / r.complaints_in_run, 3) AS harm_share,
  COALESCE(f.fleet_vehicles, 0) AS fleet_vehicles,
  (r.run_end >= ADD_MONTHS(DATE'{AS_OF}', -{LIVE_WITHIN_MONTHS})) AS is_live,
  DATE'{AS_OF}' AS as_of_month
FROM live_run r
LEFT JOIN (
  SELECT UPPER(make) AS make, UPPER(model) AS model, COUNT(DISTINCT vin) AS fleet_vehicles
  FROM gold_fleet_vehicle GROUP BY 1,2
) f ON f.make = r.make AND f.model = r.model
WHERE r.run_end >= ADD_MONTHS(DATE'{AS_OF}', -{RECENT_WITHIN_MONTHS})
""")

spark.sql("""
ALTER TABLE gold_emerging_signal SET TBLPROPERTIES (
  'comment' = 'Series firing NOW under the same volume-anomaly rule the backtest measured (16.0% vs 11.1% placebo). Pure volume anomaly - harm_share is descriptive and is NOT an input to detection (I-051).'
)
""")

n = spark.table("gold_emerging_signal").count()
print(f"live signals: {n}")
print(
    spark.sql(
        "SELECT COUNT(*) AS total, SUM(CAST(is_live AS INT)) AS live, "
        "SUM(CASE WHEN fleet_vehicles > 0 THEN 1 ELSE 0 END) AS fleet_relevant "
        "FROM gold_emerging_signal"
    ).collect()[0]
)
display(
    spark.sql("""
      SELECT series_key, run_end, run_len, max_z, complaints_in_run,
             harm_share, fleet_vehicles, is_live
      FROM gold_emerging_signal
      ORDER BY fleet_vehicles DESC, run_end DESC, max_z DESC LIMIT 20
    """)
)

# COMMAND ----------

# A console that lists every series would be noise, and one that lists none is a broken
# demo. Fail loudly on either, rather than shipping a page nobody looks at twice.
assert n > 0, "no signals at all — check the corpus edge and the window settings"
assert n < 2000, f"{n} signals is not a work queue; tighten the rule before shipping this"
print("emerging-signal build passed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Per-signal explanation via `ai_extract`
# MAGIC
# MAGIC §6 of `ARCHITECTURE.md` says clustering "remains valid for explaining a signal and for
# MAGIC keeping `ai_extract` spend proportional to what an operator sees" — but until now that
# MAGIC was the design intent, not code. Nothing in `src/` ever called `ai_extract`. This closes
# MAGIC that gap the way it was scoped from the start: **once per signal (~n, not ~2.24M)**, on a
# MAGIC bounded sample of narratives, never on the ingest path (`silver_complaint` already has
# MAGIC `COMPDESC` — re-deriving component from prose there was rejected once already, I-009).

# COMMAND ----------

NARRATIVES_PER_SIGNAL = 5

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW signal_narrative_sample AS
SELECT make, model, comp_top,
       ARRAY_JOIN(COLLECT_LIST(narrative), ' ||| ') AS sample_text
FROM (
  SELECT s.make, s.model, s.comp_top, c.narrative,
         ROW_NUMBER() OVER (PARTITION BY s.make, s.model, s.comp_top
                             ORDER BY c.received_date DESC) AS rn
  FROM gold_emerging_signal s
  JOIN silver_complaint c
    ON c.make = s.make AND c.model = s.model
   AND SPLIT(c.component, ':')[0] = s.comp_top
   AND c.received_date BETWEEN s.run_start AND s.run_end
  WHERE c.narrative IS NOT NULL
)
WHERE rn <= {NARRATIVES_PER_SIGNAL}
GROUP BY make, model, comp_top
""")

print(
    "signals with at least one narrative to extract from:",
    spark.table("signal_narrative_sample").count(),
    "of",
    n,
)

# COMMAND ----------

# One ai_extract call per signal — bounded to the same grain as gold_emerging_signal, so
# cost tracks the panel size, not the corpus. Schema form per the current ai_extract
# signature (a JSON schema string, not the older label-array form).
spark.sql("""
CREATE OR REPLACE TEMP VIEW signal_extraction AS
SELECT make, model, comp_top,
       ai_extract(
         sample_text,
         '{"failure_mode":{"type":"string"},"severity_language":{"type":"string"}}',
         map('version','2.0')
       ) AS extracted
FROM signal_narrative_sample
""")

spark.sql("""
ALTER TABLE gold_emerging_signal ADD COLUMNS IF NOT EXISTS (
  failure_mode STRING COMMENT 'ai_extract, per signal not per complaint — descriptive only, never a detection input',
  severity_language STRING COMMENT 'ai_extract, per signal not per complaint — descriptive only, never a detection input'
)
""")

spark.sql("""
MERGE INTO gold_emerging_signal t
USING signal_extraction e
ON t.make = e.make AND t.model = e.model AND t.comp_top = e.comp_top
WHEN MATCHED THEN UPDATE SET
  t.failure_mode = e.extracted:response:failure_mode::STRING,
  t.severity_language = e.extracted:response:severity_language::STRING
""")

explained = spark.sql(
    "SELECT COUNT(*) AS n FROM gold_emerging_signal WHERE failure_mode IS NOT NULL"
).collect()[0]["n"]
print(f"signals with an ai_extract explanation: {explained} of {n}")
display(
    spark.sql("""
      SELECT series_key, failure_mode, severity_language
      FROM gold_emerging_signal WHERE failure_mode IS NOT NULL
      ORDER BY fleet_vehicles DESC LIMIT 10
    """)
)

# COMMAND ----------

# Not yet wired further downstream: the Lakebase loader, `Signal` in
# `routers/signals.py`, and `Signals.tsx` all predate this column and do not read it.
# Surfacing it in the console is real follow-on work, not implied by this cell running —
# state that explicitly rather than let "the column exists" be mistaken for "the operator
# sees it".
print(
    "NOTE: failure_mode/severity_language are not yet surfaced past this table — "
    "Lakebase loader, API, and console are unchanged."
)
