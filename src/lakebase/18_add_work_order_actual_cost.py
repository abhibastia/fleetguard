# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — `fleetguard_work_order.actual_cost`
# MAGIC
# MAGIC Adds a nullable `NUMERIC(10, 2)` column so whoever closes out a work order can log what
# MAGIC the repair actually cost (parts + labor). This replaces an earlier UI idea that applied
# MAGIC one flat assumed dollar figure to every vehicle regardless of what was actually wrong with
# MAGIC it — a steering-rack repair on a Class 8 tractor and a brake job on a pickup are not the
# MAGIC same cost, and a single multiplier can't represent that. Real, optionally-logged
# MAGIC per-work-order cost can be summed and broken down by component/depot instead — see
# MAGIC `docs/ARCHITECTURE.md` for the fuller reasoning (a counterfactual "cost avoided" figure was
# MAGIC deliberately rejected; this column only ever represents cost actually incurred).
# MAGIC
# MAGIC `REPLICA IDENTITY FULL` is already set on this table, so the new column flows through CDF
# MAGIC automatically — no new CDF work.

# COMMAND ----------

import os

# I-045 / I-068: psycopg[binary] 3.3.5 aborts on Databricks serverless (FIPS self-test
# failure), so the pure-Python impl is required *there* - but macOS has no system libpq, so
# forcing it unconditionally breaks local execution. Same conditional as
# db.py::_select_psycopg_impl.
if os.getenv("DATABRICKS_RUNTIME_VERSION"):
    os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
owner_token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
owner = w.current_user.me().user_name

conn = psycopg.connect(
    host=host, user=owner, password=owner_token, dbname=PG_DB, sslmode="require", autocommit=False
)
print(f"connected to {PG_DB} as {owner}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Add the column
# MAGIC
# MAGIC `ADD COLUMN IF NOT EXISTS` is valid Postgres (unlike `ADD CONSTRAINT IF NOT EXISTS`, which
# MAGIC needed a manual `pg_constraint` check in `16_add_work_order_status_check.py`) — no manual
# MAGIC existence check needed here.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        f"ALTER TABLE {PG_SCHEMA}.fleetguard_work_order "
        f"ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(10, 2)"
    )
print("column actual_cost added (or already existed)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Non-negative CHECK constraint
# MAGIC
# MAGIC A negative logged cost is always a data-entry mistake, never a real repair. `NULL` (not
# MAGIC yet logged) must remain valid — `CHECK` constraints in Postgres already treat `NULL` as
# MAGIC passing (the expression evaluates to unknown, not false), so no explicit `OR NULL` clause
# MAGIC is needed, but it's worth stating since that behavior is easy to assume is wrong.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = %(name)s AND conrelid = %(table)s::regclass",
        {"name": "fg_wo_actual_cost_nonnegative", "table": f"{PG_SCHEMA}.fleetguard_work_order"},
    )
    already_exists = cur.fetchone() is not None

    if already_exists:
        print("constraint fg_wo_actual_cost_nonnegative already exists — nothing to do")
    else:
        cur.execute(f"""
            ALTER TABLE {PG_SCHEMA}.fleetguard_work_order
            ADD CONSTRAINT fg_wo_actual_cost_nonnegative
            CHECK (actual_cost >= 0)
        """)
        print("constraint fg_wo_actual_cost_nonnegative added")
conn.commit()
print("committed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — a negative cost must actually be rejected
# MAGIC
# MAGIC Same discipline as `16_add_work_order_status_check.py`: exercised under a real write,
# MAGIC inside a savepoint so the failed statement doesn't abort the whole session, and rolled
# MAGIC back so this proof leaves no trace in the table.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"SELECT wo_id FROM {PG_SCHEMA}.fleetguard_work_order ORDER BY created_at LIMIT 1")
    row = cur.fetchone()

if row is None:
    print("no work orders exist yet to prove the constraint against — skipping the live check")
else:
    wo_id = row[0]
    rejected = False
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT check_proof")
        try:
            cur.execute(
                f"UPDATE {PG_SCHEMA}.fleetguard_work_order SET actual_cost = %s WHERE wo_id = %s",
                (-1, wo_id),
            )
        except psycopg.errors.CheckViolation:
            rejected = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT check_proof")
    assert rejected, (
        f"a negative actual_cost was NOT rejected for {wo_id} — the constraint is not "
        "actually enforcing anything, investigate before trusting it"
    )
    print(f"proof passed: a negative actual_cost on {wo_id} was rejected and rolled back")

conn.close()
