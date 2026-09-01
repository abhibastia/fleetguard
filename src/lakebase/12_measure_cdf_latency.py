# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — measure real Lakebase CDF capture latency
# MAGIC
# MAGIC §8.3 quotes **~15 s** for Postgres-write → visible-in-Unity-Catalog. That figure is
# MAGIC *documented by Databricks*, not measured here, and Databricks publishes **no latency
# MAGIC SLA** for this path — so the proposal must not present it as a system result until
# MAGIC someone times it.
# MAGIC
# MAGIC ## Two traps this notebook is built to avoid
# MAGIC
# MAGIC **1. `_timestamp` is the wrong clock.** The `_timestamp` column on a CDF history
# MAGIC table is the *Postgres commit* timestamp, not the moment the row became queryable in
# MAGIC Delta. Differencing it against the writing client's clock gives ~0.3 s and looks like
# MAGIC a brilliant result; it is really source-side commit time plus clock skew. Only
# MAGIC wall-clock write-then-poll measures what §8.3 claims.
# MAGIC
# MAGIC **2. A cold first query hides inside the answer.** The first attempt at this
# MAGIC reported 21.55 s with `polls = 1` — it found the row on its *first* check, having
# MAGIC never observed the row absent. That is an **upper bound, not a measurement**, and
# MAGIC most of it was Spark's own query-startup cost, not CDF.
# MAGIC
# MAGIC The fixes: **warm the query path before committing**, poll tightly, and require the
# MAGIC probe to observe the row *absent* at least once before it appears. A run that never
# MAGIC sees absence is reported as `is_upper_bound = true` rather than quietly quoted as a
# MAGIC latency. Three probes run, so the result is a small distribution rather than one
# MAGIC sample.
# MAGIC
# MAGIC **Safety.** Writes three rows to `fleetguard_audit_log`, which is append-only by
# MAGIC design and carries no foreign keys, each tagged `entity_type = 'LATENCY_PROBE'`.
# MAGIC Nothing is deleted or altered.

# COMMAND ----------

import datetime
import os
import time
import uuid

# See I-045 — must precede `import psycopg`.
os.environ.setdefault("PSYCOPG_IMPL", "python")

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"
UC = "bootcamp_students.fleetguard"
HISTORY = "bootcamp_students.bootcamp_cdc.lb_fleetguard_audit_log_history"

N_PROBES = 3
TIMEOUT_S = 300
POLL_EVERY_S = 0.5

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name


def visible(probe_id: str) -> bool:
    """True once the probe row is queryable in the CDF destination table."""
    return (
        spark.sql(f"SELECT COUNT(*) AS n FROM {HISTORY} WHERE entity_id = '{probe_id}'").collect()[
            0
        ]["n"]
        > 0
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## Warm the query path
# MAGIC
# MAGIC Runs the *exact* query shape against a probe id that cannot exist, so Spark planning,
# MAGIC the Delta log read and the serverless warm-up all happen **before** the clock starts.
# MAGIC The cost is printed: if the warm query is still slow, every latency below inherits
# MAGIC that floor and the numbers should be read with it in mind.

# COMMAND ----------

for i in range(3):
    t = time.time()
    assert not visible(f"warmup-{uuid.uuid4()}"), "warmup id should never exist"
    print(f"  warm-up query {i + 1}: {time.time() - t:.2f}s")

warm_cost = time.time()
visible(f"warmup-{uuid.uuid4()}")
warm_cost = time.time() - warm_cost
print(f"\nsteady-state query cost: {warm_cost:.2f}s  <- the resolution floor of this probe")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Probe
# MAGIC
# MAGIC The loop encodes its own verdict: a timeout raises rather than reporting success
# MAGIC (I-043), and a run that never observed the row absent is flagged as an upper bound
# MAGIC rather than being quoted as a measurement.

# COMMAND ----------

conn = psycopg.connect(
    host=host, user=user, password=token, dbname=PG_DB, sslmode="require", autocommit=False
)

results = []
for probe_num in range(1, N_PROBES + 1):
    probe_id = f"latency-probe-{uuid.uuid4()}"

    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                (entity_type, entity_id, action, actor_principal)
                VALUES ('LATENCY_PROBE', %s, 'PROBE', %s)""",
            (probe_id, user),
        )
        t_commit = time.time()
        conn.commit()

    deadline = t_commit + TIMEOUT_S
    found_at = None
    observed_absent = False
    polls = 0

    while time.time() < deadline:
        polls += 1
        if visible(probe_id):
            found_at = time.time()
            break
        # Seeing the row absent is what turns an upper bound into a measurement.
        observed_absent = True
        time.sleep(POLL_EVERY_S)

    if found_at is None:
        conn.close()
        raise SystemExit(
            f"TIMEOUT: probe {probe_num} never appeared in {HISTORY} within {TIMEOUT_S}s "
            f"({polls} polls). CDF is not replicating — a real failure, not a slow run."
        )

    latency = found_at - t_commit
    results.append(
        {
            "probe_id": probe_id,
            "probe_num": probe_num,
            "committed_at": datetime.datetime.fromtimestamp(t_commit, tz=datetime.UTC),
            "visible_at": datetime.datetime.fromtimestamp(found_at, tz=datetime.UTC),
            "latency_seconds": float(round(latency, 2)),
            "observed_absent": bool(observed_absent),
            "is_upper_bound": bool(not observed_absent),
            "polls": int(polls),
            "poll_interval_seconds": float(POLL_EVERY_S),
            "warm_query_seconds": float(round(warm_cost, 2)),
            "documented_seconds": 15.0,
        }
    )
    verdict = "measured" if observed_absent else "UPPER BOUND (never saw it absent)"
    print(f"  probe {probe_num}: {latency:6.2f}s  polls={polls:<4} {verdict}")

conn.close()

# COMMAND ----------

measured = [r["latency_seconds"] for r in results if r["observed_absent"]]
print(f"\n{len(measured)}/{len(results)} probes are true measurements")
if measured:
    print(f"  min {min(measured):.2f}s   max {max(measured):.2f}s")
    print(f"  mean {sum(measured) / len(measured):.2f}s")
    print("  vs §8.3's documented ~15 s")
else:
    print("  ALL probes are upper bounds — the query path is slower than CDF.")
    print("  Report as '<= Ns', never as a latency.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist
# MAGIC
# MAGIC `get-run-output` returns nothing without `dbutils.notebook.exit()` (I-045), so the
# MAGIC measurement goes to a table or it is effectively lost.

# COMMAND ----------

(
    spark.createDataFrame(results)
    .write.mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(f"{UC}.ops_cdf_latency")
)
display(spark.table(f"{UC}.ops_cdf_latency").orderBy("committed_at"))
