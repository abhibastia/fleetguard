# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 5 step 1: ONE table, then verify CDF
# MAGIC
# MAGIC Creates **only** `fleetguard_depot` — the smallest and least consequential of the
# MAGIC eleven — and writes rows to it. The other ten are not created until the CDF
# MAGIC round-trip is confirmed working.
# MAGIC
# MAGIC **Why one first.** The Postgres schema `bootcamp_students` is shared with the cohort,
# MAGIC and its CDF destination `bootcamp_students.bootcamp_cdc` (owned by
# MAGIC `zach@zachwilson.tech`, use authorised) already holds 354 tables of which **105 are
# MAGIC `_1`/`_2` orphans**. CDF auto-suffixes on collision *silently* rather than erroring,
# MAGIC and renaming a Postgres table orphans its history table. So the failure mode is not
# MAGIC an error message — it is a wrongly-named table discovered a week later. Ten minutes
# MAGIC spent proving the round-trip on one table is cheap against that.
# MAGIC
# MAGIC Dependencies come from the job environment spec, **not** `%pip` +
# MAGIC `dbutils.library.restartPython()` — the latter kills the kernel in a job task
# MAGIC (`Fatal error: The Python kernel is unresponsive`).

# COMMAND ----------

import psycopg
from databricks.sdk import WorkspaceClient

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
TABLE = "fleetguard_depot"

ALL_ELEVEN = [
    "fleetguard_vehicle",
    "fleetguard_depot",
    "fleetguard_defect_signal",
    "fleetguard_recall_campaign",
    "fleetguard_vehicle_exposure",
    "fleetguard_service_campaign",
    "fleetguard_work_order",
    "fleetguard_agent_action",
    "fleetguard_approval",
    "fleetguard_audit_log",
    "fleetguard_public_summary",
]

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
# NOTE: the parameter is `endpoint=`, not `name=`.
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name
print(f"host: {host}\nuser: {user}")

# autocommit=False deliberately. Postgres has transactional DDL, so CREATE + ALTER +
# INSERT/UPDATE/DELETE all commit or none do. In a schema shared with ~296 students a
# half-applied change is worse than no change.
conn = psycopg.connect(
    host=host,
    user=user,
    password=token,
    dbname=PG_DB,
    sslmode="require",
    autocommit=False,
)

# Hard guard: this notebook may only ever touch objects it owns by name.
assert TABLE.startswith("fleetguard_"), f"refusing to operate on non-project table {TABLE}"
assert all(x.startswith("fleetguard_") for x in ALL_ELEVEN)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Pre-flight — names free, privileges present, version supported

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        "SELECT current_database(), current_user, current_setting('server_version_num')::int"
    )
    db, cu, vnum = cur.fetchone()
    print(f"connected to {db} as {cu}")
    print(
        f"server_version_num {vnum} — CDF needs PG 16/17/18: "
        f"{'OK' if vnum >= 160000 else 'TOO OLD'}"
    )

    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (PG_SCHEMA,)
    )
    print(f"tables already in {PG_SCHEMA}: {cur.fetchone()[0]:,}")

    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name = ANY(%s) ORDER BY 1",
        (PG_SCHEMA, ALL_ELEVEN),
    )
    taken = [r[0] for r in cur.fetchall()]
    print(f"fleetguard_* names already taken: {taken or 'NONE — all 11 free'}")

    cur.execute("SELECT has_schema_privilege(%s, %s, 'CREATE')", (cu, PG_SCHEMA))
    can_create = cur.fetchone()[0]
    print(f"CREATE privilege on {PG_SCHEMA}: {can_create}")

if TABLE in taken:
    raise SystemExit(f"ABORT: {TABLE} already exists — do not clobber a shared schema")
if not can_create:
    raise SystemExit(f"ABORT: no CREATE privilege on {PG_SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Create the one table
# MAGIC
# MAGIC `REPLICA IDENTITY FULL` is a hard CDF prerequisite — without it the WAL carries only
# MAGIC the primary key on update/delete, so `update_preimage` rows would be useless.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        CREATE TABLE {PG_SCHEMA}.{TABLE} (
            depot_id           TEXT PRIMARY KEY,
            depot_name         TEXT NOT NULL,
            region             TEXT NOT NULL,
            city               TEXT,
            state              TEXT,
            manager_principal  TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute(f"ALTER TABLE {PG_SCHEMA}.{TABLE} REPLICA IDENTITY FULL")
    cur.execute(
        f"COMMENT ON TABLE {PG_SCHEMA}.{TABLE} IS "
        f"'FleetGuard depot registry. CDF -> bootcamp_students.bootcamp_cdc.lb_{TABLE}_history'"
    )
    print(f"created {PG_SCHEMA}.{TABLE} with REPLICA IDENTITY FULL")

    cur.execute(
        """
        SELECT c.relreplident FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """,
        (PG_SCHEMA, TABLE),
    )
    ident = cur.fetchone()[0]
    print(f"relreplident = {ident!r}  ('f' = FULL, which is what CDF requires)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Exercise all three change types
# MAGIC
# MAGIC Insert, update and delete, so the history table can be checked for
# MAGIC `insert` / `update_preimage` / `update_postimage` / `delete` rather than just inserts.

# COMMAND ----------

depots = spark.sql("""
    SELECT depot_id, depot_name, region, city, state, manager_principal
    FROM bootcamp_students.fleetguard.gold_fleet_depot ORDER BY depot_id
""").collect()

with conn.cursor() as cur:
    cur.executemany(
        f"INSERT INTO {PG_SCHEMA}.{TABLE} "
        "(depot_id, depot_name, region, city, state, manager_principal) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        [
            (d.depot_id, d.depot_name, d.region, d.city, d.state, d.manager_principal)
            for d in depots
        ],
    )
    print(f"inserted {len(depots)} depots")

    cur.execute(
        f"UPDATE {PG_SCHEMA}.{TABLE} SET depot_name = depot_name || ' (renamed)', "
        "updated_at = now() WHERE depot_id = %s",
        ("DEP-001",),
    )
    print("updated DEP-001  -> should yield update_preimage + update_postimage")

    cur.execute(f"DELETE FROM {PG_SCHEMA}.{TABLE} WHERE depot_id = %s", ("DEP-060",))
    print("deleted DEP-060  -> should yield a delete row")

    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE}")
    print(f"rows now in Postgres: {cur.fetchone()[0]}  (expect {len(depots) - 1})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Commit — or leave no trace
# MAGIC
# MAGIC Everything above ran inside one transaction. Nothing is visible to CDF, to the
# MAGIC shared schema, or to any other user until this cell commits. If any earlier cell
# MAGIC raised, run the rollback below instead and the shared schema is exactly as it was.

# COMMAND ----------

try:
    conn.commit()
    print("COMMITTED — fleetguard_depot now exists and CDF will pick it up")
except Exception:
    conn.rollback()
    print("ROLLED BACK — shared schema untouched")
    raise
finally:
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC To abandon instead, before committing:
# MAGIC ```python
# MAGIC conn.rollback(); conn.close()   # leaves the shared schema byte-identical
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. What to check next
# MAGIC
# MAGIC CDF flushes roughly every 15 seconds. The verification query — run separately so
# MAGIC this notebook stays a pure writer:
# MAGIC
# MAGIC ```sql
# MAGIC SELECT _pg_change_type, COUNT(*)
# MAGIC FROM bootcamp_students.bootcamp_cdc.lb_fleetguard_depot_history
# MAGIC GROUP BY 1;
# MAGIC ```
# MAGIC
# MAGIC Expect 60 `insert`, 1 `update_preimage`, 1 `update_postimage`, 1 `delete`.
# MAGIC **Confirm the table name has no `_1` suffix** — that would mean a silent collision.
