# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Lakebase Postgres inspection (READ ONLY)
# MAGIC
# MAGIC Runs before any DDL. The Postgres schema `bootcamp_students` is **shared with the
# MAGIC whole cohort**, and its CDF destination `bootcamp_students.bootcamp_cdc` is owned by
# MAGIC `zach@zachwilson.tech`. Use of both was explicitly authorised.
# MAGIC
# MAGIC The thing this checks for is name collision. CDF destination tables auto-suffix on
# MAGIC collision (`lb_x_history_1`), and **renaming a Postgres table orphans its history
# MAGIC table** — 105 of the 256 `lb_*` tables already in that schema are exactly such
# MAGIC orphans. So the `fleetguard_*` names have to be free before the first `CREATE`, not after.

# COMMAND ----------

# MAGIC %pip install --quiet psycopg[binary]
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import psycopg
from databricks.sdk import WorkspaceClient

PROJECT = "projects/summer-bootcamp-2026-v2"
BRANCH = f"{PROJECT}/branches/production"
ENDPOINT = f"{BRANCH}/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"

w = WorkspaceClient()
ep = w.postgres.get_endpoint(name=ENDPOINT)
host = ep.status.hosts.host
cred = w.postgres.generate_database_credential(name=ENDPOINT)
user = w.current_user.me().user_name

print(f"host : {host}")
print(f"user : {user}")

conn = psycopg.connect(
    host=host,
    user=user,
    password=cred.token,
    dbname=PG_DB,
    sslmode="require",
    autocommit=True,
)

# COMMAND ----------

FG_TABLES = [
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

with conn.cursor() as cur:
    cur.execute("SELECT current_database(), current_user, version()")
    db, cu, ver = cur.fetchone()
    print(f"connected to {db} as {cu}")
    print(f"{ver.split(',')[0]}")

    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s",
        (PG_SCHEMA,),
    )
    print(f"\ntables already in {PG_SCHEMA}: {cur.fetchone()[0]:,}")

    # THE check: are our names free?
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = ANY(%s) ORDER BY 1",
        (PG_SCHEMA, FG_TABLES),
    )
    taken = [r[0] for r in cur.fetchall()]
    if taken:
        print(f"\n*** COLLISION — these fleetguard_* names already exist: {taken}")
    else:
        print(f"\nall {len(FG_TABLES)} fleetguard_* names are FREE")

    # anything else already using the fg_ prefix?
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name LIKE 'fleetguard\\_%%' ORDER BY 1",
        (PG_SCHEMA,),
    )
    others = [r[0] for r in cur.fetchall()]
    print(f"other fleetguard_-prefixed tables in the schema: {others or 'none'}")

    # can we create? check schema privileges
    cur.execute("SELECT has_schema_privilege(%s, %s, 'CREATE')", (cu, PG_SCHEMA))
    print(f"\nCREATE privilege on {PG_SCHEMA}: {cur.fetchone()[0]}")

    cur.execute("SELECT current_setting('server_version_num')::int")
    vnum = cur.fetchone()[0]
    print(
        f"server_version_num: {vnum}  (CDF requires PG 16/17/18 -> "
        f"{'OK' if vnum >= 160000 else 'TOO OLD'})"
    )

# COMMAND ----------

conn.close()
print("connection closed — no DDL executed")
