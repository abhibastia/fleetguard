# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — referential integrity for the Lakebase schema
# MAGIC
# MAGIC Until now this schema had **no foreign keys at all** — `pg_constraint` held only `p`
# MAGIC (primary key), `n` (not-null) and `c` (check) rows, and every cross-table relationship
# MAGIC was enforced by application code. That was a real policy, not an oversight, but it was
# MAGIC only ever stated in a code comment
# MAGIC (`app/backend/fleetguard_api/agent_actions.py`: *"app-checked, not DB-enforced"*) and
# MAGIC one row of `ARCHITECTURE.md` §7's invariant table. It was never argued anywhere, and
# MAGIC nothing tested it.
# MAGIC
# MAGIC ## Why reverse it
# MAGIC
# MAGIC The app checks are good and they stay. What they cannot cover is everything that
# MAGIC writes to Postgres *without going through the app* — the bulk loaders
# MAGIC (`10_load_reference_from_gold.py`, `13_load_exposure.py`, `14_load_signals.py`), the
# MAGIC seed script, and any future migration. `13_` already hand-rolls a "ghost VIN" check
# MAGIC and rolls back if exposure references a VIN the roster does not have, which is a
# MAGIC foreign key written out longhand, once, in one loader. A constraint is that check
# MAGIC applied to every writer for free, including the ones not written yet.
# MAGIC
# MAGIC ## Measured before designing, not after
# MAGIC
# MAGIC Every one of the 14 candidate relationships was scanned against live Postgres on
# MAGIC 2026-09-20 and **all 14 returned zero orphans**. So there is no `NOT VALID` /
# MAGIC `VALIDATE CONSTRAINT` two-step here and no data cleanup: the constraints go on
# MAGIC validated immediately. The pre-flight scan below stays anyway — if this notebook is
# MAGIC ever re-run against dirtier data it must fail loudly and name the rows, rather than
# MAGIC succeed by having been written on a good day.
# MAGIC
# MAGIC ## Three choices worth stating
# MAGIC
# MAGIC 1. **No `CASCADE` anywhere.** This schema is append-and-audit. A cascade would let one
# MAGIC    parent delete silently remove audited history, which is precisely what the audit
# MAGIC    log exists to prevent. Deletes are `RESTRICT`, except one `SET NULL`.
# MAGIC 2. **`work_order.assigned_to` → technician is `SET NULL`.** Retiring a technician
# MAGIC    should unassign their open work, not block the retirement and not delete the work.
# MAGIC 3. **The FK is a floor under the app check, never a replacement.**
# MAGIC    `routers/work_orders.py` validates that an assignee is on the roster *and* belongs
# MAGIC    to the work order's own depot *and* is active — strictly stronger than any FK, and
# MAGIC    its comment calling that "not just a foreign-key-shaped decoration" stays true.
# MAGIC
# MAGIC ## Also fixes a defect found while writing this — see I-107
# MAGIC
# MAGIC `fleetguard_depot_assignment` was created by `15_enable_depot_rls.py` **without**
# MAGIC `REPLICA IDENTITY FULL`, unlike every sibling table, and consequently has **no CDF
# MAGIC history table at all**. Repaired here because this notebook is already the one
# MAGIC touching that table's DDL.

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
_TABLES_TOUCHED = (
    "fleetguard_vehicle",
    "fleetguard_depot",
    "fleetguard_depot_assignment",
    "fleetguard_technician",
    "fleetguard_recall_campaign",
    "fleetguard_defect_signal",
    "fleetguard_vehicle_exposure",
    "fleetguard_service_campaign",
    "fleetguard_work_order",
    "fleetguard_watchlist",
    "fleetguard_agent_action",
    "fleetguard_approval",
)
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

# MAGIC %md
# MAGIC ## The relationships
# MAGIC
# MAGIC `(constraint_name, child_table, child_column, parent_table, parent_column, on_delete)`.
# MAGIC Ordered parents-before-children only for readability — Postgres does not care, and all
# MAGIC of this commits in one transaction.

# COMMAND ----------

FKS = [
    # --- everything that belongs to a depot -------------------------------------------
    (
        "fk_fg_vehicle_depot",
        "fleetguard_vehicle",
        "depot_id",
        "fleetguard_depot",
        "depot_id",
        "RESTRICT",
    ),
    (
        "fk_fg_technician_depot",
        "fleetguard_technician",
        "depot_id",
        "fleetguard_depot",
        "depot_id",
        "RESTRICT",
    ),
    (
        "fk_fg_depot_assignment_depot",
        "fleetguard_depot_assignment",
        "depot_id",
        "fleetguard_depot",
        "depot_id",
        "RESTRICT",
    ),
    (
        "fk_fg_wo_depot",
        "fleetguard_work_order",
        "depot_id",
        "fleetguard_depot",
        "depot_id",
        "RESTRICT",
    ),
    # --- exposure: the join that drives the whole work queue ---------------------------
    # `13_load_exposure.py` already checks both of these by hand and rolls back on a miss.
    (
        "fk_fg_exposure_vin",
        "fleetguard_vehicle_exposure",
        "vin",
        "fleetguard_vehicle",
        "vin",
        "RESTRICT",
    ),
    (
        "fk_fg_exposure_campaign",
        "fleetguard_vehicle_exposure",
        "campaign_id",
        "fleetguard_recall_campaign",
        "campaign_id",
        "RESTRICT",
    ),
    (
        "fk_fg_exposure_signal",
        "fleetguard_vehicle_exposure",
        "signal_id",
        "fleetguard_defect_signal",
        "signal_id",
        "RESTRICT",
    ),
    # --- the gated write path ----------------------------------------------------------
    (
        "fk_fg_sc_campaign",
        "fleetguard_service_campaign",
        "campaign_id",
        "fleetguard_recall_campaign",
        "campaign_id",
        "RESTRICT",
    ),
    (
        "fk_fg_sc_signal",
        "fleetguard_service_campaign",
        "signal_id",
        "fleetguard_defect_signal",
        "signal_id",
        "RESTRICT",
    ),
    (
        "fk_fg_wo_service_campaign",
        "fleetguard_work_order",
        "service_campaign_id",
        "fleetguard_service_campaign",
        "service_campaign_id",
        "RESTRICT",
    ),
    ("fk_fg_wo_vin", "fleetguard_work_order", "vin", "fleetguard_vehicle", "vin", "RESTRICT"),
    # The one SET NULL: retiring a technician unassigns their work, it does not delete it
    # and is not blocked by it.
    (
        "fk_fg_wo_assigned_to",
        "fleetguard_work_order",
        "assigned_to",
        "fleetguard_technician",
        "technician_id",
        "SET NULL",
    ),
    # --- agent surfaces -----------------------------------------------------------------
    (
        "fk_fg_watchlist_campaign",
        "fleetguard_watchlist",
        "campaign_id",
        "fleetguard_recall_campaign",
        "campaign_id",
        "RESTRICT",
    ),
    (
        "fk_fg_approval_action",
        "fleetguard_approval",
        "action_id",
        "fleetguard_agent_action",
        "action_id",
        "RESTRICT",
    ),
]

# An unindexed FK child column makes every parent DELETE a sequential scan of the child.
# Five of the fourteen child columns have no index today; the rest are already covered by
# 08_create_remaining_tables.py and 23_add_missing_indexes.py.
CHILD_INDEXES = [
    ("ix_fg_wo_vin", "fleetguard_work_order", "vin"),
    ("ix_fg_wo_assigned_to", "fleetguard_work_order", "assigned_to"),
    ("ix_fg_exposure_signal", "fleetguard_vehicle_exposure", "signal_id"),
    ("ix_fg_sc_signal", "fleetguard_service_campaign", "signal_id"),
    ("ix_fg_approval_action", "fleetguard_approval", "action_id"),
]

print(f"{len(FKS)} foreign keys, {len(CHILD_INDEXES)} supporting indexes")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pre-flight: scan for orphans before touching anything
# MAGIC
# MAGIC Measured clean on 2026-09-20 (all 14 at zero). This must stay in the notebook so a
# MAGIC later re-run against different data fails with a readable message and a row count
# MAGIC instead of a bare Postgres error — and, per the standing rule in this directory,
# MAGIC **do not drop a constraint from the list just to make this pass.** An orphan means a
# MAGIC loader is wrong.

# COMMAND ----------

orphans = {}
with conn.cursor() as cur:
    for name, child, col, parent, pcol, _ in FKS:
        cur.execute(
            f"""
            SELECT COUNT(*) FROM {PG_SCHEMA}.{child} c
            LEFT JOIN {PG_SCHEMA}.{parent} p ON p.{pcol} = c.{col}
            WHERE c.{col} IS NOT NULL AND p.{pcol} IS NULL
            """
        )
        n = cur.fetchone()[0]
        if n:
            orphans[name] = n
        print(f"  {'ORPHANS' if n else 'clean  '} {n:>7,}  {child}.{col} -> {parent}.{pcol}")

assert not orphans, (
    f"refusing to add foreign keys — orphaned rows found: {orphans}. "
    "Fix the data or the loader that produced it; do not remove the constraint."
)
print("\nall candidate relationships are clean")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Apply
# MAGIC
# MAGIC `ADD CONSTRAINT` has no `IF NOT EXISTS` in Postgres (I-067), so idempotency is a
# MAGIC manual `pg_constraint` lookup. `CREATE INDEX IF NOT EXISTS` *is* valid — different
# MAGIC DDL, different rules.

# COMMAND ----------


def constraint_exists(cur, name: str, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = %(n)s AND conrelid = %(t)s::regclass",
        {"n": name, "t": f"{PG_SCHEMA}.{table}"},
    )
    return cur.fetchone() is not None


added, skipped = [], []
with conn.cursor() as cur:
    for iname, table, col in CHILD_INDEXES:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {iname} ON {PG_SCHEMA}.{table} ({col})")
    print(f"supporting indexes ensured: {len(CHILD_INDEXES)}")

    for name, child, col, parent, pcol, on_delete in FKS:
        if constraint_exists(cur, name, child):
            skipped.append(name)
            continue
        cur.execute(
            f"""
            ALTER TABLE {PG_SCHEMA}.{child}
            ADD CONSTRAINT {name} FOREIGN KEY ({col})
            REFERENCES {PG_SCHEMA}.{parent} ({pcol})
            ON DELETE {on_delete}
            """
        )
        added.append(name)

print(f"added {len(added)}, already present {len(skipped)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it enforces — both directions
# MAGIC
# MAGIC A test that only proves rejection would pass just as happily against a constraint
# MAGIC pointing at the wrong column, so the converse is checked too. Both writes happen
# MAGIC inside a savepoint and are rolled back.

# COMMAND ----------

with conn.cursor() as cur:
    # Reject: a work order for a depot that does not exist.
    cur.execute("SAVEPOINT fk_proof")
    rejected = False
    try:
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_work_order
                (wo_id, vin, depot_id, status)
                SELECT 'FK-PROOF-1', vin, 'DEPOT-DOES-NOT-EXIST', 'OPEN'
                FROM {PG_SCHEMA}.fleetguard_vehicle LIMIT 1"""
        )
    except psycopg.errors.ForeignKeyViolation:
        rejected = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT fk_proof")

    assert rejected, (
        "a work order referencing a nonexistent depot was ACCEPTED — the foreign key is not "
        "enforcing anything; investigate before trusting it"
    )
    print("reject proof   : work order with an unknown depot_id was refused")

    # Accept: the same insert with a real depot must still succeed, or the constraint is
    # pointing somewhere wrong and the rejection above proved nothing useful.
    cur.execute("SAVEPOINT fk_proof_ok")
    cur.execute(
        f"""INSERT INTO {PG_SCHEMA}.fleetguard_work_order
            (wo_id, vin, depot_id, status)
            SELECT 'FK-PROOF-2', vin, depot_id, 'OPEN'
            FROM {PG_SCHEMA}.fleetguard_vehicle LIMIT 1"""
    )
    cur.execute(
        f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_work_order WHERE wo_id = 'FK-PROOF-2'"
    )
    accepted = cur.fetchone()[0] == 1
    cur.execute("ROLLBACK TO SAVEPOINT fk_proof_ok")

    assert accepted, (
        "a work order with a VALID depot_id was refused — the constraint is wrong, not the data"
    )
    print("accept proof   : work order with a real depot_id was accepted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## I-107 — repair `fleetguard_depot_assignment`'s replica identity
# MAGIC
# MAGIC `15_enable_depot_rls.py` created this table without `REPLICA IDENTITY FULL` and
# MAGIC without the assertion its sibling creation scripts carry, so it silently diverged:
# MAGIC `relreplident = 'd'`, and **no `lb_fleetguard_depot_assignment_history` exists in
# MAGIC `bootcamp_cdc`** — 13 history tables for 14 tables. Nothing noticed because nothing
# MAGIC reads that table's history.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"ALTER TABLE {PG_SCHEMA}.fleetguard_depot_assignment REPLICA IDENTITY FULL")
print("fleetguard_depot_assignment set to REPLICA IDENTITY FULL")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify against the catalog, then commit
# MAGIC
# MAGIC Never infer correctness from the absence of an exception. Three checks: every
# MAGIC constraint is present and is genuinely of type `f`; every supporting index exists;
# MAGIC and **every table is still `REPLICA IDENTITY FULL`** — the last because the
# MAGIC interaction between foreign keys and Lakebase CDF is documented nowhere, so it is
# MAGIC verified rather than assumed. Anything failing here rolls the whole migration back;
# MAGIC Postgres DDL is transactional.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(
        """
        SELECT conname FROM pg_constraint
        WHERE connamespace = %(s)s::regnamespace AND contype = 'f'
        """,
        {"s": PG_SCHEMA},
    )
    present = {r[0] for r in cur.fetchall()}
    missing = {name for name, *_ in FKS} - present
    assert not missing, f"ALTER reported success but these constraints are absent: {missing}"
    print(f"foreign keys present: {len(present & {n for n, *_ in FKS})}/{len(FKS)}")

    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %(s)s AND indexname = ANY(%(n)s)",
        {"s": PG_SCHEMA, "n": [i for i, _, _ in CHILD_INDEXES]},
    )
    idx_present = {r[0] for r in cur.fetchall()}
    idx_missing = {i for i, _, _ in CHILD_INDEXES} - idx_present
    assert not idx_missing, f"index creation reported success but these are absent: {idx_missing}"
    print(f"supporting indexes present: {len(idx_present)}/{len(CHILD_INDEXES)}")

    cur.execute(
        """
        SELECT c.relname, c.relreplident
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s AND c.relname LIKE 'fleetguard_%%' AND c.relkind = 'r'
        ORDER BY 1
        """,
        {"s": PG_SCHEMA},
    )
    identities = cur.fetchall()
    not_full = [t for t, ri in identities if ri != "f"]
    assert not not_full, (
        f"REPLICA IDENTITY is no longer FULL on {not_full} — CDF's update_preimage would be "
        "useless for these. Rolling back rather than shipping a half-replicating schema."
    )
    print(f"REPLICA IDENTITY FULL on all {len(identities)} tables")

conn.commit()
print("\ncommitted")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What is NOT covered, deliberately
# MAGIC
# MAGIC - **`fleetguard_audit_log` gets no foreign keys.** It is an append-only record of what
# MAGIC   happened, and `entity_id` is polymorphic — it holds campaign ids, work-order ids and
# MAGIC   service-campaign ids in the same column. An audit row must survive the deletion of
# MAGIC   whatever it describes; that is the point of an audit log.
# MAGIC - **`fleetguard_agent_action.trace_id`** references an MLflow inference table in Unity
# MAGIC   Catalog, not a Postgres table. Not expressible as an FK.
# MAGIC - **`fleetguard_work_order.assigned_to`'s depot agreement** stays in
# MAGIC   `routers/work_orders.py`. An FK can say "this technician exists"; it cannot say
# MAGIC   "this technician works at this work order's depot".

# COMMAND ----------

conn.close()
print("done")
