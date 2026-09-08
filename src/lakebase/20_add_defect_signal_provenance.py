# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — provenance on `fleetguard_defect_signal`
# MAGIC
# MAGIC The agent is gaining its first **write action**: `open_defect_signal` lets it persist a
# MAGIC defect signal it judged worth tracking, which then appears in the console's Emerging tab
# MAGIC alongside the ones the batch detector found. Two very different origins in one table.
# MAGIC
# MAGIC ## Why an explicit `source` column rather than inferring it
# MAGIC
# MAGIC An agent-opened signal is already *distinguishable* without any schema change: the
# MAGIC detector-only columns (`max_z`, `run_len`, `series_key`, `as_of_month`) would be NULL,
# MAGIC because the agent did not run the z-score detector. It would be tempting to rely on that.
# MAGIC
# MAGIC **Don't.** That is a property which is true by accident, and this project has already been
# MAGIC bitten by exactly that shape — I-069 (a cost figure that was "safe" only because of
# MAGIC incidental JSON wrapping) and the audit-log CSV, where formula injection is prevented by a
# MAGIC side effect rather than a guard. A future detector that leaves `max_z` NULL, or an agent
# MAGIC that learns to populate it, silently breaks the inference. `source` says what happened.
# MAGIC
# MAGIC ## Columns
# MAGIC
# MAGIC - `source` — `'DETECTOR'` (default) or `'AGENT'`. The default classifies all 48 existing
# MAGIC   rows correctly with no backfill, because every row present today came from the detector.
# MAGIC - `opened_by` — the **real human** the agent acted for. The write executes in the FastAPI
# MAGIC   app under the caller's own OBO token (the agent's serving endpoint has no Postgres
# MAGIC   path), so this is a genuine identity, not a service principal.
# MAGIC - `rationale` — why the agent opened it. Free text, and the only place its reasoning is
# MAGIC   persisted.

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
CHECK_NAME = "fg_defect_signal_source_check"

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
# MAGIC ## Add the columns
# MAGIC
# MAGIC `ADD COLUMN IF NOT EXISTS` is valid Postgres and idempotent. The `NOT NULL DEFAULT` on
# MAGIC `source` is what backfills the existing rows in one statement.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        ALTER TABLE {PG_SCHEMA}.fleetguard_defect_signal
          ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'DETECTOR',
          ADD COLUMN IF NOT EXISTS opened_by  TEXT,
          ADD COLUMN IF NOT EXISTS rationale  TEXT
    """)
print("columns source / opened_by / rationale added (or already existed)")

# COMMAND ----------

with conn.cursor() as cur:
    # Postgres has no `ADD CONSTRAINT IF NOT EXISTS` (I-067) — idempotency is a manual
    # existence check against pg_constraint. `ADD COLUMN IF NOT EXISTS` above *is* valid;
    # the two DDL forms genuinely differ.
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = %(name)s AND conrelid = %(table)s::regclass",
        {"name": CHECK_NAME, "table": f"{PG_SCHEMA}.fleetguard_defect_signal"},
    )
    if cur.fetchone() is not None:
        print(f"constraint {CHECK_NAME} already exists — nothing to do")
    else:
        cur.execute(f"""
            ALTER TABLE {PG_SCHEMA}.fleetguard_defect_signal
            ADD CONSTRAINT {CHECK_NAME} CHECK (source IN ('DETECTOR', 'AGENT'))
        """)
        print(f"constraint {CHECK_NAME} added")
conn.commit()
print("committed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — every pre-existing row must be classified `DETECTOR`
# MAGIC
# MAGIC If this is wrong the Emerging tab would attribute the batch detector's work to the agent,
# MAGIC which is precisely the misattribution the column exists to prevent.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        f"SELECT source, COUNT(*) FROM {PG_SCHEMA}.fleetguard_defect_signal GROUP BY source"
    )
    by_source = dict(cur.fetchall())

print(f"rows by source: {by_source}")
assert "AGENT" not in by_source or by_source.get("AGENT", 0) == 0 or by_source.get("DETECTOR"), (
    f"unexpected source distribution before any agent write: {by_source}"
)
assert by_source.get("DETECTOR", 0) > 0, (
    f"expected the existing detector rows to be classified DETECTOR, got {by_source}"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove the CHECK actually rejects — configured is not enforced
# MAGIC
# MAGIC Same discipline as `16_`, `18_` and `19_`: a real write, inside a savepoint so the failed
# MAGIC statement does not poison the session, rolled back so the proof leaves nothing behind.

# COMMAND ----------

rejected = False
with conn.cursor() as cur:
    cur.execute("SAVEPOINT source_proof")
    try:
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
                (signal_id, component, status, source)
                VALUES (%s, %s, 'OPEN', %s)""",
            ("SIG-SOURCE-PROOF-DELETEME", "STEERING", "NOT_A_REAL_SOURCE"),
        )
    except psycopg.errors.CheckViolation:
        rejected = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT source_proof")

assert rejected, (
    "an invalid source value was NOT rejected — the CHECK is not enforcing anything, "
    "investigate before trusting it"
)
print("proof passed: an invalid source was rejected and rolled back")

# COMMAND ----------

# MAGIC %md
# MAGIC ## And prove the converse — a legitimate AGENT row is accepted
# MAGIC
# MAGIC A rejection-only proof would pass just as happily against a CHECK that forbade *every*
# MAGIC value, including the one the new feature depends on.

# COMMAND ----------

accepted = False
with conn.cursor() as cur:
    cur.execute("SAVEPOINT agent_proof")
    try:
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
                (signal_id, component, status, source, opened_by, rationale)
                VALUES (%s, %s, 'OPEN', 'AGENT', %s, %s)""",
            ("SIG-AGENT-PROOF-DELETEME", "STEERING", owner, "migration proof"),
        )
        accepted = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT agent_proof")

assert accepted, (
    "a valid AGENT row was rejected — the feature this migration exists for cannot work"
)
print("proof passed: an AGENT-sourced row is accepted (and was rolled back)")

conn.close()
