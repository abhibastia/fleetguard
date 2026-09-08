# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 11: CDF → gold facts, on a `table_update` trigger
# MAGIC
# MAGIC The last unbuilt link in the data loop. Everything else in the round trip already
# MAGIC exists and is measured: the console writes Postgres under the caller's identity, Lakebase
# MAGIC CDF replicates that into `bootcamp_students.bootcamp_cdc` in **7.1–15.6 s** (I-046), and
# MAGIC the history tables are queryable. What did **not** exist is anything that turns those
# MAGIC append-only history tables into current-state facts in the project's own schema, or that
# MAGIC does it *without a human running a job* — which is the whole of §8.3's claim.
# MAGIC
# MAGIC This notebook is the derivation. The job that carries it (`fleetguard-cdf-to-gold`)
# MAGIC supplies the automation via `trigger.table_update`.
# MAGIC
# MAGIC ## The pattern this notebook does NOT use, and why
# MAGIC
# MAGIC `CLAUDE.md` recorded a "latest per key" pattern that filters `'delete'` in the **inner**
# MAGIC `WHERE`, before the window function runs. That is wrong, and wrong in the silent
# MAGIC direction: removing the tombstone from the window means the last surviving event for a
# MAGIC deleted key is its own `insert`, so **deleted rows come back as current**.
# MAGIC
# MAGIC Measured here before writing a line of the derivation:
# MAGIC `lb_fleetguard_agent_action_history` holds 4 inserts and 2 deletes, and live Postgres
# MAGIC holds 2 rows. The old pattern returned **4**. The corrected one returns **2**.
# MAGIC
# MAGIC The two resurrected rows would have been the *test data* that was deliberately cleaned
# MAGIC out of live Lakebase on 2026-09-04 — so the gold layer would have quietly re-asserted
# MAGIC records the operator believed were gone. Corrected in `CLAUDE.md` and logged as I-080.
# MAGIC
# MAGIC **Rank over the tombstones, then drop the keys whose latest event is one.**

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
CDC = "bootcamp_students.bootcamp_cdc"
UC = f"{CATALOG}.{SCHEMA}"

spark.sql(f"USE {UC}")
print(f"deriving into {UC} from {CDC}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The derivation
# MAGIC
# MAGIC One helper, applied to both tables, so the two facts cannot drift apart in how they
# MAGIC interpret a change stream — the same reason the silver layer computes its failure
# MAGIC reasons once and drives both branches from them.


# COMMAND ----------


def latest_per_key(history_table: str, key: str) -> str:
    """SQL for current state from a Lakebase CDF history table.

    `update_preimage` is the BEFORE image of an update and is never current, so it is
    excluded from the window outright. `delete` IS kept in the window — it is the tombstone
    that proves the key is gone — and filtered only after ranking. See I-080.
    """
    return f"""
        SELECT * EXCEPT (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY _sort_by DESC) AS rn
            FROM {history_table}
            WHERE _pg_change_type <> 'update_preimage'
        )
        WHERE rn = 1 AND _pg_change_type <> 'delete'
    """


FACTS = {
    # fact table            (history table,                              key)
    "gold_agent_action": (f"{CDC}.lb_fleetguard_agent_action_history", "action_id"),
    "gold_defect_signal_current": (
        f"{CDC}.lb_fleetguard_defect_signal_history",
        "signal_id",
    ),
}

for fact, (history, key) in FACTS.items():
    spark.sql(f"CREATE OR REPLACE TABLE {fact} AS {latest_per_key(history, key)}")
    print(f"{fact:<28} <- {history}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify by arithmetic, not by "it ran"
# MAGIC
# MAGIC The rule this project has earned ten times over: never infer correctness from the
# MAGIC absence of an exception. For a change stream the checkable invariant is
# MAGIC **distinct keys = live keys + deleted keys**, with no key on both sides. If that holds,
# MAGIC the split is right; if it does not, the ranking is wrong and the fact table is a guess.

# COMMAND ----------

for fact, (history, key) in FACTS.items():
    r = spark.sql(f"""
        WITH ranked AS (
            SELECT {key} AS k, _pg_change_type AS ct,
                   ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY _sort_by DESC) AS rn
            FROM {history} WHERE _pg_change_type <> 'update_preimage'
        )
        SELECT
            (SELECT COUNT(DISTINCT {key}) FROM {history})              AS distinct_keys,
            (SELECT COUNT(*) FROM ranked WHERE rn=1 AND ct <> 'delete') AS live_keys,
            (SELECT COUNT(*) FROM ranked WHERE rn=1 AND ct  = 'delete') AS deleted_keys,
            (SELECT COUNT(*) FROM {fact})                               AS fact_rows
    """).collect()[0]

    print(
        f"{fact:<28} distinct={r.distinct_keys} live={r.live_keys} "
        f"deleted={r.deleted_keys} fact_rows={r.fact_rows}"
    )
    assert r.live_keys + r.deleted_keys == r.distinct_keys, (
        f"{fact}: {r.live_keys} + {r.deleted_keys} != {r.distinct_keys} — "
        "every key must be exactly one of live or deleted"
    )
    assert r.fact_rows == r.live_keys, (
        f"{fact}: wrote {r.fact_rows} rows but {r.live_keys} keys are live"
    )

    # The regression that motivated this notebook. The old pattern filtered deletes before
    # ranking; if it ever creeps back, this fires rather than silently inflating the fact.
    old = (
        spark.sql(f"""
        SELECT COUNT(*) AS n FROM (
            SELECT ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY _sort_by DESC) AS rn
            FROM {history} WHERE _pg_change_type NOT IN ('delete','update_preimage')
        ) WHERE rn = 1
    """)
        .collect()[0]
        .n
    )
    if r.deleted_keys:
        assert old > r.fact_rows, (
            f"{fact}: the superseded pattern should over-count while deletes exist "
            f"(got {old} vs {r.fact_rows}) — if these now agree, re-check I-080"
        )
        print(f"    (superseded pattern would have returned {old} — I-080 regression held)")

print("\nall fact tables reconcile")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Record freshness, so the velocity claim is measured rather than asserted
# MAGIC
# MAGIC §8.3 claims sub-minute Postgres→UC. The pieces were measured separately (CDF capture
# MAGIC 7.1–15.6 s, I-046) but the *end* of the chain — when a fact table actually reflects the
# MAGIC write — was never recorded. This writes one row per run so the claim has evidence with
# MAGIC dates on it, and so a stale trigger is visible as a gap rather than invisible.

# COMMAND ----------

from datetime import UTC, datetime, timezone

rows = []
for fact, (history, _) in FACTS.items():
    m = spark.sql(f"SELECT MAX(_timestamp) AS newest FROM {history}").collect()[0].newest
    n = spark.table(fact).count()
    rows.append((fact, history, n, m, datetime.now(UTC)))

spark.createDataFrame(
    rows,
    "fact_table STRING, source_history STRING, fact_rows BIGINT, "
    "newest_change TIMESTAMP, refreshed_at TIMESTAMP",
).write.mode("append").saveAsTable("ops_cdf_fact_refresh")

display(
    spark.sql("""
        SELECT fact_table, fact_rows, newest_change, refreshed_at,
               ROUND(BIGINT(refreshed_at) - BIGINT(newest_change), 1) AS lag_seconds
        FROM ops_cdf_fact_refresh ORDER BY refreshed_at DESC LIMIT 10
    """)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read `lag_seconds` honestly
# MAGIC
# MAGIC It is **change-to-refresh**, not Postgres-to-UC latency. On a *triggered* run it is a
# MAGIC fair end-to-end number. On a **manual** run it is meaningless — it measures how long
# MAGIC since anyone last wrote, which for an idle table is hours. Quote it only from runs whose
# MAGIC `trigger` was `table_update`, and never average the two together.
# MAGIC
# MAGIC This is the same discipline as I-046, where the first CDF latency attempt produced an
# MAGIC upper bound that was not a measurement — and the same as the three lead-time intervals
# MAGIC §3 keeps distinct.
