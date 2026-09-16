# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — the watchlist (`fleetguard_watchlist`)
# MAGIC
# MAGIC The agent's second write action, `watch_campaign`, lets it flag an NHTSA recall campaign
# MAGIC for the fleet safety team to keep an eye on — distinct from `open_defect_signal` (flags a
# MAGIC component/make/model *pattern*, not a specific campaign) and `propose_service_campaign`
# MAGIC (proposes dispatch, never launches). This is a pure bookmark: no fleet exposure is
# MAGIC computed, no work order is touched.
# MAGIC
# MAGIC Like `fleetguard_technician` (`17_create_technician_roster.py`), this is a standalone
# MAGIC numbered script rather than an addition to `08_create_remaining_tables.py` — that file has
# MAGIC been treated as "the original ten (+depot)" since Phase 5, and every table added since has
# MAGIC gotten its own script. CDF picks the new table up automatically once
# MAGIC `REPLICA IDENTITY FULL` is set — schema-level, no separate config (CLAUDE.md).
# MAGIC
# MAGIC ## Why a partial unique index on `(campaign_id, watched_by)`
# MAGIC
# MAGIC Same reasoning as `ux_fg_service_campaign_active` (`19_add_service_campaign_uniqueness.py`):
# MAGIC a `SELECT`-then-`INSERT` check in the handler is a TOCTOU race under concurrent requests,
# MAGIC so the index — not the handler's pre-check — is what actually serialises a double-click.
# MAGIC Scoped to `status = 'ACTIVE'` rather than unconditionally on the pair, so a dismissed
# MAGIC entry (should a dismiss workflow ever ship) would not block re-watching the same campaign
# MAGIC later. No dismiss endpoint exists yet — `status` defaults to `'ACTIVE'` and nothing else
# MAGIC writes it — but the predicate is written this way now so it does not need redesigning if
# MAGIC one does.
# MAGIC
# MAGIC No safety-check-for-existing-duplicates step, unlike `19`: this table is being created
# MAGIC fresh in this same script, so no prior rows can violate the index.

# COMMAND ----------

import os

if os.getenv("DATABRICKS_RUNTIME_VERSION"):
    os.environ.setdefault("PSYCOPG_IMPL", "python")  # I-045, see 16_add_work_order_status_check.py

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
INDEX = "ux_fg_watchlist_active"

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
_TABLES_TOUCHED = ("fleetguard_watchlist",)
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.fleetguard_watchlist (
            watchlist_id  TEXT PRIMARY KEY,
            campaign_id   TEXT NOT NULL,
            rationale     TEXT NOT NULL,
            watched_by    TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'ACTIVE',
            watched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_fg_watchlist_campaign
        ON {PG_SCHEMA}.fleetguard_watchlist (campaign_id)
    """)
    # `CREATE UNIQUE INDEX IF NOT EXISTS` is valid Postgres (unlike `ADD CONSTRAINT IF NOT
    # EXISTS` — see 16's comment on that distinction).
    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX}
        ON {PG_SCHEMA}.fleetguard_watchlist (campaign_id, watched_by)
        WHERE status = 'ACTIVE'
    """)
    cur.execute(f"ALTER TABLE {PG_SCHEMA}.fleetguard_watchlist REPLICA IDENTITY FULL")
conn.commit()
print("table + indexes created, REPLICA IDENTITY FULL set (CDF will pick this table up)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — a duplicate active watch must actually be rejected, and a dismissed one
# MAGIC must not block a rewatch
# MAGIC
# MAGIC Same discipline as `19_add_service_campaign_uniqueness.py`: exercised with real writes,
# MAGIC inside savepoints so a failed statement doesn't poison the session, rolled back so the
# MAGIC proof leaves nothing behind in a table meant to start empty.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"SELECT campaign_id FROM {PG_SCHEMA}.fleetguard_recall_campaign LIMIT 1")
    row = cur.fetchone()

if row is None:
    print("no recall campaign exists yet to prove against — skipping the live check")
else:
    campaign_id = row[0]

    with conn.cursor() as cur:
        cur.execute("SAVEPOINT watchlist_proof")
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_watchlist
                (watchlist_id, campaign_id, rationale, watched_by)
                VALUES (%s, %s, %s, %s)""",
            ("WATCH-PROOF-DELETEME-1", campaign_id, "uniqueness proof", owner),
        )

        rejected = False
        cur.execute("SAVEPOINT dupe_proof")
        try:
            cur.execute(
                f"""INSERT INTO {PG_SCHEMA}.fleetguard_watchlist
                    (watchlist_id, campaign_id, rationale, watched_by)
                    VALUES (%s, %s, %s, %s)""",
                ("WATCH-PROOF-DELETEME-2", campaign_id, "duplicate proof", owner),
            )
        except psycopg.errors.UniqueViolation:
            rejected = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT dupe_proof")
        assert rejected, (
            f"a second ACTIVE watch of {campaign_id} by {owner} was NOT rejected — the index "
            "is not enforcing anything, investigate before trusting it"
        )
        print(f"proof passed: a duplicate active watch of {campaign_id} was rejected")

        allowed = False
        cur.execute("SAVEPOINT dismissed_proof")
        try:
            cur.execute(
                f"""INSERT INTO {PG_SCHEMA}.fleetguard_watchlist
                    (watchlist_id, campaign_id, rationale, watched_by, status)
                    VALUES (%s, %s, %s, %s, 'DISMISSED')""",
                ("WATCH-PROOF-DELETEME-3", campaign_id, "converse proof", owner),
            )
            allowed = True
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT dismissed_proof")
        assert allowed, (
            "a DISMISSED row for an already-watched campaign was rejected — the index "
            "predicate is wider than 'ACTIVE' and would block legitimate rewatches"
        )
        print("proof passed: a non-ACTIVE row for the same campaign+user is still permitted")

        cur.execute("ROLLBACK TO SAVEPOINT watchlist_proof")

conn.rollback()  # end the proof transaction without committing any of the proof rows
print("proof transaction rolled back — table is empty, only the DDL from above is committed")

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        SELECT relreplident FROM pg_class
        WHERE oid = '{PG_SCHEMA}.fleetguard_watchlist'::regclass
    """)
    relreplident = cur.fetchone()[0]
assert relreplident == "f", f"expected REPLICA IDENTITY FULL ('f'), got {relreplident!r}"
print("verified: fleetguard_watchlist has REPLICA IDENTITY FULL")

conn.close()
