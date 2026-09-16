# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 5 step 2: the remaining ten Lakebase tables
# MAGIC
# MAGIC The CDF round-trip is already proven on `fleetguard_depot` (I-038): exact
# MAGIC destination name, no `_1` collision suffix, all four change types, all five metadata
# MAGIC columns. This creates the other ten on the same pattern.
# MAGIC
# MAGIC **Safety properties, in a schema shared with ~296 students:**
# MAGIC
# MAGIC - **Idempotent** — `CREATE TABLE IF NOT EXISTS`, so a re-run is a no-op rather than
# MAGIC   an error. It reports created-vs-skipped so a partial previous run is visible.
# MAGIC - **Transactional** — `autocommit=False`. Postgres has transactional DDL, so all ten
# MAGIC   tables commit together or none do. A half-built schema is worse than none.
# MAGIC - **Name-guarded** — asserts every target starts with `fleetguard_`. Nothing else can
# MAGIC   be touched, and there is no `DROP`, `TRUNCATE`, `GRANT` or `ALTER SCHEMA` anywhere.
# MAGIC - **`REPLICA IDENTITY FULL` on every table** — a hard CDF prerequisite. Without it the
# MAGIC   WAL carries only the primary key on update/delete, so `update_preimage` is useless.
# MAGIC
# MAGIC Schemas map 1:1 to proposal §4.4, extended where the build has since learned
# MAGIC something — `match_basis` on exposure exists because variant matches outnumber exact
# MAGIC ones 3:1 (I-030), and `vin` is `VARCHAR(17)` because complaint VINs are 11-char
# MAGIC partials that must never be confused with fleet VINs (I-011).

# COMMAND ----------

import os

# See I-045. `psycopg[binary]` 3.3.5 aborts the kernel with a FIPS self-test failure on
# serverless. This job's environment is still cached from before that release, so it works
# today — but a rebuild would break it silently. Set the guard now, not after it fails.
os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402 - must follow the PSYCOPG_IMPL assignment above
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"

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

S = PG_SCHEMA

DDL = {
    # ---- fleet roster -------------------------------------------------------
    "fleetguard_vehicle": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_vehicle (
            vin                VARCHAR(17) PRIMARY KEY,
            depot_id           TEXT NOT NULL,
            segment            TEXT,
            make               TEXT NOT NULL,
            model              TEXT NOT NULL,
            model_year         INT,
            body_class         TEXT,
            gvwr_class         TEXT,
            manufacture_date   DATE,
            mileage            BIGINT,
            status             TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    # ---- proactive side: Model A output -------------------------------------
    "fleetguard_defect_signal": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_defect_signal (
            signal_id          TEXT PRIMARY KEY,
            cluster_id         TEXT,
            component          TEXT NOT NULL,
            make               TEXT,
            model              TEXT,
            model_year_min     INT,
            model_year_max     INT,
            confidence         NUMERIC(5,4),
            severity_score     NUMERIC(8,4),
            complaint_count    INT,
            injured_total      INT,
            deaths_total       INT,
            corroboration_rate NUMERIC(5,4),
            status             TEXT NOT NULL DEFAULT 'OPEN',
            opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    # ---- reactive side: campaigns from flat file or live API ----------------
    "fleetguard_recall_campaign": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_recall_campaign (
            campaign_id        TEXT PRIMARY KEY,
            nhtsa_number       TEXT NOT NULL,
            component          TEXT,
            make               TEXT,
            model              TEXT,
            model_year         INT,
            manufacture_start  DATE,
            manufacture_end    DATE,
            do_not_drive       BOOLEAN NOT NULL DEFAULT FALSE,
            park_outside       BOOLEAN NOT NULL DEFAULT FALSE,
            park_it            BOOLEAN NOT NULL DEFAULT FALSE,
            consequence        TEXT,
            remedy             TEXT,
            issued_at          TIMESTAMPTZ,
            source             TEXT NOT NULL DEFAULT 'FLAT_FILE',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    # ---- the join that drives the work queue --------------------------------
    # match_basis is not decorative: EXACT is the deterministic tier §7 guarantees,
    # MODEL_VARIANT is the residual tier Model B scores. Variants outnumber exact 3:1.
    "fleetguard_vehicle_exposure": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_vehicle_exposure (
            exposure_id        BIGSERIAL PRIMARY KEY,
            vin                VARCHAR(17) NOT NULL,
            campaign_id        TEXT,
            signal_id          TEXT,
            match_basis        TEXT NOT NULL,
            match_confidence   NUMERIC(5,4),
            matched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT exposure_has_a_source CHECK (campaign_id IS NOT NULL OR signal_id IS NOT NULL)
        )""",
    # ---- the gated action ---------------------------------------------------
    "fleetguard_service_campaign": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_service_campaign (
            service_campaign_id TEXT PRIMARY KEY,
            campaign_id        TEXT,
            signal_id          TEXT,
            title              TEXT NOT NULL,
            vehicle_count      INT NOT NULL DEFAULT 0,
            status             TEXT NOT NULL DEFAULT 'DRAFT',
            created_by         TEXT NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by        TEXT,
            approved_at        TIMESTAMPTZ,
            launched_at        TIMESTAMPTZ
        )""",
    "fleetguard_work_order": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_work_order (
            wo_id              TEXT PRIMARY KEY,
            service_campaign_id TEXT,
            vin                VARCHAR(17) NOT NULL,
            depot_id           TEXT NOT NULL,
            assigned_to        TEXT,
            due_date           DATE,
            status             TEXT NOT NULL DEFAULT 'OPEN',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at       TIMESTAMPTZ
        )""",
    # ---- agent audit trail --------------------------------------------------
    # This table plus fleetguard_defect_signal are what §8.3's table_update trigger
    # watches via lb_fleetguard_agent_action_history.
    "fleetguard_agent_action": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_agent_action (
            action_id          BIGSERIAL PRIMARY KEY,
            tool               TEXT NOT NULL,
            tool_input         JSONB,
            tool_output        JSONB,
            actor_principal    TEXT NOT NULL,
            on_behalf_of       TEXT,
            requires_approval  BOOLEAN NOT NULL DEFAULT FALSE,
            latency_ms         INT,
            input_tokens       INT,
            output_tokens      INT,
            trace_id           TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    # UNUSED as of 2026-09-16 (full-repo review finding): no router or
    # agent tool reads or writes this table — the live approval flow records its decision
    # directly on fleetguard_service_campaign/fleetguard_audit_log instead (routers/
    # approval.py). Left in the DDL rather than dropped: the table already exists live with
    # this script's own idempotency guarantee (CREATE TABLE IF NOT EXISTS), and removing the
    # entry here would desync this script's own "ten"/"eleven" counts throughout without
    # actually cleaning anything up in the workspace. Consider a real DROP TABLE (a separate,
    # deliberate migration) rather than editing this file further.
    "fleetguard_approval": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_approval (
            approval_id        BIGSERIAL PRIMARY KEY,
            action_id          BIGINT NOT NULL,
            service_campaign_id TEXT,
            decision           TEXT NOT NULL,
            rationale          TEXT,
            decided_by         TEXT NOT NULL,
            decided_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approval_decision_valid CHECK (decision IN ('APPROVE','REJECT'))
        )""",
    # ---- append-only ---------------------------------------------------------
    "fleetguard_audit_log": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_audit_log (
            audit_id           BIGSERIAL PRIMARY KEY,
            entity_type        TEXT NOT NULL,
            entity_id          TEXT NOT NULL,
            action             TEXT NOT NULL,
            actor_principal    TEXT NOT NULL,
            before_state       JSONB,
            after_state        JSONB,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    # ---- unauthenticated read surface (§5.2) --------------------------------
    # Pre-aggregated and masked so the public principal never touches a base table,
    # and the external page never waits on a SQL Warehouse cold start.
    #
    # UNUSED as of 2026-09-16, same finding as fleetguard_approval above: the Evidence tab
    # (README, docs/API.md's /api/evidence) sources from the published backtest result
    # instead of this table. Left in place for the same reason.
    "fleetguard_public_summary": f"""
        CREATE TABLE IF NOT EXISTS {S}.fleetguard_public_summary (
            metric_key         TEXT PRIMARY KEY,
            metric_value       NUMERIC,
            metric_text        TEXT,
            unit               TEXT,
            computed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
}

INDEXES = [
    f"CREATE INDEX IF NOT EXISTS ix_fg_vehicle_depot     ON {S}.fleetguard_vehicle (depot_id)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_vehicle_mmy       ON {S}.fleetguard_vehicle (make, model, model_year)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_exposure_vin      ON {S}.fleetguard_vehicle_exposure (vin)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_exposure_campaign ON {S}.fleetguard_vehicle_exposure (campaign_id)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_wo_depot_status   ON {S}.fleetguard_work_order (depot_id, status)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_action_created    ON {S}.fleetguard_agent_action (created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_audit_entity      ON {S}.fleetguard_audit_log (entity_type, entity_id)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_campaign_parkit   ON {S}.fleetguard_recall_campaign (park_it) WHERE park_it",
]

# Hard guard — this notebook may only ever create objects it owns by name.
assert all(t.startswith("fleetguard_") for t in DDL), "refusing: non-project table name"
print(f"{len(DDL)} tables to ensure, {len(INDEXES)} indexes")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create — idempotent, all inside one transaction

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
        "AND table_name LIKE %s",
        (S, "fleetguard%"),
    )
    before = {r[0] for r in cur.fetchall()}
    print(f"already present: {sorted(before) or 'none'}\n")

    for name, ddl in DDL.items():
        cur.execute(ddl)
        # REPLICA IDENTITY FULL is idempotent and required for CDF on every table.
        cur.execute(f"ALTER TABLE {S}.{name} REPLICA IDENTITY FULL")
        print(f"  {'skipped (exists)' if name in before else 'CREATED':<18} {name}")

    for stmt in INDEXES:
        cur.execute(stmt)
    print(f"\n{len(INDEXES)} indexes ensured")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify before committing
# MAGIC
# MAGIC Every table must show `relreplident = 'f'`. Anything else means CDF would capture
# MAGIC updates and deletes without their prior row, and `update_preimage` would be useless.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        """
        SELECT c.relname, c.relreplident
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname LIKE 'fleetguard%%' AND c.relkind = 'r'
        ORDER BY c.relname
    """,
        (S,),
    )
    rows = cur.fetchall()

bad = [(n, i) for n, i in rows if i != "f"]
for n, i in rows:
    print(f"  {n:<32} relreplident={i!r} {'OK' if i == 'f' else '*** NOT FULL ***'}")

print(f"\ntables: {len(rows)} (expect 11 including fleetguard_depot)")
if bad:
    conn.rollback()
    conn.close()
    raise SystemExit(f"ROLLED BACK — REPLICA IDENTITY not FULL on: {bad}")

# COMMAND ----------

try:
    conn.commit()
    print("COMMITTED — CDF will now capture all 11 tables")
except Exception:
    conn.rollback()
    print("ROLLED BACK — shared schema untouched")
    raise
finally:
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC CDF replicates the DDL, so all ten destination tables appear as soon as the
# MAGIC `CREATE TABLE`s commit — no row has to be written first. (An earlier note here
# MAGIC claimed the opposite; measured 2026-09-01, see I-044.) Verify with:
# MAGIC
# MAGIC ```sql
# MAGIC SHOW TABLES IN bootcamp_students.bootcamp_cdc LIKE 'lb_fleetguard*';
# MAGIC ```
# MAGIC
# MAGIC Expect eleven, none carrying a `_1` suffix.
