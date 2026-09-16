# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — one active service campaign per recall (I-063)
# MAGIC
# MAGIC `approve_campaign` has never checked whether a recall already has a live service
# MAGIC campaign. Approving twice produced two `fleetguard_service_campaign` rows and a full
# MAGIC duplicate set of work orders — one per exposed vehicle, again. Found in the 2026-09-02
# MAGIC repo review and deliberately left open because "block it / return the existing one /
# MAGIC something else" is a product decision; decided 2026-09-07: **block it, 409**.
# MAGIC
# MAGIC **This is not hypothetical.** The 2026-09-04 test-data cleanup found six campaigns for
# MAGIC recall `17V629000`, several sharing an identical auto-generated title minutes apart —
# MAGIC repeated approvals during manual testing, exactly this bug leaving footprints.
# MAGIC
# MAGIC ## Why an index and not just a check in the handler
# MAGIC
# MAGIC The case being defended against is a **double-click**: two requests milliseconds apart.
# MAGIC A `SELECT`-then-`INSERT` in the handler is a textbook TOCTOU race — both requests read
# MAGIC "nothing there", both insert. Under Postgres READ COMMITTED that is not merely possible,
# MAGIC it is the *likely* interleaving for simultaneous requests. Only a database-level
# MAGIC constraint actually serialises them; the handler's own check exists to produce a good
# MAGIC error message in the common case, not to enforce the rule.
# MAGIC
# MAGIC ## Why *partial*, on `status = 'LAUNCHED'`
# MAGIC
# MAGIC A cancelled campaign must not block re-launching the same recall later — a real
# MAGIC workflow (launch, cancel because the remedy changed, relaunch). Scoping the uniqueness
# MAGIC to live rows expresses "one *active* campaign per recall" rather than "one ever".
# MAGIC `status` is a bare `TEXT DEFAULT 'DRAFT'` with no CHECK, and only `'LAUNCHED'` has ever
# MAGIC been written (`approval.py` hardcodes it), so the predicate matches reality today and
# MAGIC stays correct if DRAFT/CANCELLED rows appear later.
# MAGIC
# MAGIC `campaign_id` is nullable — a campaign can be raised from a `signal_id` instead. Postgres
# MAGIC unique indexes permit repeated NULLs, so signal-driven campaigns are unaffected, which is
# MAGIC the behaviour we want rather than something to work around.

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
INDEX = "ux_fg_service_campaign_active"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
owner_token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
owner = w.current_user.me().user_name

conn = psycopg.connect(
    host=host, user=owner, password=owner_token, dbname=PG_DB, sslmode="require", autocommit=False
)
print(f"connected to {PG_DB} as {owner}")

# COMMAND ----------

# Hard guard — this notebook may only ever touch objects it owns by name (shared schema).
_TABLES_TOUCHED = ("fleetguard_service_campaign",)
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Safety check first — the index cannot be created over existing duplicates
# MAGIC
# MAGIC If this fails, do **not** widen the predicate to make it pass. Duplicates mean real
# MAGIC double-approvals are already in the table, and which one is authoritative (and what
# MAGIC happens to the other's work orders) is a data decision, not a migration detail.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        SELECT campaign_id, COUNT(*) AS n
        FROM {PG_SCHEMA}.fleetguard_service_campaign
        WHERE status = 'LAUNCHED' AND campaign_id IS NOT NULL
        GROUP BY campaign_id
        HAVING COUNT(*) > 1
        ORDER BY n DESC
    """)
    dupes = cur.fetchall()

assert not dupes, (
    f"existing duplicate active campaigns would block the index: {dupes} — decide which row "
    "is authoritative and what happens to the other's work orders before re-running"
)
print("safety check passed: no recall currently has more than one LAUNCHED campaign")

# COMMAND ----------

with conn.cursor() as cur:
    # `CREATE UNIQUE INDEX IF NOT EXISTS` *is* valid Postgres — unlike `ADD CONSTRAINT IF NOT
    # EXISTS`, which is not and needed a manual pg_constraint check in
    # 16_add_work_order_status_check.py (I-067). Different DDL, different rules.
    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX}
        ON {PG_SCHEMA}.fleetguard_service_campaign (campaign_id)
        WHERE status = 'LAUNCHED'
    """)
conn.commit()
print(f"index {INDEX} created (or already existed)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — a duplicate must actually be rejected, not merely configured
# MAGIC
# MAGIC Same discipline as `16_add_work_order_status_check.py` and
# MAGIC `18_add_work_order_actual_cost.py`: exercised with a real write, inside a savepoint so
# MAGIC the failed statement doesn't poison the session, and rolled back so the proof leaves
# MAGIC nothing behind.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        SELECT campaign_id FROM {PG_SCHEMA}.fleetguard_service_campaign
        WHERE status = 'LAUNCHED' AND campaign_id IS NOT NULL LIMIT 1
    """)
    row = cur.fetchone()

if row is None:
    print("no launched campaign exists yet to prove against — skipping the live check")
else:
    existing_campaign_id = row[0]
    rejected = False
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT dupe_proof")
        try:
            cur.execute(
                f"""INSERT INTO {PG_SCHEMA}.fleetguard_service_campaign
                    (service_campaign_id, campaign_id, title, vehicle_count, status, created_by)
                    VALUES (%s, %s, %s, 0, 'LAUNCHED', %s)""",
                ("SC-DUPE-PROOF-DELETEME", existing_campaign_id, "uniqueness proof", owner),
            )
        except psycopg.errors.UniqueViolation:
            rejected = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT dupe_proof")
    assert rejected, (
        f"a second LAUNCHED campaign for {existing_campaign_id} was NOT rejected — the index "
        "is not enforcing anything, investigate before trusting it"
    )
    print(f"proof passed: a duplicate LAUNCHED campaign for {existing_campaign_id} was rejected")

# COMMAND ----------

# MAGIC %md
# MAGIC ## And prove the *converse* — a cancelled campaign must not block a relaunch
# MAGIC
# MAGIC The partial predicate is the whole design; a test that only proves rejection would pass
# MAGIC just as happily against a plain (wrong) unique index on `campaign_id`.

# COMMAND ----------

if row is not None:
    allowed = False
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT relaunch_proof")
        try:
            cur.execute(
                f"""INSERT INTO {PG_SCHEMA}.fleetguard_service_campaign
                    (service_campaign_id, campaign_id, title, vehicle_count, status, created_by)
                    VALUES (%s, %s, %s, 0, 'CANCELLED', %s)""",
                ("SC-CANCELLED-PROOF-DELETEME", existing_campaign_id, "relaunch proof", owner),
            )
            allowed = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT relaunch_proof")
    assert allowed, (
        "a CANCELLED row for an already-launched recall was rejected — the index predicate is "
        "wider than 'LAUNCHED' and would block legitimate relaunches"
    )
    print("proof passed: a non-LAUNCHED row for the same recall is still permitted")

conn.close()
