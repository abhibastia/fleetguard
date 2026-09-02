# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Model B golden set (Phase 4, E-07/E-08)
# MAGIC
# MAGIC Model B scores the `MODEL_VARIANT` residual — cases where make matches and the
# MAGIC manufacture window fits, but the roster's vPIC-decoded model string differs from the
# MAGIC recall's own model field (`F-250` vs `F-250 SD`; `TRANSIT CONNECT` vs `TRANSIT`).
# MAGIC 725,356 of 989,042 exposure rows are this tier (I-030) — nearly 3× the exact tier — so
# MAGIC it is not an edge case, it is most of the fleet's real exposure.
# MAGIC
# MAGIC ## Why this notebook exists, and the rule it does not break
# MAGIC
# MAGIC `ENHANCEMENTS.md` E-08 is explicit: synthetic evals are for the **agent only**. Model B's
# MAGIC golden set must be **real** labelled recall/fleet pairs — nothing generated to look
# MAGIC plausible. That rules out asking a model to invent labels.
# MAGIC
# MAGIC It does not rule out this approach: NHTSA's `defect_description` field is **free text,
# MAGIC but it is NHTSA's own published recall scope** —
# MAGIC *"Ford Motor Company is recalling certain 2022 Super Duty F-250, F-350, 2021 F-150…"* —
# MAGIC naming vehicles in the regulator's own words even where the *structured* `model` field
# MAGIC says something narrower (`F-250 SD`). Whether a roster vehicle is truly in scope is
# MAGIC therefore derivable from a real, authoritative source, not invented.
# MAGIC
# MAGIC **What this is not: human adjudication.** A text-mention is a proxy, not a verdict — a
# MAGIC name could appear in a passing clause, or a genuinely-covered vehicle could go unnamed
# MAGIC because NHTSA used an unlisted synonym. `derivation_method` says exactly how each label
# MAGIC was produced, so nothing downstream can mistake this for a human-reviewed set. A random
# MAGIC sample is printed for a sanity spot-check before the table is trusted.
# MAGIC
# MAGIC ## The case that justifies being careful
# MAGIC
# MAGIC `FORD TRANSIT CONNECT` appears as a `MODEL_VARIANT` match against recall model
# MAGIC `TRANSIT` (I-059). Transit Connect and Transit are different platforms. The label rule
# MAGIC below requires the **full multi-word model string** to appear in the recall text, so
# MAGIC "TRANSIT" alone in the description does not confirm "TRANSIT CONNECT" — this is the
# MAGIC case the substring direction has to get right.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

MIN_GOLDEN_SET = 150

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW variant_pairs AS
SELECT DISTINCT e.campaign_number, e.make, e.model, e.recall_model
FROM gold_fleet_exposure e
WHERE e.match_basis = 'MODEL_VARIANT'
""")

spark.sql("""
CREATE OR REPLACE TEMP VIEW recall_text AS
SELECT campaign_number, FIRST(defect_description) AS defect_description
FROM silver_recall
GROUP BY campaign_number
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Label rule
# MAGIC
# MAGIC `label = 1` when the roster's full model string appears verbatim (case-insensitive) in
# MAGIC the recall's own defect description AND the make appears too — both, so a lone
# MAGIC coincidental word match cannot pass. `label = 0` only when neither the vPIC model nor
# MAGIC the recall's own `recall_model` field name it — a pair with `recall_model` support but no
# MAGIC text mention is genuinely ambiguous and is EXCLUDED rather than force-labelled, because
# MAGIC guessing at that boundary is exactly the synthetic-label mistake this notebook exists to
# MAGIC avoid.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold_model_b_golden_set
CLUSTER BY (campaign_number)
AS
SELECT
  p.campaign_number, p.make, p.model, p.recall_model,
  CASE
    WHEN UPPER(r.defect_description) LIKE CONCAT('%', UPPER(p.model), '%')
     AND UPPER(r.defect_description) LIKE CONCAT('%', UPPER(p.make), '%')
      THEN 1
    WHEN UPPER(r.defect_description) NOT LIKE CONCAT('%', UPPER(p.model), '%')
     AND UPPER(p.recall_model) NOT LIKE CONCAT('%', UPPER(p.model), '%')
      THEN 0
    ELSE NULL  -- ambiguous: excluded, not guessed
  END AS label,
  SUBSTRING(r.defect_description, 1, 240) AS evidence_snippet,
  'defect_description_text_match_v1' AS derivation_method,
  CURRENT_TIMESTAMP() AS derived_at
FROM variant_pairs p
JOIN recall_text r ON r.campaign_number = p.campaign_number
""")

spark.sql("""
ALTER TABLE gold_model_b_golden_set SET TBLPROPERTIES (
  'comment' = 'Model B golden set. Labels derived from NHTSA defect_description text
  (real regulatory scope language), NOT human-adjudicated and NOT synthetic (E-08). NULL
  label = ambiguous pair, excluded rather than guessed. See I-059 / src/fleet/05.'
)
""")

df = spark.table("gold_model_b_golden_set")
total = df.count()
labelled = df.filter("label IS NOT NULL").count()
pos = df.filter("label = 1").count()
neg = df.filter("label = 0").count()
ambiguous = total - labelled
print(f"candidate pairs   : {total}")
print(f"labelled          : {labelled}  ({pos} positive, {neg} negative)")
print(f"excluded ambiguous: {ambiguous}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Spot-check before trusting it
# MAGIC
# MAGIC Fifteen labelled pairs, printed with their evidence, for a human sanity pass. This is
# MAGIC not a substitute for real adjudication — it is the minimum check before a derived label
# MAGIC set is used to train anything.

# COMMAND ----------

sample = spark.sql("""
    SELECT campaign_number, make, model, recall_model, label, evidence_snippet
    FROM gold_model_b_golden_set WHERE label IS NOT NULL
    ORDER BY RAND(42) LIMIT 15
""").collect()

for r in sample:
    tag = "POS" if r["label"] == 1 else "NEG"
    print(f"[{tag}] {r['make']} {r['model']}  (recall model: {r['recall_model']})")
    print(f"      {r['evidence_snippet']}")
    print()

# COMMAND ----------

assert labelled >= MIN_GOLDEN_SET, (
    f"only {labelled} usable pairs, need >= {MIN_GOLDEN_SET} — widen the derivation or "
    f"accept a smaller golden set explicitly, do not silently proceed under target"
)
assert pos > 0 and neg > 0, "golden set is single-class — cannot train or evaluate a classifier"
print(f"\ngolden set ready: {labelled} pairs ({pos} positive / {neg} negative)")
