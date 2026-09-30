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
# MAGIC **Widened 2026-09-23 to the fleet's make/model pairs matched in TIERS (I-115).** The
# MAGIC first version of this rescope joined `v.make = c.make AND v.model = c.model` — exact
# MAGIC spelling only — and that is wrong on this data in a way this project had already fixed
# MAGIC three times elsewhere. NHTSA writes `F-250 SD`; vPIC, and therefore `gold_fleet_vehicle`,
# MAGIC writes `F-250`. Same trucks, no exact match. **All 2,116 of the fleet's F-250s match only
# MAGIC as `MODEL_VARIANT`** (I-030), so the fleet's most numerous truck had its complaints
# MAGIC excluded from the retrieval corpus outright: an agent asked about F-250 brake failures
# MAGIC searched an index that did not contain the evidence, and answered from what was left.
# MAGIC
# MAGIC That is the worst shape of failure this project keeps finding — not an error, a confident
# MAGIC answer over a silently truncated corpus. I-030 was fixed in the gold layer, I-075 in the
# MAGIC agent write path and I-079 in the emerging detector; the retrieval path is the one that
# MAGIC never got it. The predicate below is **copied from `src/backtest/10_emerging_signals.py`**
# MAGIC rather than rewritten, so all four now agree by construction.
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

dbutils.widgets.text("catalog", "bootcamp_students")
dbutils.widgets.text("schema", "fleetguard")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
FQ = f"{CATALOG}.{SCHEMA}"

spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.silver_complaint_chunk_indexed
COMMENT 'AI Search source: complaint chunks for make/model pairs in the fleet roster
(gold_fleet_vehicle), matched in the same EXACT-then-MODEL_VARIANT tiers as the gold
exposure table, the agent write path and the emerging detector. Rescoped 2026-09-23 from
the post-2010 ODI investigation series (1,746,601 chunks) for the two-window submission
plan (I-111); the exact-only join was widened to variants 2026-09-23 (I-115) because it
excluded every one of the fleet''s 2,116 F-250s. Same $6.72/day cost under the 2M-vector
single-unit threshold (I-035). See docs/ISSUES.md I-111, I-115.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS
WITH fleet AS (
  SELECT DISTINCT UPPER(make) AS make, UPPER(model) AS model
  FROM {FQ}.gold_fleet_vehicle
)
SELECT
  c.*,
  -- Nested tiers, not disjoint ones: EXACT simply wins when it exists, mirroring
  -- agent_actions.py and 10_emerging_signals.py. Carried into the table so a later pass can
  -- sync it and let the agent say *how* a retrieved complaint relates to the fleet. NOT in
  -- columns_to_sync today -- adding it changes the index-creation command away from the one
  -- proven in Run 1, which is not a thing to do days before submission (see docs/RUNBOOK.md).
  CASE
    WHEN EXISTS (
      SELECT 1 FROM fleet f WHERE f.make = c.make AND f.model = c.model
    ) THEN 'EXACT'
    ELSE 'MODEL_VARIANT'
  END AS match_basis
FROM {FQ}.silver_complaint_chunk c
WHERE EXISTS (
  -- EXISTS, NOT A JOIN. A join here fans out: one complaint model can match several fleet
  -- models (`F-250` and `F-250 SD` are both in the roster), which would duplicate chunks and
  -- silently inflate the index -- and `chunk_id` is the index primary key, so duplicates
  -- would be a build failure rather than merely wasteful.
  SELECT 1
  FROM fleet f
  WHERE f.make = c.make
    AND (f.model = c.model
         OR c.model LIKE f.model || ' %'
         OR f.model LIKE c.model || ' %')
)
""")
print("silver_complaint_chunk_indexed rebuilt (fleet make/model scope, EXACT + MODEL_VARIANT)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify
# MAGIC
# MAGIC **MEASURED 2026-09-30 in Run 2, and the exact-equality assert is back (I-126).** It had
# MAGIC been downgraded to a bounded floor/ceiling check at I-115, deliberately: widening the join
# MAGIC to `MODEL_VARIANT` invalidated the old `n_chunks == 115_499` constant, the index endpoint
# MAGIC and warehouse are torn down between the two submission windows, and writing a guessed
# MAGIC replacement would have been exactly the failure this project keeps logging — a
# MAGIC plausible-looking figure asserted as a measured one.
# MAGIC
# MAGIC | tier | chunks |
# MAGIC |---|---|
# MAGIC | `EXACT` | **115,499** |
# MAGIC | `MODEL_VARIANT` | **63,848** |
# MAGIC | **total** | **179,347** |
# MAGIC
# MAGIC **Two independent derivations agree, which is why this is pinned rather than merely
# MAGIC recorded.** The predicate below was run directly against `abhi` as an ad-hoc query
# MAGIC *before* this job, and the job then printed the same three numbers. That is the I-035
# MAGIC discipline: the figure four documents were waiting on is not the single output of the code
# MAGIC it is supposed to be checking.
# MAGIC
# MAGIC Two properties worth keeping:
# MAGIC
# MAGIC - **`EXACT` reproduces I-035's 2026-08-31 figure of 115,499 to the row.** The old scope is
# MAGIC   exactly reproducible, which is what makes the widened total trustworthy rather than
# MAGIC   merely plausible — and it means a future regression in the *variant* half shows up as a
# MAGIC   change in the total while `EXACT` stays put, localising the fault.
# MAGIC - **The cross-workspace prediction held.** Free edition measured `116,252 + 64,577 =
# MAGIC   180,829` (I-123), and the reasoning recorded in `docs/RUNBOOK.md` — smaller corpus
# MAGIC   (2,240,289 vs 2,249,908 complaints), 47 vs 49 make/model pairs — predicted abhi would
# MAGIC   land *"near 180K, a few percent lower"*. Actual: **0.82% lower**.
# MAGIC
# MAGIC The `MODEL_VARIANT` tier is **35.6%** of the corpus here. That is a much smaller share than
# MAGIC I-030's ~3:1 variant-to-exact ratio on `gold_fleet_exposure`, and the difference is real
# MAGIC rather than a bug: exposure is counted per *vehicle* and the fleet's single most numerous
# MAGIC model (2,116 F-250s) matches only as a variant, while chunks are counted per *complaint*
# MAGIC and NHTSA's exact spellings dominate the narrative corpus. **Do not "correct" one of these
# MAGIC two numbers to match the other** — they measure different populations.

# COMMAND ----------

# Measured in Run 2 on `abhi` (2026-09-30, I-126), and cross-checked against an independent
# ad-hoc count of the same predicate before this job ran. The per-tier constants are pinned as
# well as the total: a regression that moved rows *between* tiers while preserving the sum would
# otherwise pass, and that is precisely the I-115 failure — every F-250 silently in the wrong
# bucket while the headline number looked right.
EXPECTED_EXACT = 115_499  # unchanged since I-035, 2026-08-31 — the old scope reproduces exactly
EXPECTED_VARIANT = 63_848  # the I-115 widening; 35.6% of the corpus
EXPECTED_TOTAL = EXPECTED_EXACT + EXPECTED_VARIANT  # 179,347

tiers = {
    r["match_basis"]: r["n"]
    for r in spark.sql(
        f"""SELECT match_basis, count(*) AS n
            FROM {FQ}.silver_complaint_chunk_indexed GROUP BY match_basis"""
    ).collect()
}
n_chunks = sum(tiers.values())
n_pairs = spark.sql(
    f"SELECT count(DISTINCT make, model) AS n FROM {FQ}.gold_fleet_vehicle"
).collect()[0]["n"]
n_makes = spark.sql(f"SELECT count(DISTINCT make) AS n FROM {FQ}.gold_fleet_vehicle").collect()[0][
    "n"
]

print(f"chunks        : {n_chunks:,}")
print(f"  EXACT       : {tiers.get('EXACT', 0):,}")
print(f"  MODEL_VARIANT: {tiers.get('MODEL_VARIANT', 0):,}")
print(f"make/model    : {n_pairs}")
print(f"makes         : {n_makes}")
print(f"\n{n_chunks:,} chunks — this is the number the index builds (pinned, I-126).")

# Per-tier first, then the total. A failure in one tier names itself instead of leaving the
# operator to work out which half of the predicate moved.
assert tiers.get("EXACT", 0) == EXPECTED_EXACT, (
    f"EXACT is {tiers.get('EXACT', 0):,}, expected {EXPECTED_EXACT:,}. This half has been "
    "stable since I-035 (2026-08-31) across a full silver rebuild and the I-115 widening, so a "
    "change here is the fleet roster or the complaint corpus moving, not this predicate. Check "
    "gold_fleet_vehicle (expect 20,000 vehicles / 47 make-model pairs / 15 makes) before "
    "re-pinning."
)
assert tiers.get("MODEL_VARIANT", 0) == EXPECTED_VARIANT, (
    f"MODEL_VARIANT is {tiers.get('MODEL_VARIANT', 0):,}, expected {EXPECTED_VARIANT:,}. "
    "**Zero means this is still the old exact-only scope** — the failure I-115 was filed for, "
    "in which all 2,116 of the fleet's F-250s are absent from the retrieval corpus and the "
    "agent answers about them from whatever is left. Any other value means the word-boundary "
    "anchor on `LIKE model || ' %'` changed behaviour; check it before building an index."
)
assert n_chunks == EXPECTED_TOTAL, (
    f"got {n_chunks:,} chunks, expected {EXPECTED_TOTAL:,} — and both tiers matched "
    "individually, so this is arithmetic, not scope. Investigate rather than re-pinning."
)
print(f"OK — {n_chunks:,} chunks, both tiers exact (I-030, I-111, I-115, I-126)")
