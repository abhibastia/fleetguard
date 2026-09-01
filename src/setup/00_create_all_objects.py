# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — create every object, from an empty workspace
# MAGIC
# MAGIC Single entry point for rebuilding the project. Run this, follow the order it prints,
# MAGIC and the workspace ends up where it is today.
# MAGIC
# MAGIC ## Read this before assuming it is a DDL script
# MAGIC
# MAGIC Most FleetGuard tables are **derived**, not declared. `silver_complaint` is produced by
# MAGIC a Lakeflow pipeline; `gold_fleet_vehicle` by the fleet-registry job; `gold_backtest_*`
# MAGIC by the Phase 9 chain. Issuing `CREATE TABLE` for those would produce *empty tables with
# MAGIC the right names* — which looks like a successful rebuild and is worse than no script at
# MAGIC all, because the failure is silent.
# MAGIC
# MAGIC So this notebook does two different things, and keeps them clearly apart:
# MAGIC
# MAGIC 1. **Creates the foundations that nothing else creates** — schema, volume, volume
# MAGIC    subdirectories, watermark table. Idempotent; safe to re-run.
# MAGIC 2. **Prints the rebuild order** for everything derived, naming the job that produces
# MAGIC    each object, and **verifies** what actually exists against what should.
# MAGIC
# MAGIC Lakebase's 11 Postgres tables are genuine DDL and live in
# MAGIC `src/lakebase/08_create_remaining_tables.py` — kept separate because they are in a
# MAGIC different database, created over a different connection, and governed by different
# MAGIC safety rules (shared schema, `REPLICA IDENTITY FULL`).
# MAGIC
# MAGIC ## Safety
# MAGIC
# MAGIC `CREATE ... IF NOT EXISTS` throughout. **No `DROP`, no `TRUNCATE`, no `GRANT`, no
# MAGIC `ALTER SCHEMA` anywhere in this notebook.** It runs inside a metastore shared with
# MAGIC ~296 other students and touches only `bootcamp_students.fleetguard`.

# COMMAND ----------

import os

CATALOG = "bootcamp_students"
SCHEMA = "fleetguard"
VOLUME = "nhtsa_flat_files"
FQ = f"{CATALOG}.{SCHEMA}"
VOL_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"

# Catalog creation is NOT attempted: `bootcamp_students` is owned by another user, and on
# free-edition workspaces `databricks catalogs create` is blocked for default storage
# anyway. The catalog is a precondition, not something this project creates.
print(f"target schema : {FQ}")
print(f"target volume : {VOL_PATH}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Foundations

# COMMAND ----------

spark.sql(
    f"CREATE SCHEMA IF NOT EXISTS {FQ} COMMENT 'FleetGuard — vehicle defect early warning and recall response. Medallion layers are table-name prefixes (bronze_/silver_/gold_), not sibling schemas, because catalog creation is unavailable in this workspace.'"
)
spark.sql(
    f"CREATE VOLUME IF NOT EXISTS {FQ}.{VOLUME} COMMENT 'Raw NHTSA ODI flat files, one subdirectory per source.'"
)
print("schema and volume ensured")

# Auto Loader monitors a DIRECTORY, not a file: `read_files('<dir>/FILE.txt')` fails with
# "Input path ... is not a directory" (I-020). One subdirectory per source also stops four
# different schemas sharing a single file listing.
for sub in ("cmpl", "rcl", "inv", "tsbs"):
    os.makedirs(f"{VOL_PATH}/{sub}", exist_ok=True)
    print(f"  {VOL_PATH}/{sub}")

# COMMAND ----------

# The one table not produced by any job — the ingest job reads it on its first run to decide
# whether to download, so it must exist beforehand.
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {FQ}.ops_ingest_watermark (
  source        STRING  COMMENT 'logical source name',
  url           STRING,
  last_modified STRING  COMMENT 'Last-Modified verbatim, for If-Modified-Since',
  etag          STRING  COMMENT 'recorded for reference only — the host ignores If-None-Match (I-004)',
  bytes_written BIGINT,
  landed_file   STRING,
  checked_at    TIMESTAMP,
  outcome       STRING  COMMENT 'downloaded | not_modified | failed'
)
USING DELTA
COMMENT 'Ingest watermarks for the NHTSA flat files. Drives If-Modified-Since change detection.'
""")
print("ops_ingest_watermark ensured")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Rebuild order
# MAGIC
# MAGIC Each step depends on the ones above it. `producer` is what actually creates the
# MAGIC objects — a job name, or the pipeline.

# COMMAND ----------

REBUILD = [
    (
        1,
        "fleetguard-ingest-flat-files",
        "src/ingest/01_download_flat_files.py",
        "volume files (10 .txt across 4 subdirs)",
        "Downloads ~2.6 GB. Re-runs are cheap: If-Modified-Since returns 304 (I-004).",
    ),
    (
        2,
        "PIPELINE fleetguard-bronze-silver",
        "src/pipelines/{bronze,silver}/*.sql",
        "bronze_* (4) + silver_* (10)",
        "Lakeflow Declarative Pipeline. read_files MUST use quote => '\\0' or 143 rows corrupt silently (I-012).",
    ),
    (
        3,
        "fleetguard-build-fleet-registry",
        "src/fleet/04_build_fleet_registry.py",
        "gold_fleet_vehicle, gold_fleet_depot, gold_fleet_exposure",
        "Calls vPIC. make/model/year come from vPIC, never from complaint VINs (I-011).",
    ),
    (
        4,
        "fleetguard-poll-recalls-api",
        "src/ingest/05_poll_recalls_api.py",
        "bronze_recall_api, gold_recall_alert, ops_recall_poll_state",
        "Polls recallsByVehicle using gold_fleet_exposure.recall_model, not the vPIC name (I-029).",
    ),
    (
        5,
        "MANUAL — AI Search",
        "see docs/STATUS.md",
        "endpoint fleetguard-vs, index complaint_chunk_idx",
        "~7 h to sync 1,746,601 chunks. THE ONLY RECURRING COST (~$6.72/day).",
    ),
    (
        6,
        "fleetguard-hybrid-query-test",
        "src/search/09_hybrid_query_test.py",
        "ops_hybrid_query_test",
        "Phase 3 done-when evidence. Must run against a ready index, not a partial one (I-041).",
    ),
    (
        7,
        "fleetguard-create-remaining-tables",
        "src/lakebase/08_create_remaining_tables.py",
        "11 Postgres fleetguard_* tables (+ 11 CDF history tables)",
        "Postgres DDL, not UC. CDF replicates DDL, so history tables appear immediately (I-044).",
    ),
    (
        8,
        "fleetguard-load-reference-from-gold",
        "src/lakebase/10_load_reference_from_gold.py",
        "rows in fleetguard_depot / _vehicle / _recall_campaign",
        "Needs PSYCOPG_IMPL=python or the kernel aborts on a FIPS self-test (I-045).",
    ),
    (
        9,
        "fleetguard-measure-cdf-latency",
        "src/lakebase/12_measure_cdf_latency.py",
        "ops_cdf_latency",
        "Measures §8.3's capture latency. Warm the query path first or you measure Spark startup (I-046).",
    ),
    (
        10,
        "fleetguard-lead-time-backtest-v2",
        "src/backtest/03_lead_time_backtest_v2.py",
        "gold_lead_time_backtest, _control, _summary",
        "The published 16.0% / 11.1% baseline. Needs only silver — runnable straight after step 2.",
    ),
    (
        11,
        "fleetguard-build-backtest-scope",
        "src/backtest/04_build_backtest_scope.py",
        "gold_backtest_scope, gold_backtest_complaint",
        "37-month window: 24 lookback + 12 baseline + 1. A 24-month window silently kills long-lead detection.",
    ),
    (
        12,
        "fleetguard-embed-backtest-complaints",
        "src/backtest/05_embed_backtest_complaints.py",
        "gold_backtest_embedding (273,398 x 1024)",
        "~$5 of ai_query. Resumable — re-runs only embed complaint_ids not already present.",
    ),
    (
        13,
        "fleetguard-semantic-subdivision",
        "src/backtest/08_semantic_subdivision.py",
        "gold_backtest_subcluster",
        "k-means subdivision. Replaced HDBSCAN, which labelled 85% of embeddings noise (I-048).",
    ),
    (
        14,
        "fleetguard-lead-time-backtest-v3",
        "src/backtest/09_lead_time_backtest_v3.py",
        "gold_lead_time_v3, gold_lead_time_v3_summary",
        "Recomputes BOTH groupings on the same data — never compare v3 against the published v2.",
    ),
]

w = max(len(r[1]) for r in REBUILD)
for n, producer, path, creates, note in REBUILD:
    print(f"\n{n:>2}. {producer:<{w}}  {path}")
    print(f"    creates: {creates}")
    print(f"    note   : {note}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Verify — what exists vs what should
# MAGIC
# MAGIC The point of the exercise. A rebuild is only complete when this reports no missing
# MAGIC objects; the step number tells you which producer to run for anything absent.

# COMMAND ----------

EXPECTED = {
    "bronze_complaints": 1,
    "bronze_recalls": 1,
    "bronze_investigations": 1,
    "bronze_tsbs": 1,
    "silver_complaint": 2,
    "silver_complaint_quarantine": 2,
    "silver_complaint_chunk": 2,
    "silver_complaint_chunk_indexed": 2,
    "silver_recall": 2,
    "silver_recall_quarantine": 2,
    "silver_investigation": 2,
    "silver_investigation_quarantine": 2,
    "silver_investigation_case": 2,
    "silver_tsb": 2,
    "silver_tsb_bulletin": 2,
    "gold_fleet_vehicle": 3,
    "gold_fleet_depot": 3,
    "gold_fleet_exposure": 3,
    "bronze_recall_api": 4,
    "gold_recall_alert": 4,
    "ops_recall_poll_state": 4,
    "ops_hybrid_query_test": 6,
    "ops_lakebase_load": 8,
    "ops_cdf_latency": 9,
    "gold_lead_time_backtest": 10,
    "gold_lead_time_control": 10,
    "gold_lead_time_summary": 10,
    "gold_backtest_scope": 11,
    "gold_backtest_complaint": 11,
    "gold_backtest_embedding": 12,
    "gold_backtest_subcluster": 13,
    "gold_lead_time_v3": 14,
    "gold_lead_time_v3_summary": 14,
    "ops_ingest_watermark": 0,  # created above
}

present = {
    r["tableName"]
    for r in spark.sql(f"SHOW TABLES IN {FQ}").collect()
    if not r["tableName"].startswith(("__", "event_log"))
}

missing = sorted(set(EXPECTED) - present, key=lambda t: (EXPECTED[t], t))
extra = sorted(present - set(EXPECTED))

print(f"expected {len(EXPECTED)} objects · present {len(present)}\n")
if missing:
    print("MISSING — run the producer at the given step:")
    for t in missing:
        print(f"  step {EXPECTED[t]:>2}   {t}")
else:
    print("nothing missing — every expected object exists")

if extra:
    print(
        f"\nnot in the manifest ({len(extra)}) — add them here or they will be lost in a rebuild:"
    )
    for t in extra:
        print(f"  {t}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Not covered here, deliberately
# MAGIC
# MAGIC - **Lakebase Postgres tables** — `src/lakebase/08_create_remaining_tables.py`. Separate
# MAGIC   database, separate connection, separate safety rules.
# MAGIC - **Lakebase CDF enablement** — **UI only**. No CLI, no API, and not a Declarative
# MAGIC   Automation Bundle resource (I-017). It is a manual runbook step in any rebuild.
# MAGIC - **The AI Search endpoint and index** — created manually (step 5) because it is the
# MAGIC   only recurring cost and should never be resurrected by accident.
