# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — load emerging defect signals into Lakebase
# MAGIC
# MAGIC Populates `fleetguard_defect_signal`, the proactive half of the console. Created in
# MAGIC Phase 5 and empty until now, because the thing meant to fill it —
# MAGIC `gold_emerging_cluster` — was descoped when clustering was falsified (I-048/I-049).
# MAGIC `gold_emerging_signal` fills it instead, at the grain the detector actually uses.
# MAGIC
# MAGIC ## The DDL describes a model that was never built
# MAGIC
# MAGIC `fleetguard_defect_signal` was designed around a clustered, harm-weighted Model A:
# MAGIC `cluster_id`, `confidence`, `severity_score`, `corroboration_rate`. None of those exist
# MAGIC (I-051 — the detector is **pure volume anomaly**). Rather than fill them with
# MAGIC plausible-looking numbers, they are left **NULL** and the real detector outputs are
# MAGIC added as columns. A NULL that says "not built" is worth more than a value that implies
# MAGIC a calibration nobody performed.
# MAGIC
# MAGIC Putting `max_z` in `confidence` would be exactly that mistake: a z-score is not a
# MAGIC probability, and a console rendering it as one would be lying in a percentage sign.
# MAGIC
# MAGIC ## Safety
# MAGIC
# MAGIC Idempotent (`ON CONFLICT DO UPDATE` on `signal_id`), transactional, name-guarded.
# MAGIC Adding columns is safe under Lakebase CDF — CDF replicates DDL (I-044).

# COMMAND ----------

import os

os.environ.setdefault("PSYCOPG_IMPL", "python")  # I-045

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
UC = "bootcamp_students.fleetguard"
TABLE = "fleetguard_defect_signal"

assert TABLE.startswith("fleetguard_"), "refusing: non-project table name"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name

conn = psycopg.connect(
    host=host, user=user, password=token, dbname=PG_DB, sslmode="require", autocommit=False
)
print(f"connected to {PG_DB} as {user}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Widen the table to carry what the detector actually produces

# COMMAND ----------

ALTERS = [
    "ADD COLUMN IF NOT EXISTS series_key TEXT",
    "ADD COLUMN IF NOT EXISTS run_start DATE",
    "ADD COLUMN IF NOT EXISTS run_end DATE",
    "ADD COLUMN IF NOT EXISTS run_len INT",
    "ADD COLUMN IF NOT EXISTS max_z NUMERIC(8,2)",
    "ADD COLUMN IF NOT EXISTS harm_share NUMERIC(5,3)",
    "ADD COLUMN IF NOT EXISTS fleet_vehicles INT",
    # How `fleet_vehicles` was matched: 'EXACT' | 'MODEL_VARIANT' | 'NONE' (I-079). Without it
    # the console shows a count whose strength it cannot state, and a MODEL_VARIANT number is
    # genuinely weaker — the RAM PROMASTER match includes 315 PROMASTER CITY vans. Nullable on
    # purpose: agent-opened rows are written by `agent_actions.py`, which computes the tier for
    # its reply but does not persist it here, so those rows carry NULL rather than a wrong tier.
    "ADD COLUMN IF NOT EXISTS match_basis TEXT",
    "ADD COLUMN IF NOT EXISTS is_live BOOLEAN",
    "ADD COLUMN IF NOT EXISTS as_of_month DATE",
]
with conn.cursor() as cur:
    for a in ALTERS:
        cur.execute(f"ALTER TABLE {PG_SCHEMA}.{TABLE} {a}")
conn.commit()
print(f"{len(ALTERS)} columns ensured on {TABLE}")

# COMMAND ----------

SQL = f"""
SELECT CONCAT_WS(':', series_key, DATE_FORMAT(run_start, 'yyyy-MM')) AS signal_id,
       series_key, comp_top AS component, make, model,
       run_start, run_end, run_len,
       CAST(max_z AS DECIMAL(8,2))       AS max_z,
       complaints_in_run                 AS complaint_count,
       CAST(harm_share AS DECIMAL(5,3))  AS harm_share,
       fleet_vehicles, match_basis, is_live, as_of_month
FROM {UC}.gold_emerging_signal
"""

pdf = spark.sql(SQL).toPandas()
print(f"signals to load: {len(pdf):,}")
print(pdf[["series_key", "run_end", "max_z", "fleet_vehicles", "is_live"]].head(10).to_string())

COLS = list(pdf.columns)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Upsert
# MAGIC
# MAGIC `status` keeps its DDL default of `OPEN` on insert and is **not** overwritten on
# MAGIC conflict: if an operator has triaged a signal, a nightly reload must not silently
# MAGIC reopen it.

# COMMAND ----------

placeholders = ", ".join(["%s"] * len(COLS))
updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c != "signal_id")
stmt = (
    f"INSERT INTO {PG_SCHEMA}.{TABLE} ({', '.join(COLS)}) VALUES ({placeholders}) "
    f"ON CONFLICT (signal_id) DO UPDATE SET {updates}, updated_at = now()"
)

rows = [tuple(None if r[c] is None else r[c] for c in COLS) for _, r in pdf.iterrows()]
with conn.cursor() as cur:
    cur.executemany(stmt, rows)
conn.commit()

with conn.cursor() as cur:
    cur.execute(
        f"SELECT COUNT(*), COUNT(*) FILTER (WHERE is_live), "
        f"COUNT(*) FILTER (WHERE fleet_vehicles > 0), "
        f"COUNT(*) FILTER (WHERE source = 'DETECTOR'), "
        f"COUNT(*) FILTER (WHERE source = 'AGENT') FROM {PG_SCHEMA}.{TABLE}"
    )
    total, live, fleet, detector, agent = cur.fetchone()

print(
    f"loaded: {total} signals ({detector} DETECTOR + {agent} AGENT) · {live} live · {fleet} fleet-relevant"
)

# RECONCILE AGAINST THE ROWS THIS LOADER OWNS, NOT THE WHOLE TABLE (I-099).
#
# This asserted `total == len(pdf)` — that Postgres holds exactly what the batch just wrote.
# But `fleetguard_defect_signal` is **co-owned by design**: the agent write path opens signals
# into it too (`source = 'AGENT'`, with `opened_by` naming the human who authorised it), which
# is the whole reason §8.3's trigger uses `ANY_UPDATED`. So the moment anyone exercises the
# agent's write path — the most demo-relevant thing in the project — this job fails forever.
#
# Measured 2026-09-11: 48 DETECTOR + 2 AGENT = 50, and the loader aborted with
# "expected 48 in Postgres, found 50" **after committing a correct load**. A green upsert
# reported as a red job, which on demo eve reads as a broken pipeline.
#
# `source` is `NOT NULL DEFAULT 'DETECTOR'` with a CHECK constraint, so this partition is
# exhaustive. Equality on the DETECTOR slice still catches the real failure the original was
# reaching for — a signal that dropped out of gold and lingers here, since the write is
# `ON CONFLICT DO UPDATE` and never deletes.
assert detector == len(pdf), (
    f"expected {len(pdf)} DETECTOR rows in Postgres, found {detector} "
    f"({agent} AGENT rows are not this loader's and are excluded)"
)
assert fleet > 0, "no fleet-relevant signals — the proactive panel would demo empty"
conn.close()
print("signal load reconciled")
