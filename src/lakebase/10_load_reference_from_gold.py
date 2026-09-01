# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 5 step 3: load the reference tables from gold
# MAGIC
# MAGIC Populates `fleetguard_depot`, `fleetguard_vehicle` and `fleetguard_recall_campaign`
# MAGIC from the gold layer. Three reasons this comes before the big exposure load:
# MAGIC
# MAGIC 1. ~~CDF creates a destination on the first write~~ — **wrong, and already settled.**
# MAGIC    CDF replicates the DDL, so all eleven history tables existed the moment step 2
# MAGIC    committed, with exact names and no `_1` collision suffixes (I-044). The naming
# MAGIC    decision (I-036) is therefore already proven for all eleven, not just the one
# MAGIC    tested in I-038. This load is about **data**, not about materialising tables.
# MAGIC 2. **It yields a measured CDF throughput figure** on ~20k rows, which is what the
# MAGIC    exposure decision needs — `gold_fleet_exposure` is **989,042 rows**, and pushing
# MAGIC    that through a *shared* Public-Preview CDF pipeline unmeasured would violate the
# MAGIC    standing constraint that running a notebook must not affect other users.
# MAGIC 3. `fleetguard_vehicle_exposure` has FK-shaped references to vehicle and campaign, so
# MAGIC    the parents should exist first regardless.
# MAGIC
# MAGIC **Safety properties** (unchanged from step 2, this writes to a schema shared with
# MAGIC ~296 students): idempotent, transactional, name-guarded, no `DROP`/`TRUNCATE`/`ALTER
# MAGIC SCHEMA`/`GRANT` anywhere.
# MAGIC
# MAGIC Idempotency is `COPY` into a `TEMP` table followed by `INSERT … ON CONFLICT DO
# MAGIC UPDATE`. `COPY` alone cannot express a conflict clause, and plain `INSERT` of 20k rows
# MAGIC one statement at a time is minutes of round-trips. The temp table is session-scoped,
# MAGIC so it is invisible to other users and disappears on disconnect.

# COMMAND ----------

import datetime
import io
import os
import time

# `psycopg[binary]` 3.3.5 bundles an OpenSSL that fails a FIPS self-test on Databricks
# serverless and aborts the whole kernel with SIGABRT — before any project code runs
# (I-045). The pure-Python implementation talks to the system libpq (16.0.15) instead and
# loads cleanly. This MUST be set before `import psycopg`; psycopg reads it at import.
os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402 - must follow the PSYCOPG_IMPL assignment above
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
UC = "bootcamp_students.fleetguard"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name

conn = psycopg.connect(
    host=host,
    user=user,
    password=token,
    dbname=PG_DB,
    sslmode="require",
    autocommit=False,
)
print(f"connected to {PG_DB} as {user}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Source queries
# MAGIC
# MAGIC `fleetguard_recall_campaign` is at **campaign** grain, but `silver_recall` is at
# MAGIC make/model/year grain — 244,701 rows for 15,211 campaigns. Aggregating loses the
# MAGIC per-model detail, which is correct here: the vehicle-level link lives in
# MAGIC `fleetguard_vehicle_exposure`, and the campaign row is descriptive.
# MAGIC
# MAGIC Where a campaign spans several makes/models/years the descriptive column is set
# MAGIC **NULL rather than to an arbitrary representative** — a campaign covering nine models
# MAGIC does not have "a" model, and picking one silently would be a lie a demo could read
# MAGIC straight out of the table. `model_year` is kept as an explicit min/max pair.

# COMMAND ----------

SOURCES = {
    "fleetguard_depot": {
        "key": "depot_id",
        "columns": ["depot_id", "depot_name", "region", "city", "state", "manager_principal"],
        "sql": f"""
            SELECT depot_id, depot_name, region, city, state, manager_principal
            FROM {UC}.gold_fleet_depot
        """,
    },
    "fleetguard_vehicle": {
        "key": "vin",
        "columns": [
            "vin",
            "depot_id",
            "segment",
            "make",
            "model",
            "model_year",
            "body_class",
            "gvwr_class",
            "manufacture_date",
            "mileage",
            "status",
        ],
        "sql": f"""
            SELECT vin, depot_id, segment, make, model, model_year,
                   body_class, gvwr_class, manufacture_date, mileage, status
            FROM {UC}.gold_fleet_vehicle
        """,
    },
    # Only campaigns the fleet is actually exposed to. The other ~15k NHTSA campaigns
    # describe vehicles nobody in this fleet owns; loading them would inflate the table
    # and the CDF stream with rows no query ever reaches.
    "fleetguard_recall_campaign": {
        "key": "campaign_id",
        "columns": [
            "campaign_id",
            "nhtsa_number",
            "component",
            "make",
            "model",
            "model_year",
            "manufacture_start",
            "manufacture_end",
            "do_not_drive",
            "park_outside",
            "park_it",
            "consequence",
            "remedy",
            "issued_at",
            "source",
        ],
        "sql": f"""
            WITH exposed AS (
                SELECT DISTINCT campaign_number
                FROM {UC}.gold_fleet_exposure
                WHERE campaign_number IS NOT NULL
            )
            SELECT
                r.campaign_number                                   AS campaign_id,
                r.campaign_number                                   AS nhtsa_number,
                MAX(r.component)                                    AS component,
                -- NULL unless the campaign is unambiguous at that grain
                CASE WHEN COUNT(DISTINCT r.make)  = 1 THEN MAX(r.make)  END AS make,
                CASE WHEN COUNT(DISTINCT r.model) = 1 THEN MAX(r.model) END AS model,
                CASE WHEN COUNT(DISTINCT r.model_year) = 1 THEN MAX(r.model_year) END AS model_year,
                CAST(MIN(r.manufacture_start) AS DATE)              AS manufacture_start,
                CAST(MAX(r.manufacture_end)   AS DATE)              AS manufacture_end,
                COALESCE(MAX(CASE WHEN r.do_not_drive  THEN 1 ELSE 0 END), 0) = 1 AS do_not_drive,
                COALESCE(MAX(CASE WHEN r.park_outside  THEN 1 ELSE 0 END), 0) = 1 AS park_outside,
                COALESCE(MAX(CASE WHEN r.park_it       THEN 1 ELSE 0 END), 0) = 1 AS park_it,
                MAX(r.consequence_description)                      AS consequence,
                MAX(r.corrective_action)                            AS remedy,
                MIN(r.report_received_date)                         AS issued_at,
                'FLAT_FILE'                                         AS source
            FROM {UC}.silver_recall r
            JOIN exposed e ON e.campaign_number = r.campaign_number
            GROUP BY r.campaign_number
        """,
    },
}

# Hard guard — this notebook may only ever write to objects it owns by name.
assert all(t.startswith("fleetguard_") for t in SOURCES), "refusing: non-project table name"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load
# MAGIC
# MAGIC `COPY … FROM STDIN WITH (FORMAT csv)` rather than text format, because free-text
# MAGIC columns (`consequence`, `remedy`) contain tabs, newlines and backslashes that the
# MAGIC default text format would interpret as delimiters or escapes. Same class of bug as
# MAGIC I-012, where NHTSA narratives broke the Spark CSV reader's default quoting.

# COMMAND ----------


def to_csv_buffer(pdf, columns):
    """Serialise a pandas frame to an in-memory CSV that Postgres COPY accepts."""
    buf = io.StringIO()
    pdf[columns].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    return buf


timings = {}

with conn.cursor() as cur:
    for table, spec in SOURCES.items():
        cols = spec["columns"]
        key = spec["key"]
        collist = ", ".join(cols)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != key)

        pdf = spark.sql(spec["sql"]).toPandas()
        print(f"\n{table}: {len(pdf):,} source rows")

        cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{table}")
        before = cur.fetchone()[0]

        t0 = time.time()
        # Session-scoped staging table; invisible to other sessions, dropped on commit.
        cur.execute(
            f"CREATE TEMP TABLE stage_{table} "
            f"(LIKE {PG_SCHEMA}.{table} INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        with cur.copy(
            f"COPY stage_{table} ({collist}) FROM STDIN WITH (FORMAT csv, NULL '\\N')"
        ) as cp:
            cp.write(to_csv_buffer(pdf, cols).read())

        cur.execute(f"""
            INSERT INTO {PG_SCHEMA}.{table} ({collist})
            SELECT {collist} FROM stage_{table}
            ON CONFLICT ({key}) DO UPDATE SET {updates}
        """)
        affected = cur.rowcount
        elapsed = time.time() - t0

        cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{table}")
        after = cur.fetchone()[0]

        timings[table] = {
            "source_rows": len(pdf),
            "rows_before": before,
            "rows_after": after,
            "upserted": affected,
            "seconds": round(elapsed, 2),
        }
        print(
            f"  {before:,} -> {after:,} rows  ({affected:,} upserted in {elapsed:.1f}s"
            f" = {len(pdf) / max(elapsed, 0.01):,.0f} rows/s)"
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify before committing
# MAGIC
# MAGIC Row count must equal the source exactly. A short count means `ON CONFLICT` collapsed
# MAGIC rows that were supposed to be distinct — i.e. the key is wrong — which would look
# MAGIC like a successful load.

# COMMAND ----------

problems = []
for table, t in timings.items():
    ok = t["rows_after"] == t["source_rows"]
    print(
        f"  {table:<30} source={t['source_rows']:>7,}  loaded={t['rows_after']:>7,}  "
        f"{'OK' if ok else '*** MISMATCH ***'}"
    )
    if not ok:
        problems.append((table, t["source_rows"], t["rows_after"]))

if problems:
    conn.rollback()
    conn.close()
    raise SystemExit(f"ROLLED BACK — row-count mismatch: {problems}")

# COMMAND ----------

commit_wall_clock = None
try:
    commit_wall_clock = time.time()
    conn.commit()
    print(f"COMMITTED at epoch {commit_wall_clock:.3f} — CDF capture starts from here")
except Exception:
    conn.rollback()
    print("ROLLED BACK — shared schema untouched")
    raise
finally:
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Record the load for the CDF latency measurement
# MAGIC
# MAGIC The commit timestamp is the `t0` for the §8.3 capture-latency figure, which is still
# MAGIC *documented* (~15 s) rather than *measured*. Persisting it means the measurement can
# MAGIC be taken later against a real write instead of a synthetic one.

# COMMAND ----------

rows = [
    {
        "table_name": t,
        "source_rows": v["source_rows"],
        "rows_after": v["rows_after"],
        "upserted": v["upserted"],
        "load_seconds": float(v["seconds"]),
        "committed_at_epoch": float(commit_wall_clock),
        "committed_at": datetime.datetime.fromtimestamp(commit_wall_clock, tz=datetime.UTC),
    }
    for t, v in timings.items()
]
(
    spark.createDataFrame(rows)
    .write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"{UC}.ops_lakebase_load")
)
display(spark.table(f"{UC}.ops_lakebase_load"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC ```sql
# MAGIC SHOW TABLES IN bootcamp_students.bootcamp_cdc LIKE 'lb_fleetguard*';
# MAGIC ```
# MAGIC
# MAGIC All eleven already exist (I-044). What changes here is their **contents**: check
# MAGIC that `lb_fleetguard_vehicle_history` and `lb_fleetguard_recall_campaign_history` go
# MAGIC from 0 rows to the loaded counts, all with `_pg_change_type = 'insert'`.
# MAGIC
# MAGIC Then use the measured rows/s here to size the `gold_fleet_exposure` load (989,042
# MAGIC rows) before running it.
