# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — rescope the AI Search source to the fleet's make/model (I-111)
# MAGIC
# MAGIC `silver_complaint_chunk_indexed` — the table `complaint_chunk_idx` syncs from — was
# MAGIC originally scoped to *"complaint chunks for make/model series that had a post-2010 ODI
# MAGIC investigation"*: 1,746,601 chunks, ~6.7 h to build (I-041). That scope was created by a
# MAGIC one-off `CREATE OR REPLACE TABLE AS SELECT` run directly against the workspace on
# MAGIC 2026-08-31 — never committed as a script, so there was nothing to re-run and nothing a
# MAGIC rebuild-from-empty could reproduce.
# MAGIC
# MAGIC **Rescoped 2026-09-23** to the fleet's own make/model pairs — 47 pairs across 15 makes,
# MAGIC read live from `gold_fleet_vehicle` — for the two-window submission plan in
# MAGIC `docs/STATUS.md`. That plan runs the AI Search build twice (a dress rehearsal, then the
# MAGIC real thing before submission), and at the post-2010 scope that is ~13.4 h of index-build
# MAGIC exposure against a hard 4 October deadline, with I-105 already showing one rebuild can
# MAGIC fail outright. At the fleet scope both builds together take under an hour.
# MAGIC
# MAGIC Cost is identical either way — every scope under 2M vectors bills the same $6.72/day
# MAGIC (I-035) — so this buys schedule safety for free. It also reads better to a judge: a
# MAGIC fleet-safety tool retrieving complaints about vehicles this fleet does not operate is a
# MAGIC weaker demo than one scoped to what the fleet actually runs.
# MAGIC
# MAGIC **What this invalidates, and must be corrected before submission — not silently:**
# MAGIC
# MAGIC - `docs/ARCHITECTURE.md` §4.4, `docs/DEMO.md` and `docs/EVIDENCE.md` all quote
# MAGIC   **1,746,601 chunks**; EVIDENCE calls it "well past the 1M" — both become wrong and need
# MAGIC   the new number.
# MAGIC - Phase 3's done-when evidence (`ops_hybrid_query_test`) must be **re-run against this
# MAGIC   scope and republished as-is, including if it comes back worse.** The existing paraphrase
# MAGIC   probe ("car suddenly sped up on its own") leans on unintended-acceleration complaints
# MAGIC   that may concentrate in makes this fleet does not operate — pick a probe the new scope
# MAGIC   can actually answer, and report what it says.
# MAGIC - The full corpus stays in Delta regardless (2.2M complaints, 5.8M TSBs) — the
# MAGIC   lakehouse-scale claim is unaffected. Only the vector index number changes.
# MAGIC
# MAGIC Idempotent — `CREATE OR REPLACE TABLE AS SELECT` — and free to re-run: this table is
# MAGIC Delta only, no AI Search endpoint involved. Needs step 2 (silver pipeline) and step 3
# MAGIC (fleet registry) done first.

# COMMAND ----------

CATALOG = "bootcamp_students"
SCHEMA = "fleetguard"
FQ = f"{CATALOG}.{SCHEMA}"

spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.silver_complaint_chunk_indexed
COMMENT 'AI Search source: complaint chunks for make/model pairs in the fleet roster
(gold_fleet_vehicle -- 47 pairs, 15 makes). Rescoped 2026-09-23 from the post-2010 ODI
investigation series (1,746,601 chunks) for the two-window submission plan (I-111): both
AI Search builds together now take under an hour instead of ~13.4h, at the same
$6.72/day cost under the 2M-vector single-unit threshold (I-035). See docs/ISSUES.md
I-111.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS
SELECT c.*
FROM {FQ}.silver_complaint_chunk c
WHERE EXISTS (
  SELECT 1
  FROM {FQ}.gold_fleet_vehicle v
  WHERE v.make = c.make AND v.model = c.model
)
""")
print("silver_complaint_chunk_indexed rebuilt (fleet make/model scope)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify
# MAGIC
# MAGIC Cross-checked against a live, independent count on 2026-09-23 before this was
# MAGIC committed: **115,499** — exact match to the figure I-035 measured for this scope on
# MAGIC 2026-08-31, so the definition here reproduces what was actually measured, not a
# MAGIC plausible-looking approximation of it.

# COMMAND ----------

n_chunks = spark.sql(f"SELECT count(*) AS n FROM {FQ}.silver_complaint_chunk_indexed").collect()[0][
    "n"
]
n_pairs = spark.sql(
    f"SELECT count(DISTINCT make, model) AS n FROM {FQ}.gold_fleet_vehicle"
).collect()[0]["n"]
n_makes = spark.sql(f"SELECT count(DISTINCT make) AS n FROM {FQ}.gold_fleet_vehicle").collect()[0][
    "n"
]

print(f"chunks       : {n_chunks:,}")
print(f"make/model   : {n_pairs}")
print(f"makes        : {n_makes}")

assert n_chunks == 115_499, f"expected 115,499 chunks for the fleet scope, got {n_chunks:,}"
print("OK — matches the measured fleet make/model scope (I-035, I-111)")
