# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — CHECK constraint on `fleetguard_work_order.status`
# MAGIC
# MAGIC `fleetguard_work_order.status` has been a bare `TEXT` column since it was created
# MAGIC (`08_create_remaining_tables.py`) — no CHECK, no enum, only `'OPEN'` ever written (the
# MAGIC hardcoded value in `approval.py`'s `approve_campaign`). This adds the same kind of
# MAGIC constraint `fleetguard_approval.decision` already has, ahead of the new
# MAGIC `PATCH /work-orders/{wo_id}` endpoint that will write `IN_PROGRESS`/`COMPLETED`/
# MAGIC `CANCELLED` for the first time.
# MAGIC
# MAGIC `REPLICA IDENTITY FULL` is already set on this table (every table gets it in the
# MAGIC original setup script), so status changes already flow through CDF — this migration adds
# MAGIC no new CDF work.

# COMMAND ----------

import os

# I-045: psycopg[binary] 3.3.5 aborts on Databricks serverless (FIPS self-test failure), so
# the pure-Python impl is required *there* - but macOS has no system libpq, so forcing it
# unconditionally (as 15_enable_depot_rls.py does, having only ever run on Databricks compute)
# breaks local execution. Same conditional as db.py::_select_psycopg_impl.
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
# MAGIC ## Safety check first — every existing row must already satisfy the constraint
# MAGIC
# MAGIC Confirmed by reading `approval.py`: the only `INSERT` into this table hardcodes
# MAGIC `status='OPEN'`. Checked here rather than assumed, so the migration fails loudly instead
# MAGIC of the `ALTER TABLE` failing loudly for the same reason a step later.

# COMMAND ----------

ALLOWED = ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED")

with conn.cursor() as cur:
    # psycopg3 binds parameters server-side, so a tuple passed for `IN %s` does not expand
    # the way it would under psycopg2's client-side substitution — `= ANY(%(allowed)s)` with
    # a list is the correct psycopg3 idiom for "one of these values".
    cur.execute(
        f"SELECT status, COUNT(*) FROM {PG_SCHEMA}.fleetguard_work_order "
        f"WHERE NOT (status = ANY(%(allowed)s)) GROUP BY status",
        {"allowed": list(ALLOWED)},
    )
    offending = cur.fetchall()
assert not offending, (
    f"existing rows outside {ALLOWED} would violate the new constraint: {offending} — "
    "investigate before adding it, do not widen the allowed set just to make this pass"
)
print(f"safety check passed: every existing row's status is already in {ALLOWED}")

# COMMAND ----------

with conn.cursor() as cur:
    # Postgres has no `ADD CONSTRAINT IF NOT EXISTS` (unlike `ADD COLUMN IF NOT EXISTS`) —
    # idempotency has to be a manual existence check against pg_constraint.
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = %(name)s AND conrelid = %(table)s::regclass",
        {"name": "fg_wo_status_check", "table": f"{PG_SCHEMA}.fleetguard_work_order"},
    )
    already_exists = cur.fetchone() is not None

    if already_exists:
        print("constraint fg_wo_status_check already exists — nothing to do")
    else:
        cur.execute(f"""
            ALTER TABLE {PG_SCHEMA}.fleetguard_work_order
            ADD CONSTRAINT fg_wo_status_check
            CHECK (status IN ('OPEN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
        """)
        print("constraint fg_wo_status_check added")
conn.commit()
print("committed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — an invalid status must actually be rejected, not just configured
# MAGIC
# MAGIC Same discipline as `15_enable_depot_rls.py`: exercised under a real write, inside a
# MAGIC savepoint so the failed statement doesn't abort the whole session, and rolled back so
# MAGIC this proof leaves no trace in the table.

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
                f"UPDATE {PG_SCHEMA}.fleetguard_work_order SET status = %s WHERE wo_id = %s",
                ("NOT_A_REAL_STATUS", wo_id),
            )
        except psycopg.errors.CheckViolation:
            rejected = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT check_proof")
    assert rejected, (
        f"an invalid status was NOT rejected for {wo_id} — the constraint is not actually "
        "enforcing anything, investigate before trusting it"
    )
    print(f"proof passed: an invalid status on {wo_id} was rejected and rolled back")

conn.close()
