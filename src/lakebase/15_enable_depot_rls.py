# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Postgres RLS on depot scoping (Phase 10, the visible slice)
# MAGIC
# MAGIC `scoping.py` has said since Phase 7: *"Postgres RLS can enforce depot scoping below
# MAGIC the application — which is what keeps §5.1's 'the frontend cannot bypass it' literally
# MAGIC true once the policies are written."* Nobody had written them. This does.
# MAGIC
# MAGIC ## Scope — deliberately narrow, and the narrowing is stated, not hidden
# MAGIC
# MAGIC Full governance scope is Data Classification + ABAC + DQ Monitors + System Tables.
# MAGIC That was cut to a **visible slice** on 2026-08-31: depot-scoped RLS on the operational
# MAGIC path, proved once, not the full governance matrix. This notebook is that slice.
# MAGIC
# MAGIC ## The policy is fail-open by default, and that is a real limitation, not an oversight
# MAGIC
# MAGIC `fleetguard_vehicle` is shared Lakebase infrastructure with 25+ Databricks identities as
# MAGIC Postgres login roles (measured 2026-09-01), most of them unrelated bootcamp students who
# MAGIC have never touched this schema. **Restricting them by default is not this project's call
# MAGIC to make** — it would change behaviour for people who never opted into FleetGuard at all.
# MAGIC
# MAGIC So the policy is additive: a principal with **no row** in `fleetguard_depot_assignment`
# MAGIC sees everything, exactly as today. A principal *explicitly assigned* a depot sees only
# MAGIC that depot's vehicles, enforced by Postgres, below the application. That is real
# MAGIC enforcement for the depot-manager persona §2 describes — it is not yet default-deny
# MAGIC governance, and the gap is recorded rather than implied away. A production rollout would
# MAGIC flip the default and enroll every principal explicitly; that is out of scope here.
# MAGIC
# MAGIC ## `fleetguard_vehicle_exposure` needs no policy of its own
# MAGIC
# MAGIC It carries no `depot_id` — every read joins it to `fleetguard_vehicle` (see
# MAGIC `routers/queue.py`). Postgres RLS filters rows returned *from a table scan*, so
# MAGIC restricting `fleetguard_vehicle` restricts every join through it. Confirmed below, not
# MAGIC assumed.

# COMMAND ----------

import os

os.environ.setdefault("PSYCOPG_IMPL", "python")  # I-045

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

dbutils.widgets.text("lakebase_project", "projects/summer-bootcamp-2026-v2")
dbutils.widgets.text("pg_schema", "bootcamp_students")
dbutils.widgets.text("pg_database", "databricks_postgres")
PROJECT = dbutils.widgets.get("lakebase_project")
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = dbutils.widgets.get("pg_schema")
PG_DB = dbutils.widgets.get("pg_database")

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
_TABLES_TOUCHED = ("fleetguard_depot_assignment", "fleetguard_vehicle")
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

# MAGIC %md
# MAGIC ## The assignment table and the policy
# MAGIC
# MAGIC `postgres_role` is the Postgres identity RLS sees (`current_user`) — for a Databricks
# MAGIC identity that is the user's email, matching what `db.py` already relies on.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.fleetguard_depot_assignment (
            postgres_role TEXT PRIMARY KEY,
            depot_id      TEXT NOT NULL,
            assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            note          TEXT
        )
    """)

    # REPLICA IDENTITY FULL, same as every other table in this schema. Missing here until
    # 2026-09-20 (I-107), which is why `lb_fleetguard_depot_assignment_history` never
    # appeared in bootcamp_cdc: CDF had only the primary key to work with, and the table
    # was the one exception to an invariant three documents asserted was universal. It went
    # unnoticed because nothing reads that history table. The assertion below is the part
    # that matters — `08_`, `17_` and `22_` all carry one and this script did not, so it was
    # the missing *check*, not the missing ALTER, that let the gap persist.
    cur.execute(f"""
        ALTER TABLE {PG_SCHEMA}.fleetguard_depot_assignment REPLICA IDENTITY FULL
    """)
    cur.execute(
        "SELECT relreplident FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %(s)s AND c.relname = 'fleetguard_depot_assignment'",
        {"s": PG_SCHEMA},
    )
    _ri = cur.fetchone()[0]
    assert _ri == "f", (
        f"fleetguard_depot_assignment has REPLICA IDENTITY '{_ri}', not 'f' (FULL) — CDF "
        "would carry only the primary key on update/delete and update_preimage would be "
        "useless. This is I-107; do not proceed."
    )

    # Fail-open by construction: a role with no assignment row matches the first branch of
    # the OR and sees everything. Only a role with a row in the assignment table is
    # restricted, and only to that row's depot.
    cur.execute(f"""
        ALTER TABLE {PG_SCHEMA}.fleetguard_vehicle ENABLE ROW LEVEL SECURITY
    """)
    # FORCE, not just ENABLE — Postgres exempts the table OWNER from RLS by default, which
    # would make "the frontend cannot bypass it" false for any owner-connected caller. Without
    # this, RLS is real for other identities but decorative for the one running this notebook.
    cur.execute(f"""
        ALTER TABLE {PG_SCHEMA}.fleetguard_vehicle FORCE ROW LEVEL SECURITY
    """)
    cur.execute(f"""
        DROP POLICY IF EXISTS depot_scope ON {PG_SCHEMA}.fleetguard_vehicle
    """)
    cur.execute(f"""
        CREATE POLICY depot_scope ON {PG_SCHEMA}.fleetguard_vehicle
        FOR SELECT
        USING (
            NOT EXISTS (
                SELECT 1 FROM {PG_SCHEMA}.fleetguard_depot_assignment a
                WHERE a.postgres_role = current_user
            )
            OR depot_id = (
                SELECT a.depot_id FROM {PG_SCHEMA}.fleetguard_depot_assignment a
                WHERE a.postgres_role = current_user
            )
        )
    """)
conn.commit()
print("RLS enabled + policy created on fleetguard_vehicle")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — under this identity's own connection, not a config trusted on faith
# MAGIC
# MAGIC A policy that has never been queried under a restricted state is unverified. The
# MAGIC original design proved this with a second, purpose-made Postgres role — but this
# MAGIC identity has no `CREATEROLE` on this shared Lakebase instance (measured: `rolcreaterole
# MAGIC = false`, correctly restricted on infrastructure shared with ~296 other students).
# MAGIC
# MAGIC `FORCE ROW LEVEL SECURITY` above makes this identity's own connection subject to the
# MAGIC policy too, so the proof runs by toggling this identity's own assignment row and
# MAGIC re-querying — no new role required, and no privilege escalation attempted. This is not
# MAGIC a workaround: without `FORCE`, an owner-connected caller bypasses RLS entirely, which
# MAGIC would make "the frontend cannot bypass it" false for that caller regardless of policy
# MAGIC content. Proving it this way closes that gap rather than routing around it.

# COMMAND ----------

TEST_DEPOT = "DEP-041"  # largest depot (374 vehicles) — a wrong result can't hide in rounding

with conn.cursor() as cur:
    cur.execute(
        f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_vehicle WHERE depot_id = %s", (TEST_DEPOT,)
    )
    depot_truth = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_vehicle")
    full_truth = cur.fetchone()[0]
    cur.execute(
        f"""
        SELECT COUNT(DISTINCT e.exposure_id)
        FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
        JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
        WHERE v.depot_id = %s
    """,
        (TEST_DEPOT,),
    )
    exposure_truth = cur.fetchone()[0]
print(f"ground truth, all vehicles: {full_truth}")
print(f"ground truth for {TEST_DEPOT}: {depot_truth} vehicles, {exposure_truth} exposure rows")

# COMMAND ----------

# Step 1 — fail-open, under FORCE. No assignment row exists for `owner` yet, so this now
# genuinely exercises the policy's NOT EXISTS branch rather than owner-exemption.
with conn.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_vehicle")
    unassigned_sees = cur.fetchone()[0]
print(f"unassigned (no row): sees {unassigned_sees} vehicles")

# COMMAND ----------

# Step 2 — assign this identity to TEST_DEPOT and re-query in the same transaction. A
# transaction sees its own uncommitted writes, so no reconnect or commit is needed for the
# read to reflect the insert.
with conn.cursor() as cur:
    cur.execute(
        f"""INSERT INTO {PG_SCHEMA}.fleetguard_depot_assignment (postgres_role, depot_id, note)
            VALUES (%s, %s, 'RLS proof — 15_enable_depot_rls, removed at end of same run')
            ON CONFLICT (postgres_role) DO UPDATE SET depot_id = EXCLUDED.depot_id""",
        (owner, TEST_DEPOT),
    )
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_vehicle")
    restricted_sees = cur.fetchone()[0]
    cur.execute(f"SELECT DISTINCT depot_id FROM {PG_SCHEMA}.fleetguard_vehicle")
    depots_visible = [r[0] for r in cur.fetchall()]
    # The join case — what actually matters, since the console never queries
    # fleetguard_vehicle alone (see routers/queue.py). If this is not filtered, the policy
    # protects nothing the application actually reads.
    cur.execute(f"""
        SELECT COUNT(DISTINCT e.exposure_id)
        FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
        JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
    """)
    restricted_exposure = cur.fetchone()[0]

print(f"assigned to {TEST_DEPOT}: sees {restricted_sees} vehicles, depots={depots_visible}")
print(f"assigned to {TEST_DEPOT}: joined exposure count {restricted_exposure}")

# COMMAND ----------

# Step 3 — remove the assignment, confirm fail-open is restored (still within the same
# transaction, not yet committed — so nothing here has been visible to any other connection).
with conn.cursor() as cur:
    cur.execute(
        f"DELETE FROM {PG_SCHEMA}.fleetguard_depot_assignment WHERE postgres_role = %s", (owner,)
    )
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_vehicle")
    restored_sees = cur.fetchone()[0]
print(f"assignment removed: sees {restored_sees} vehicles (should be back to {full_truth})")

# COMMAND ----------

# The actual assertions — this is what "proved," not "configured," means. All three states
# (unassigned, assigned, unassigned again) are exercised under FORCE ROW LEVEL SECURITY.
#
# BUT FORCE alone is not sufficient — BYPASSRLS on the connecting role overrides even FORCE,
# and unlike FORCE it cannot be toggled by this notebook (only a role with CREATEROLE and
# ADMIN OPTION on this role may ALTER it — the same "no CREATEROLE on this account" limitation
# already documented for the shared abhi project, measured here 2026-09-25 on a brand-new,
# self-owned Lakebase project: the project-creating role gets BYPASSRLS by default and there is
# no more-privileged role available to revoke it). Detected, not assumed: a project where this
# is False would still fail loudly on a real regression.
with conn.cursor() as cur:
    cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    _bypassrls = cur.fetchone()[0]

if _bypassrls:
    print(
        f"\nSKIPPING the identity-scoped proof: role {owner!r} has BYPASSRLS, which overrides "
        "FORCE ROW LEVEL SECURITY for this connection regardless of policy correctness. The "
        "policy itself (ENABLE + FORCE + CREATE POLICY depot_scope, above) was still applied — "
        "only this self-test cannot observe it, because it has no way to connect as a "
        "non-owner role on this project. A real end user's OBO-mapped role is not expected to "
        "carry BYPASSRLS; that is unverified here, not disproven."
    )
    print(
        f"unassigned_sees={unassigned_sees} restricted_sees={restricted_sees} "
        f"depots_visible={depots_visible} restored_sees={restored_sees} (all == {full_truth}, "
        "as expected under BYPASSRLS)"
    )
else:
    assert unassigned_sees == full_truth, (
        f"fail-open branch broken: saw {unassigned_sees} with no assignment row, "
        f"expected {full_truth} — FORCE may not be applying, or the policy regressed"
    )
    assert restricted_sees == depot_truth, (
        f"RLS did not restrict correctly: saw {restricted_sees}, expected {depot_truth}"
    )
    assert depots_visible == [TEST_DEPOT], f"assigned identity saw other depots: {depots_visible}"
    assert restricted_sees < unassigned_sees, "assigned state saw as much as the unassigned state"
    assert restricted_exposure == exposure_truth, (
        "exposure join was not filtered by the vehicle-table policy — RLS on fleetguard_vehicle "
        "alone would not actually protect what the console reads"
    )
    assert restored_sees == full_truth, "removing the assignment did not restore fail-open access"
    print("\nRLS proof passed: restriction, the join path, and fail-open are all confirmed under")
    print("FORCE ROW LEVEL SECURITY — this identity cannot bypass its own policy either.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Commit — leaving no assignment row behind
# MAGIC
# MAGIC Every step above ran in one open transaction; nothing is durable, and no other
# MAGIC connection (including the app's own OBO connections) has observed any of it yet. The
# MAGIC final state committed here is the real one going forward: RLS + FORCE enabled, the
# MAGIC policy live, and the assignment table empty — every current principal stays fail-open
# MAGIC exactly as before this notebook ran, until someone is deliberately enrolled.

# COMMAND ----------

# The DELETE in Step 3 already removed the one assignment row this notebook created; commit
# just makes that (and the earlier RLS/FORCE/policy DDL) durable.
conn.commit()

with conn.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_depot_assignment")
    remaining = cur.fetchone()[0]
assert remaining == 0, (
    f"{remaining} row(s) left in fleetguard_depot_assignment after teardown — "
    "some principal would be unexpectedly restricted"
)
conn.close()
print("committed: RLS + FORCE active, policy live, assignment table empty (0 rows)")
