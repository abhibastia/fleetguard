# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — make the live recall API visible in the console
# MAGIC
# MAGIC The `recallsByVehicle` integration has been real since Phase 1 — 200 fleet combos
# MAGIC swept per run, 653 distinct campaigns in `bronze_recall_api`, novel campaigns derived
# MAGIC into `gold_recall_alert` and described in that notebook's own comments as *"the
# MAGIC reactive product surface"*.
# MAGIC
# MAGIC **Nothing read it.** Grepped 2026-09-20: zero references to either table in the app
# MAGIC routers, the frontend, the dashboard JSON, the metric views, the agent tools or the
# MAGIC tests. The only place a human ever saw it was two `display()` calls at the bottom of
# MAGIC the polling job. An integration that runs and is then consumed by nothing is an
# MAGIC integration a reader has to take on trust.
# MAGIC
# MAGIC ## Why this is a loader and not a route
# MAGIC
# MAGIC The console has **no request-time Unity Catalog read**. Every authenticated route
# MAGIC goes through `db.py connect(principal)` to Lakebase Postgres; the single exception is
# MAGIC `/api/evidence`, which serves a committed JSON file precisely because it must answer
# MAGIC without a credential (§8a). Adding the project's first warehouse-backed route would
# MAGIC mean a `WorkspaceClient` in the app, a fake for it in the test suite and a snapshot
# MAGIC story for it — real surface area, to show data that changes once per sweep.
# MAGIC
# MAGIC Loading it into Lakebase instead reuses the path everything else already uses, and
# MAGIC gets CDF replication back into Unity Catalog for free.
# MAGIC
# MAGIC ## Two tables, because they are two grains
# MAGIC
# MAGIC | table | grain | what it answers |
# MAGIC |---|---|---|
# MAGIC | `fleetguard_recall_api_poll` | one row per (make, model, year) combo | is the integration healthy, and what does it cover |
# MAGIC | `fleetguard_recall_api_alert` | one row per novel campaign | has the API seen a recall the daily flat file has not |
# MAGIC
# MAGIC **The alert table was empty for weeks, and then it was not.** Until 2026-09-20 every
# MAGIC one of the 653 campaigns the API returned was already in the flat file, so
# MAGIC `gold_recall_alert` held zero rows — the correct answer, and the console must be able
# MAGIC to say that in words rather than render an empty table. Then the first sweep in twenty
# MAGIC days (the corpus had gone stale) found **2 genuinely novel campaigns across 5 rows,
# MAGIC touching 651 fleet vehicles**: `26V578000` (FORD F-150 2024, fuel system, 272 vehicles
# MAGIC across 60 depots) and `26V583000` (FREIGHTLINER CASCADIA 2020-2023, ABS, 379 vehicles).
# MAGIC The live API has both; the daily flat file, which still runs to 2026-08-27, does not.
# MAGIC
# MAGIC That is the reactive half doing exactly what it claims, on real data — and it is worth
# MAGIC being careful about, because I-032 is the case where this same derivation reported 9
# MAGIC "novel" campaigns that were all already known. The fix then was to key novelty on
# MAGIC `campaign_number` alone rather than on model strings that differ between sources. These
# MAGIC two survive that test. **The console must still handle zero gracefully** — the flat
# MAGIC file catching up returns this to empty, and that is not a failure either.

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
UC = "bootcamp_students.fleetguard"

POLL_TABLE = "fleetguard_recall_api_poll"
ALERT_TABLE = "fleetguard_recall_api_alert"

_TABLES_TOUCHED = (POLL_TABLE, ALERT_TABLE)
assert all(t.startswith("fleetguard_") for t in _TABLES_TOUCHED), "refusing: non-project table name"

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
# MAGIC ## Schema
# MAGIC
# MAGIC `REPLICA IDENTITY FULL` and the read-back assertion are **not optional** — I-107 is
# MAGIC exactly the table that was created without them and silently never replicated.

# COMMAND ----------

DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.{POLL_TABLE} (
        combo_key            TEXT PRIMARY KEY,
        make                 TEXT NOT NULL,
        model                TEXT NOT NULL,
        model_year           INT  NOT NULL,
        fleet_vehicles       INT  NOT NULL DEFAULT 0,
        last_polled_at       TIMESTAMPTZ,
        last_status          TEXT,
        last_campaign_count  INT,
        refreshed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.{ALERT_TABLE} (
        -- SURROGATE KEY, because `campaign_number` is NOT the grain. gold_recall_alert
        -- groups by campaign AND make/model/model_year, so one campaign spans several rows:
        -- 26V583000 covers FREIGHTLINER CASCADIA 2020, 2021, 2022 and 2023 as four rows with
        -- different exposure counts. A campaign_number primary key looked obviously right and
        -- was rejected by Postgres on the first live load — the constraint caught a modelling
        -- error that a nullable-tolerant design would have hidden as silently merged rows.
        -- make/model/model_year are nullable in the source, so they cannot be a composite PK.
        alert_key            TEXT PRIMARY KEY,
        campaign_number      TEXT NOT NULL,
        make                 TEXT,
        model                TEXT,
        model_year           INT,
        component            TEXT,
        park_it              BOOLEAN NOT NULL DEFAULT FALSE,
        park_outside         BOOLEAN NOT NULL DEFAULT FALSE,
        report_received_date TEXT,
        consequence          TEXT,
        summary              TEXT,
        vehicles_exposed     INT NOT NULL DEFAULT 0,
        depots_affected      INT NOT NULL DEFAULT 0,
        fetched_at           TIMESTAMPTZ,
        refreshed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS ix_fg_api_poll_status ON {PG_SCHEMA}.{POLL_TABLE} (last_status)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_api_alert_exposed ON {PG_SCHEMA}.{ALERT_TABLE} (vehicles_exposed DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_fg_api_alert_campaign ON {PG_SCHEMA}.{ALERT_TABLE} (campaign_number)",
    f"ALTER TABLE {PG_SCHEMA}.{POLL_TABLE} REPLICA IDENTITY FULL",
    f"ALTER TABLE {PG_SCHEMA}.{ALERT_TABLE} REPLICA IDENTITY FULL",
]

with conn.cursor() as cur:
    for stmt in DDL:
        cur.execute(stmt)
    cur.execute(
        """
        SELECT c.relname, c.relreplident FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s AND c.relname = ANY(%(t)s)
        """,
        {"s": PG_SCHEMA, "t": list(_TABLES_TOUCHED)},
    )
    identities = dict(cur.fetchall())

missing = [t for t in _TABLES_TOUCHED if identities.get(t) != "f"]
assert not missing, (
    f"{missing} are not REPLICA IDENTITY FULL — CDF would carry only the primary key on "
    "update/delete. This is exactly I-107; refusing to commit."
)
print(f"schema ready, both tables REPLICA IDENTITY FULL: {identities}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read the gold/ops side
# MAGIC
# MAGIC `ops_recall_poll_state` is the per-combo cursor the polling job MERGEs; `fleet_vehicles`
# MAGIC comes from the same exposure query the poller builds its combo list from, so the
# MAGIC console shows coverage on the fleet's own terms rather than a bare combo list.

# COMMAND ----------

poll_pdf = spark.sql(f"""
SELECT CONCAT_WS('|', s.make, s.model, CAST(s.model_year AS STRING)) AS combo_key,
       s.make, s.model, s.model_year,
       CAST(COALESCE(e.fleet_vehicles, 0) AS INT) AS fleet_vehicles,
       s.last_polled_at, s.last_status, s.last_campaign_count
FROM {UC}.ops_recall_poll_state s
LEFT JOIN (
    SELECT make, recall_model AS model, model_year, COUNT(DISTINCT vin) AS fleet_vehicles
    FROM {UC}.gold_fleet_exposure
    GROUP BY make, recall_model, model_year
) e ON e.make = s.make AND e.model = s.model AND e.model_year = s.model_year
""").toPandas()

alert_pdf = spark.sql(f"""
SELECT CONCAT_WS('|', campaign_number, COALESCE(make, ''), COALESCE(model, ''),
                 COALESCE(CAST(model_year AS STRING), '')) AS alert_key,
       campaign_number, make, model, model_year, component,
       park_it, park_outside, report_received_date, consequence, summary,
       CAST(vehicles_exposed AS INT) AS vehicles_exposed,
       CAST(depots_affected AS INT)  AS depots_affected,
       fetched_at
FROM {UC}.gold_recall_alert
""").toPandas()

print(f"poll rows : {len(poll_pdf):,}")
print(f"alert rows: {len(alert_pdf):,}")
if len(alert_pdf) == 0:
    print(
        "  zero alerts — every campaign the API returned is already in the daily flat file. "
        "This is the correct state, not an empty load (I-032)."
    )
else:
    print(
        f"  {alert_pdf['campaign_number'].nunique()} novel campaign(s) across "
        f"{len(alert_pdf)} row(s), {int(alert_pdf['vehicles_exposed'].sum()):,} vehicles "
        "exposed — present in the live API, absent from the daily flat file"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load
# MAGIC
# MAGIC Upsert for the poll cursor. The alert table is **replace-in-transaction**: an alert
# MAGIC that is no longer novel — because the flat file has caught up — must *disappear*, and
# MAGIC `ON CONFLICT DO UPDATE` never deletes. Same reasoning as I-099's orphan problem, in
# MAGIC the one place where a delete is the correct semantic.

# COMMAND ----------


def upsert(table: str, pdf, key: str) -> None:
    if pdf.empty:
        print(f"{table}: nothing to upsert")
        return
    cols = list(pdf.columns)
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != key)
    stmt = (
        f"INSERT INTO {PG_SCHEMA}.{table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}, refreshed_at = now()"
    )
    rows = [tuple(None if r[c] is None else r[c] for c in cols) for _, r in pdf.iterrows()]
    with conn.cursor() as cur:
        cur.executemany(stmt, rows)
    print(f"{table}: upserted {len(rows):,}")


upsert(POLL_TABLE, poll_pdf, "combo_key")

# Alerts: delete-then-insert inside the open transaction, so a reader never sees an empty
# table and a stale alert cannot survive the flat file catching up.
with conn.cursor() as cur:
    cur.execute(f"DELETE FROM {PG_SCHEMA}.{ALERT_TABLE}")
if not alert_pdf.empty:
    cols = list(alert_pdf.columns)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {PG_SCHEMA}.{ALERT_TABLE} ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))})",
            [tuple(None if r[c] is None else r[c] for c in cols) for _, r in alert_pdf.iterrows()],
        )
print(f"{ALERT_TABLE}: replaced with {len(alert_pdf):,} row(s)")

conn.commit()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reconcile — exactly, in both directions

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{POLL_TABLE}")
    poll_n = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{ALERT_TABLE}")
    alert_n = cur.fetchone()[0]
    cur.execute(
        f"SELECT last_status, COUNT(*) FROM {PG_SCHEMA}.{POLL_TABLE} GROUP BY 1 ORDER BY 2 DESC"
    )
    by_status = cur.fetchall()

print(f"{POLL_TABLE:<32} {poll_n:>6,}  (source {len(poll_pdf):,})")
print(f"{ALERT_TABLE:<32} {alert_n:>6,}  (source {len(alert_pdf):,})")
print(f"status mix: {by_status}")

assert poll_n == len(poll_pdf), (
    f"{POLL_TABLE}: loaded {poll_n} but source had {len(poll_pdf)} — ON CONFLICT collapsed "
    "rows, which means combo_key is not unique per combo"
)
assert alert_n == len(alert_pdf), f"{ALERT_TABLE}: holds {alert_n} but source had {len(alert_pdf)}"

# The console's counters divide by this; a zero would be a division by zero on the page
# rather than an obviously-empty panel.
assert poll_n > 0, (
    "no poll rows — run fleetguard-poll-recalls-api first, or the console's API panel will "
    "render a coverage percentage over nothing"
)

conn.close()
print("\nrecall API status reconciled")
