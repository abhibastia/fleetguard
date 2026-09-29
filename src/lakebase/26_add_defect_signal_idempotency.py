# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — one open agent signal per series per actor (I-110 #6)
# MAGIC
# MAGIC `open_defect_signal` mints `AGENT-{uuid4}` on every call, so the write is idempotent
# MAGIC about nothing. `routers/chat.py` runs on a 120 s HTTP timeout and an agent turn can
# MAGIC legitimately take tens of seconds, so:
# MAGIC
# MAGIC ```
# MAGIC request -> timeout -> browser retries -> second signal for the same observation
# MAGIC ```
# MAGIC
# MAGIC Two rows, different ids, identical meaning, both on the operator's Emerging tab as
# MAGIC separate findings. Found by the second external repo review (2026-09-20). The same
# MAGIC review's timeout-budget fix narrows the window — the agent now budgets 90 s against the
# MAGIC client's 120 s — but narrower is not closed, and a user can always click twice.
# MAGIC
# MAGIC ## Why an index and not a check in the handler
# MAGIC
# MAGIC Identical reasoning to `19_add_service_campaign_uniqueness.py`, which this notebook is
# MAGIC modelled on: the case is two requests milliseconds apart, and a `SELECT`-then-`INSERT`
# MAGIC is a TOCTOU race that under READ COMMITTED is the *likely* interleaving, not merely a
# MAGIC possible one. Only the database serialises them. `agent_actions.py` catches the
# MAGIC resulting `UniqueViolation` and returns a 409 naming the existing signal — that is for
# MAGIC the error message, not for the enforcement.
# MAGIC
# MAGIC ## Why this key, and why *partial* on OPEN
# MAGIC
# MAGIC `(opened_by, series_key, component)`:
# MAGIC
# MAGIC - **`opened_by`** — two different safety managers independently opening a signal for
# MAGIC   the same series are two real observations, not a duplicate. Scoping per actor also
# MAGIC   keeps one person's retry from silently blocking another person's first write.
# MAGIC - **`series_key`** — the `make|model|component` string `agent_actions.py` already
# MAGIC   builds and persists. `component` is in the key as well, so that two make-less signals
# MAGIC   for different components cannot collide.
# MAGIC
# MAGIC   > **CORRECTED 2026-09-29.** This paragraph used to say `series_key` "is NULL when no
# MAGIC   > make/model was supplied", that Postgres permits repeated NULLs in a unique index,
# MAGIC   > and that such rows "fall out of the index and stay unconstrained". The middle claim
# MAGIC   > is true in general and **the conclusion is not true here.** `series_key` is built by
# MAGIC   > joining the non-empty parts of `(make, model, component)`, and since I-115 made
# MAGIC   > `component` required *and* non-blank — `min_length=1` with a `mode="before"` strip,
# MAGIC   > so `" "` is a 422 rather than an empty string — the join always has at least one
# MAGIC   > part. **`series_key` can no longer be NULL, and every agent-opened signal is
# MAGIC   > covered by this index.**
# MAGIC   >
# MAGIC   > Left as a correction rather than deleted, because the stale version was load-bearing
# MAGIC   > in the wrong direction: **two separate external reviews read it and filed the
# MAGIC   > "`series_key IS NULL` idempotency hole" as an open defect**, each proposing a second
# MAGIC   > partial index as the fix (I-117 disproved it the first time; a fifth review repeated
# MAGIC   > it on 2026-09-29). A comment describing a gap the code already shut is not a harmless
# MAGIC   > inaccuracy — it manufactures the same finding indefinitely.
# MAGIC - **`status = 'OPEN'` and `source = 'AGENT'`** — a closed signal must not block
# MAGIC   re-opening the same series later (a real workflow: open, investigate, close,
# MAGIC   recurrence). And the detector's own rows must not be constrained by a rule written
# MAGIC   for the agent write path; the batch signals loader writes `source='DETECTOR'` and is
# MAGIC   free to produce whatever the detector produces.
# MAGIC
# MAGIC Same shape as `ux_fg_service_campaign_active` and `ux_fg_watchlist_active`. Third time
# MAGIC this pattern has been the right answer on this schema.

# COMMAND ----------

import os

# I-045 / I-068: psycopg[binary] 3.3.5 aborts on Databricks serverless (FIPS self-test
# failure), so the pure-Python impl is required *there* - but macOS has no system libpq, so
# forcing it unconditionally breaks local execution. Same conditional as
# db.py::_select_psycopg_impl. (I-098: this guard is easy to omit and fails only on serverless.)
if os.getenv("DATABRICKS_RUNTIME_VERSION"):
    os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

dbutils.widgets.text("lakebase_project", "projects/summer-bootcamp-2026-v2")
dbutils.widgets.text("pg_schema", "bootcamp_students")
dbutils.widgets.text("pg_database", "databricks_postgres")
PROJECT = dbutils.widgets.get("lakebase_project")
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = dbutils.widgets.get("pg_schema")
PG_DB = dbutils.widgets.get("pg_database")
INDEX = "ux_fg_defect_signal_agent_active"

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
_TABLES_TOUCHED = ("fleetguard_defect_signal",)
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Safety check first — the index cannot be created over existing duplicates
# MAGIC
# MAGIC If this fails, do **not** widen the predicate to make it pass. Duplicates would mean
# MAGIC real double-writes are already in the table, and which row is authoritative is a data
# MAGIC decision, not a migration detail.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        SELECT opened_by, series_key, component, COUNT(*) AS n
        FROM {PG_SCHEMA}.fleetguard_defect_signal
        WHERE status = 'OPEN' AND source = 'AGENT' AND series_key IS NOT NULL
        GROUP BY opened_by, series_key, component
        HAVING COUNT(*) > 1
        ORDER BY n DESC
    """)
    dupes = cur.fetchall()

assert not dupes, (
    f"existing duplicate open agent signals would block the index: {dupes} — decide which "
    "row is authoritative and close the others before re-running"
)
print("safety check passed: no actor has two open agent signals for one series")

# COMMAND ----------

with conn.cursor() as cur:
    # `CREATE UNIQUE INDEX IF NOT EXISTS` *is* valid Postgres — unlike `ADD CONSTRAINT IF NOT
    # EXISTS` (I-067). Different DDL, different rules.
    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX}
        ON {PG_SCHEMA}.fleetguard_defect_signal (opened_by, series_key, component)
        WHERE status = 'OPEN' AND source = 'AGENT'
    """)
conn.commit()
print(f"index {INDEX} created (or already existed)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Prove it — a duplicate must actually be rejected, not merely configured
# MAGIC
# MAGIC Same discipline as every other constraint on this schema: exercised with a real write,
# MAGIC inside a savepoint so the failed statement doesn't poison the session, and rolled back
# MAGIC so the proof leaves nothing behind.
# MAGIC
# MAGIC This one seeds its own fixture rather than looking for an existing agent signal,
# MAGIC because on a freshly rebuilt database there may be none — and a proof that silently
# MAGIC skips is the kind of unfalsifiable check this project keeps removing.

# COMMAND ----------

PROBE = ("SIG-IDEMPOTENCY-PROOF-A", "SIG-IDEMPOTENCY-PROOF-B")
SERIES = "PROOF|PROOF-MODEL|PROOF-COMPONENT"


def _insert(cur, signal_id: str) -> None:
    cur.execute(
        f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
            (signal_id, component, make, model, series_key, fleet_vehicles,
             status, source, opened_by, rationale)
            VALUES (%s, 'PROOF-COMPONENT', 'PROOF', 'PROOF-MODEL', %s, 0,
                    'OPEN', 'AGENT', %s, 'idempotency proof')""",
        (signal_id, SERIES, owner),
    )


rejected = False
with conn.cursor() as cur:
    cur.execute("SAVEPOINT dupe_proof")
    try:
        _insert(cur, PROBE[0])
        _insert(cur, PROBE[1])
    except psycopg.errors.UniqueViolation:
        rejected = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT dupe_proof")

assert rejected, (
    "a second OPEN agent signal for the same (opened_by, series_key, component) was NOT "
    "rejected — the index is not enforcing anything, investigate before trusting it"
)
print("proof passed: a duplicate open agent signal was rejected")

# COMMAND ----------

# MAGIC %md
# MAGIC ## And prove the converse — a CLOSED signal must not block re-opening the series
# MAGIC
# MAGIC The partial predicate is the whole design. A test that only proves rejection would
# MAGIC pass just as happily against a plain (wrong) unique index, which would make a
# MAGIC recurrence unreportable forever.

# COMMAND ----------

allowed = False
with conn.cursor() as cur:
    cur.execute("SAVEPOINT reopen_proof")
    try:
        _insert(cur, PROBE[0])
        cur.execute(
            f"UPDATE {PG_SCHEMA}.fleetguard_defect_signal SET status = 'CLOSED' "
            "WHERE signal_id = %s",
            (PROBE[0],),
        )
        _insert(cur, PROBE[1])
        allowed = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT reopen_proof")

assert allowed, (
    "re-opening a series whose previous signal is CLOSED was rejected — the predicate is "
    "wider than status='OPEN' and would make a recurrence impossible to record"
)
print("proof passed: a series with a closed signal can be re-opened")

# COMMAND ----------

# MAGIC %md
# MAGIC ## And the detector must stay unconstrained
# MAGIC
# MAGIC `source='DETECTOR'` rows come from the batch signals loader. A rule written for the
# MAGIC agent write path must not silently start rejecting them.

# COMMAND ----------

detector_allowed = False
with conn.cursor() as cur:
    cur.execute("SAVEPOINT detector_proof")
    try:
        _insert(cur, PROBE[0])
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
                (signal_id, component, make, model, series_key, fleet_vehicles,
                 status, source, opened_by, rationale)
                VALUES (%s, 'PROOF-COMPONENT', 'PROOF', 'PROOF-MODEL', %s, 0,
                        'OPEN', 'DETECTOR', %s, 'detector proof')""",
            (PROBE[1], SERIES, owner),
        )
        detector_allowed = True
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT detector_proof")

assert detector_allowed, (
    "a DETECTOR signal was rejected by an index meant only for the agent write path — the "
    "predicate is missing its source = 'AGENT' clause"
)
print("proof passed: detector signals are unaffected")

conn.close()
