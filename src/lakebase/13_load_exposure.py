# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 5: load vehicle exposure into Lakebase
# MAGIC
# MAGIC Populates `fleetguard_vehicle_exposure`, the table the operator work queue reads.
# MAGIC This is the MVP's hard blocker: without it there is no queue.
# MAGIC
# MAGIC ## Scope: `EXACT` matches only — a decision, not a limitation
# MAGIC
# MAGIC `gold_fleet_exposure` holds **989,042** rows, of which **263,686** are `EXACT` and
# MAGIC 725,356 are `MODEL_VARIANT` (I-030). Loading `EXACT` only, because:
# MAGIC
# MAGIC 1. **§7's deterministic guarantee applies to `EXACT` alone.** The variant tier is what
# MAGIC    Model B (Phase 4, unbuilt) exists to score. Putting unscored variants in the work
# MAGIC    queue would present a probabilistic match as a certainty.
# MAGIC 2. **CDF is shared with ~296 other students.** ~1M change events through a
# MAGIC    Public-Preview pipeline is inconsiderate when a correct subset does the job.
# MAGIC
# MAGIC The variant tier is loaded once Model B can attach a confidence to it.
# MAGIC
# MAGIC ## Grain: one row per (vin, campaign) — deduplication is mandatory
# MAGIC
# MAGIC **`gold_fleet_exposure` is NOT unique on `(vin, campaign_number)`.** Measured:
# MAGIC 145,363 of the 263,686 `EXACT` rows are duplicate pairs — 53,915 distinct pairs
# MAGIC averaging **3.7 rows each**. Cause is join fan-out against `silver_recall`, which is at
# MAGIC make/model/year grain: component varies in about half the cases, and the remainder are
# MAGIC exact repeats.
# MAGIC
# MAGIC Loading as-is would inflate the work queue **2.2×** — an operator would see a vehicle
# MAGIC listed against the same campaign four times. The DDL already states the correct grain:
# MAGIC `fleetguard_vehicle_exposure` has no `component` column, because component is a
# MAGIC property of the *campaign* and already lives on `fleetguard_recall_campaign`.
# MAGIC
# MAGIC **263,686 rows → 118,323 distinct (vin, campaign) pairs.**
# MAGIC
# MAGIC ## Safety
# MAGIC
# MAGIC Idempotent, transactional, name-guarded. A **unique index on `(vin, campaign_id)`**
# MAGIC makes `ON CONFLICT` possible and enforces the grain at the database, so a future load
# MAGIC cannot silently reintroduce the fan-out.

# COMMAND ----------

import datetime
import io
import os
import time

# See I-045 — psycopg[binary] 3.3.5 aborts the kernel on a FIPS self-test.
os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
UC = "bootcamp_students.fleetguard"
TABLE = "fleetguard_vehicle_exposure"

MATCH_BASIS = "EXACT"

assert TABLE.startswith("fleetguard_"), "refusing: non-project table name"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name

conn = psycopg.connect(
    host=host, user=user, password=token, dbname=PG_DB, sslmode="require", autocommit=False
)
print(f"connected to {PG_DB} as {user}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Source — deduplicated to the operational grain
# MAGIC
# MAGIC `match_confidence` is `1.0000` for `EXACT` by definition: the match is deterministic on
# MAGIC make, model, year and the manufacture window. Model B will supply a real probability
# MAGIC for the variant tier; hardcoding 1.0 here would be wrong for those rows, which is
# MAGIC another reason they are not loaded yet.

# COMMAND ----------

SQL = f"""
SELECT vin,
       campaign_number       AS campaign_id,
       CAST(NULL AS STRING)  AS signal_id,
       match_basis,
       CAST(1.0 AS DECIMAL(5,4)) AS match_confidence,
       MIN(matched_at)       AS matched_at
FROM {UC}.gold_fleet_exposure
WHERE match_basis = '{MATCH_BASIS}'
  AND vin IS NOT NULL AND campaign_number IS NOT NULL
GROUP BY vin, campaign_number, match_basis
"""

COLS = ["vin", "campaign_id", "signal_id", "match_basis", "match_confidence", "matched_at"]

pdf = spark.sql(SQL).toPandas()
raw = spark.sql(
    f"SELECT COUNT(*) n FROM {UC}.gold_fleet_exposure WHERE match_basis='{MATCH_BASIS}'"
).collect()[0]["n"]
print(f"source rows (raw)   : {raw:,}")
print(f"after dedupe        : {len(pdf):,}   (removed {raw - len(pdf):,} fan-out rows)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Enforce the grain in the database
# MAGIC
# MAGIC The unique index is not only there to enable `ON CONFLICT` — it is the invariant. If a
# MAGIC later load reintroduces fan-out, this raises instead of quietly doubling the queue.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ux_fg_exposure_vin_campaign "
        f"ON {PG_SCHEMA}.{TABLE} (vin, campaign_id)"
    )
    print("unique index on (vin, campaign_id) ensured")

# COMMAND ----------


def to_csv_buffer(frame, columns):
    buf = io.StringIO()
    frame[columns].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    return buf


with conn.cursor() as cur:
    # pandas widens nullable INTs to float (I-045 sibling); discover integer columns from
    # Postgres rather than hardcoding, so a later column cannot reintroduce '2020.0'.
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (PG_SCHEMA, TABLE),
    )
    pg_types = dict(cur.fetchall())
    for c in COLS:
        if pg_types.get(c) in ("smallint", "integer", "bigint") and str(pdf[c].dtype).startswith(
            "float"
        ):
            pdf[c] = pdf[c].round().astype("Int64")
            print(f"  cast {c}: float -> Int64")

    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE}")
    before = cur.fetchone()[0]

    collist = ", ".join(COLS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c not in ("vin", "campaign_id"))

    t0 = time.time()
    cur.execute(
        f"CREATE TEMP TABLE stage_exposure "
        f"(LIKE {PG_SCHEMA}.{TABLE} INCLUDING DEFAULTS) ON COMMIT DROP"
    )
    with cur.copy(
        f"COPY stage_exposure ({collist}) FROM STDIN WITH (FORMAT csv, NULL '\\N')"
    ) as cp:
        cp.write(to_csv_buffer(pdf, COLS).read())
    copy_s = time.time() - t0

    cur.execute(f"""
        INSERT INTO {PG_SCHEMA}.{TABLE} ({collist})
        SELECT {collist} FROM stage_exposure
        ON CONFLICT (vin, campaign_id) DO UPDATE SET {updates}
    """)
    upserted = cur.rowcount
    total_s = time.time() - t0

    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE}")
    after = cur.fetchone()[0]

print(f"\n{before:,} -> {after:,} rows  ({upserted:,} upserted)")
print(f"COPY {copy_s:.1f}s · total {total_s:.1f}s · {len(pdf) / max(total_s, 0.01):,.0f} rows/s")
print("NOTE: PSYCOPG_IMPL=python is the slower pure-Python driver (I-045).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify before committing

# COMMAND ----------

problems = []
if after != len(pdf):
    problems.append(f"row count {after:,} != deduped source {len(pdf):,}")

with conn.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE} WHERE match_basis <> %s", (MATCH_BASIS,))
    off_scope = cur.fetchone()[0]
    if off_scope:
        problems.append(f"{off_scope:,} rows outside the {MATCH_BASIS} scope")

    # Every exposure must reference a campaign we actually loaded, or the queue shows a
    # vehicle exposed to a campaign with no detail behind it.
    cur.execute(f"""
        SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE} e
        LEFT JOIN {PG_SCHEMA}.fleetguard_recall_campaign c ON c.campaign_id = e.campaign_id
        WHERE c.campaign_id IS NULL
    """)
    orphans = cur.fetchone()[0]
    if orphans:
        problems.append(f"{orphans:,} exposures reference an unloaded campaign")

    # Every exposure must reference a vehicle in the fleet roster.
    cur.execute(f"""
        SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE} e
        LEFT JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
        WHERE v.vin IS NULL
    """)
    ghosts = cur.fetchone()[0]
    if ghosts:
        problems.append(f"{ghosts:,} exposures reference a VIN not in the fleet")

print(f"  rows            {after:,}")
print(f"  off-scope       {off_scope:,}")
print(f"  orphan campaign {orphans:,}")
print(f"  ghost VIN       {ghosts:,}")

if problems:
    conn.rollback()
    conn.close()
    raise SystemExit("ROLLED BACK — " + "; ".join(problems))

# COMMAND ----------

commit_epoch = None
try:
    commit_epoch = time.time()
    conn.commit()
    print(f"COMMITTED at epoch {commit_epoch:.3f}")
except Exception:
    conn.rollback()
    print("ROLLED BACK — shared schema untouched")
    raise
finally:
    conn.close()

# COMMAND ----------

(
    spark.createDataFrame(
        [
            {
                "table_name": TABLE,
                "source_rows": int(raw),
                "rows_after": int(after),
                "upserted": int(upserted),
                "load_seconds": float(round(total_s, 2)),
                "committed_at_epoch": float(commit_epoch),
                "committed_at": datetime.datetime.fromtimestamp(
                    commit_epoch, tz=datetime.UTC
                ),
            }
        ]
    )
    .write.mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(f"{UC}.ops_lakebase_load")
)
display(spark.table(f"{UC}.ops_lakebase_load").orderBy("committed_at"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC ```sql
# MAGIC SELECT _pg_change_type, COUNT(*)
# MAGIC FROM bootcamp_students.bootcamp_cdc.lb_fleetguard_vehicle_exposure_history
# MAGIC GROUP BY _pg_change_type;
# MAGIC ```
# MAGIC
# MAGIC Expect ~118k `insert` rows. CDF flushes roughly every 15 s (measured 7.1–15.6 s, I-046),
# MAGIC so a load this size takes a few flush cycles to appear in full — a partial count
# MAGIC immediately after the commit is normal, not a failure.
