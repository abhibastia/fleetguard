# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — two indexes the query shapes already need (full-repo review finding)
# MAGIC
# MAGIC `08_create_remaining_tables.py`'s `INDEXES` list missed two access patterns that exist
# MAGIC in the app today, not hypothetically:
# MAGIC
# MAGIC - **`fleetguard_work_order (service_campaign_id)`** — the join key in
# MAGIC   `routers/approval.py`'s `list_service_campaigns` (`LEFT JOIN ... ON
# MAGIC   w.service_campaign_id = s.service_campaign_id`, aggregating the whole work-order table
# MAGIC   on every Launched-tab load) and the filter in `routers/work_orders.py`'s
# MAGIC   `GET /api/work-orders?service_campaign_id=` (the click-through from that tab). Both are
# MAGIC   sequential scans without it.
# MAGIC - **`fleetguard_audit_log (created_at DESC)`** — both audit routes
# MAGIC   (`routers/audit_log.py`) end in `ORDER BY created_at DESC LIMIT`, and the CSV export
# MAGIC   defaults to 5,000 rows and permits up to 20,000. The one index that already exists,
# MAGIC   `ix_fg_audit_entity (entity_type, entity_id)`, does not help the unfiltered default —
# MAGIC   the common case for a compliance export.
# MAGIC
# MAGIC Both tables grow roughly 200 rows per approval (one work order per exposed vehicle, one
# MAGIC audit row), so this degrades with exactly the action the demo exercises.

# COMMAND ----------

import os

# I-045: psycopg[binary] 3.3.5 aborts on Databricks serverless (FIPS self-test failure), so the
# pure-Python impl is required *there* — but macOS has no system libpq, so forcing it
# unconditionally breaks local execution. Same conditional as db.py::_select_psycopg_impl.
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

# Hard guard — this notebook may only ever touch objects it owns by name (shared schema).
_TABLES_TOUCHED = ("fleetguard_work_order", "fleetguard_audit_log")
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

INDEXES = [
    (
        "ix_fg_wo_service_campaign",
        f"CREATE INDEX IF NOT EXISTS ix_fg_wo_service_campaign "
        f"ON {PG_SCHEMA}.fleetguard_work_order (service_campaign_id)",
    ),
    (
        "ix_fg_audit_created",
        f"CREATE INDEX IF NOT EXISTS ix_fg_audit_created "
        f"ON {PG_SCHEMA}.fleetguard_audit_log (created_at DESC)",
    ),
]

with conn.cursor() as cur:
    for name, ddl in INDEXES:
        cur.execute(ddl)
        print(f"  ensured {name}")
conn.commit()
print(f"\n{len(INDEXES)} indexes ensured")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify — both indexes actually exist, not just "the statement ran"

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND indexname = ANY(%s) "
        "ORDER BY indexname",
        (PG_SCHEMA, [name for name, _ in INDEXES]),
    )
    present = {r[0] for r in cur.fetchall()}

missing = {name for name, _ in INDEXES} - present
assert not missing, f"index creation reported success but these are absent: {missing}"
print(f"verified present: {sorted(present)}")

conn.close()
