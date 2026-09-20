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


def latest_per_key(history_table: str, key: str, *, since: int | None = None) -> str:
    """SQL for current state from a Lakebase CDF history table.

    `update_preimage` is the BEFORE image of an update and is never current, so it is
    excluded from the window outright. `delete` IS kept in the window — it is the tombstone
    that proves the key is gone — and filtered only after ranking. See I-080.

    With `since`, the same ranking is applied to the slice of history above a `_sort_by`
    watermark. The ranking rule is **identical** in both modes, deliberately: the full and
    incremental paths must not be two different interpretations of the same change stream.
    The only difference is how many rows go into the window.
    """
    bound = f"AND _sort_by > {since}" if since is not None else ""
    return f"""
        SELECT * EXCEPT (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY _sort_by DESC) AS rn
            FROM {history_table}
            WHERE _pg_change_type <> 'update_preimage' {bound}
        )
        WHERE rn = 1 AND _pg_change_type <> 'delete'
    """


def changed_keys_in_slice(history_table: str, key: str, since: int) -> str:
    """Latest event per key **within the slice**, including tombstones.

    This is what drives the MERGE: a key whose latest event above the watermark is a delete
    must be removed from the fact, and a key whose latest is an insert/update_postimage must
    be upserted. Tombstones are therefore kept here and discriminated in the MERGE, not
    filtered out — the same I-080 discipline, one layer along.
    """
    return f"""
        SELECT * EXCEPT (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY _sort_by DESC) AS rn
            FROM {history_table}
            WHERE _pg_change_type <> 'update_preimage' AND _sort_by > {since}
        )
        WHERE rn = 1
    """


FACTS = {
    # fact table            (history table,                              key)
    "gold_agent_action": (f"{CDC}.lb_fleetguard_agent_action_history", "action_id"),
    "gold_defect_signal_current": (
        f"{CDC}.lb_fleetguard_defect_signal_history",
        "signal_id",
    ),
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Incremental, with the full rebuild kept rather than deleted
# MAGIC
# MAGIC This job used to `CREATE OR REPLACE TABLE` both facts from the entire history on every
# MAGIC run — re-runnable, but a full recompute of a monotonically growing append-only log
# MAGIC every time an agent writes one row.
# MAGIC
# MAGIC It now reads a `_sort_by` high-water mark out of `ops_cdf_fact_refresh`, ranks only the
# MAGIC slice above it, and `MERGE`s. **`_sort_by` is `BIGINT`**, so `>` is safe — no
# MAGIC lexicographic hazard (`CLAUDE.md`).
# MAGIC
# MAGIC ### Why not `AUTO CDC INTO` (E-16)
# MAGIC
# MAGIC E-16 scoped exactly this conversion and deferred it, saying to revisit *"if
# MAGIC `21_cdf_to_gold_facts.py` needs to change for another reason anyway"*. This is that
# MAGIC reason — and the answer is still no, on the same grounds, now stronger. E-16's blocking
# MAGIC objection was that `AUTO CDC INTO`'s ranking is opaque engine internals, so the I-080
# MAGIC regression guard would have to be dropped or rebuilt elsewhere. A hand-written `MERGE`
# MAGIC keeps the guard, keeps all three assertions, and still delivers incrementality. The
# MAGIC thing E-16 was trading away is the thing this notebook exists to protect.
# MAGIC
# MAGIC ### The watermark hazard, and what catches it
# MAGIC
# MAGIC A watermark assumes nothing arrives below it later. That is an assumption, not a
# MAGIC guarantee, so it is **not** trusted: every run still reconciles the fact against the
# MAGIC *entire* history. If the cheap path and the full count disagree, the fact is rebuilt
# MAGIC from scratch, the drift is recorded, and the run fails so a human looks. Correct data
# MAGIC and a loud noise, rather than either one alone.

# COMMAND ----------

FULL_REFRESH = False
try:
    dbutils.widgets.dropdown("full_refresh", "false", ["false", "true"], "Force a full rebuild")
    FULL_REFRESH = dbutils.widgets.get("full_refresh") == "true"
except Exception:  # noqa: BLE001 - not running under Databricks
    pass


def table_exists(name: str) -> bool:
    return spark.catalog.tableExists(f"{UC}.{name}")


def last_watermark(fact: str) -> int | None:
    """Highest `_sort_by` this fact has already consumed, or None if unknown.

    None means "no usable watermark" and forces a full rebuild — which is the correct
    reading for a first run, for a fact table that does not exist yet, and for the rows
    written before this column existed (they carry NULL).
    """
    if not table_exists("ops_cdf_fact_refresh"):
        return None
    cols = {f.name for f in spark.table(f"{UC}.ops_cdf_fact_refresh").schema.fields}
    if "watermark" not in cols:
        return None
    row = spark.sql(f"""
        SELECT MAX(watermark) AS wm FROM {UC}.ops_cdf_fact_refresh
        WHERE fact_table = '{fact}' AND watermark IS NOT NULL
    """).collect()[0]
    return None if row.wm is None else int(row.wm)


modes = {}
for fact, (history, key) in FACTS.items():
    wm = None if FULL_REFRESH else last_watermark(fact)
    if wm is None or not table_exists(fact):
        reason = "full_refresh requested" if FULL_REFRESH else (
            "no fact table yet" if not table_exists(fact) else "no stored watermark"
        )
        spark.sql(f"CREATE OR REPLACE TABLE {fact} AS {latest_per_key(history, key)}")
        modes[fact] = f"FULL ({reason})"
    else:
        pending = spark.sql(
            f"SELECT COUNT(*) AS n FROM {history} WHERE _sort_by > {wm}"
        ).collect()[0].n
        if pending == 0:
            modes[fact] = f"SKIP (nothing above watermark {wm})"
        else:
            spark.sql(changed_keys_in_slice(history, key, wm)).createOrReplaceTempView(
                "_slice"
            )
            # DELETE and UPSERT in one statement so a key that was deleted and re-inserted
            # inside the same slice resolves to its latest state, not to both actions.
            spark.sql(f"""
                MERGE INTO {fact} AS t
                USING _slice AS s
                  ON t.{key} = s.{key}
                WHEN MATCHED AND s._pg_change_type = 'delete' THEN DELETE
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED AND s._pg_change_type <> 'delete' THEN INSERT *
            """)
            modes[fact] = f"INCREMENTAL ({pending} rows above watermark {wm})"

    print(f"{fact:<28} {modes[fact]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify by arithmetic, not by "it ran"
# MAGIC
# MAGIC The rule this project has earned ten times over: never infer correctness from the
# MAGIC absence of an exception. For a change stream the checkable invariant is
# MAGIC **distinct keys = live keys + deleted keys**, with no key on both sides. If that holds,
# MAGIC the split is right; if it does not, the ranking is wrong and the fact table is a guess.

# COMMAND ----------

drift: dict[str, tuple[int, int]] = {}

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

    # THE WATERMARK CHECK. `live_keys` here is computed over the ENTIRE history, so this
    # compares the cheap incremental result against the expensive truth on every run. A
    # watermark assumes nothing ever arrives below it; that is an assumption, and this is
    # what stops it being an unexamined one.
    #
    # On a mismatch: repair the data (full rebuild), record that it happened, and still
    # fail. Repairing silently would make an incremental path that quietly drifts look
    # identical to one that works — which is precisely the failure mode this whole notebook
    # was written in response to (I-080).
    if r.fact_rows != r.live_keys:
        drift[fact] = (r.fact_rows, r.live_keys)
        print(
            f"    DRIFT — incremental left {r.fact_rows} rows but {r.live_keys} keys are "
            f"live. Rebuilding {fact} from full history."
        )
        spark.sql(f"CREATE OR REPLACE TABLE {fact} AS {latest_per_key(history, key)}")
        rebuilt = spark.table(fact).count()
        assert rebuilt == r.live_keys, (
            f"{fact}: even a full rebuild wrote {rebuilt} rows for {r.live_keys} live keys "
            "— the ranking itself is wrong, not the watermark"
        )
        print(f"    rebuilt to {rebuilt} rows")
        fact_rows = rebuilt
    else:
        fact_rows = r.fact_rows

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
        assert old > fact_rows, (
            f"{fact}: the superseded pattern should over-count while deletes exist "
            f"(got {old} vs {fact_rows}) — if these now agree, re-check I-080"
        )
        print(f"    (superseded pattern would have returned {old} — I-080 regression held)")

if drift:
    raise RuntimeError(
        "incremental CDF->gold drifted from the full history and was rebuilt: "
        + "; ".join(f"{f} had {got} rows, {want} keys live" for f, (got, want) in drift.items())
        + ". The fact tables are correct now — the rebuild fixed them — but the watermark "
        "path produced a wrong answer, which means an event arrived below a watermark that "
        "had already advanced past it. Investigate before trusting the incremental path; "
        "re-run with full_refresh=true in the meantime."
    )

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

from datetime import UTC, datetime

# `watermark` and `mode` are new as of 2026-09-20. `mergeSchema` is required because the
# table predates them; the rows written before carry NULL, which `last_watermark()` reads
# as "no usable watermark" and therefore as "rebuild fully" — the safe direction.
rows = []
for fact, (history, _) in FACTS.items():
    m = spark.sql(f"SELECT MAX(_timestamp) AS newest FROM {history}").collect()[0].newest
    wm = spark.sql(f"SELECT MAX(_sort_by) AS wm FROM {history}").collect()[0].wm
    n = spark.table(fact).count()
    rows.append((fact, history, n, m, datetime.now(UTC), wm, modes[fact].split(" ")[0]))

spark.createDataFrame(
    rows,
    "fact_table STRING, source_history STRING, fact_rows BIGINT, "
    "newest_change TIMESTAMP, refreshed_at TIMESTAMP, watermark BIGINT, mode STRING",
).write.mode("append").option("mergeSchema", "true").saveAsTable("ops_cdf_fact_refresh")

display(
    spark.sql("""
        SELECT fact_table, mode, fact_rows, watermark, newest_change, refreshed_at,
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

# COMMAND ----------

# MAGIC %md
# MAGIC ## Aggregates — what the system *did*, not just what it currently holds
# MAGIC
# MAGIC Both facts above are current-state: one row per live key. Nothing anywhere aggregated
# MAGIC activity **over time**, so questions like "how many agent requests last week", "which
# MAGIC tool is used most", "what share of calls succeed" had no table to answer them.
# MAGIC
# MAGIC Two rollups, derived here because this is the job that already runs whenever the
# MAGIC underlying data changes.
# MAGIC
# MAGIC ### Read `gold_agent_activity_daily` honestly
# MAGIC
# MAGIC **It is nearly empty, and that is the true number.** `gold_agent_action` holds a
# MAGIC handful of rows because the agent's write path has been exercised deliberately, a few
# MAGIC times, by one person. The table is not seeded and must not be: a full-looking chart
# MAGIC built from invented agent traffic would be exactly the kind of claim this project
# MAGIC refuses to make elsewhere (I-051, E-15). It grows when the agent is used.
# MAGIC
# MAGIC Three things about where the numbers come from:
# MAGIC
# MAGIC - **Latency and tokens do NOT come from `gold_agent_action`.** Its `latency_ms`,
# MAGIC   `input_tokens` and `output_tokens` columns are deliberately left NULL (E-04) — the
# MAGIC   MLflow inference table is the single source, and a second copy invites the two to
# MAGIC   disagree. They come from the join instead.
# MAGIC - **Token extraction is anchored, not JSONPath'd.** The documented path
# MAGIC   `$.databricks_output.trace.info.trace_metadata['mlflow.trace.tokenUsage']` does not
# MAGIC   work — the dotted key is read as nesting, and the value is a JSON string needing a
# MAGIC   second parse. A response also carries **3–7** token blocks, one per LLM call in the
# MAGIC   agent's tool loop, so a naive `regexp_extract` matches an arbitrary one. This anchors
# MAGIC   on the trace-level block with `INSTR`, the same way the dashboard's `ds_ops_agent`
# MAGIC   dataset does (E-04).
# MAGIC - **The join is partial by construction.** Inference-table ingest lags >30 min, and
# MAGIC   every action written before 2026-09-09 has a NULL `trace_id` (E-03). So
# MAGIC   `traced_requests` is reported next to `requests` rather than folded into it, and a
# MAGIC   success rate over zero traced requests is NULL, not 0% or 100%.

# COMMAND ----------

PAYLOAD = f"{UC}.fleetguard_agent_payload"
has_payload = spark.catalog.tableExists(PAYLOAD)
print(f"inference table {'found' if has_payload else 'ABSENT'} — {PAYLOAD}")

# The agent's two write tools. Kept in one place here and in agent_actions.py; if a third
# write tool is ever added, this list is one of the two things that must change.
WRITE_TOOLS = ("open_defect_signal", "watch_campaign")
_write_sql = ", ".join(f"'{t}'" for t in WRITE_TOOLS)

usage_cte = (
    f"""
    usage AS (
      SELECT databricks_request_id,
             execution_duration_ms,
             status_code,
             SUBSTR(response, INSTR(response, 'mlflow.trace.tokenUsage'), 200) AS tu
      FROM {PAYLOAD}
    )
    """
    if has_payload
    else """
    usage AS (
      SELECT CAST(NULL AS STRING) AS databricks_request_id,
             CAST(NULL AS BIGINT) AS execution_duration_ms,
             CAST(NULL AS INT)    AS status_code,
             CAST(NULL AS STRING) AS tu
      WHERE FALSE
    )
    """
)

spark.sql(f"""
CREATE OR REPLACE TABLE gold_agent_activity_daily
COMMENT 'Agent activity rolled up by day, tool and actor: requests, write actions, success rate, latency percentiles and token cost. Latency/tokens come from the MLflow inference table (E-04), never from gold_agent_action, whose corresponding columns are deliberately NULL. traced_requests is reported separately because the join is partial: inference ingest lags >30 min and pre-2026-09-09 actions carry no trace_id.'
AS
WITH {usage_cte},
joined AS (
  SELECT
    DATE(a.created_at)                        AS activity_date,
    a.tool                                    AS tool,
    COALESCE(a.on_behalf_of, a.actor_principal) AS actor,
    a.action_id                               AS action_id,
    a.trace_id                                AS trace_id,
    u.status_code                             AS status_code,
    u.execution_duration_ms                   AS duration_ms,
    CAST(regexp_extract(u.tu, 'total_tokens[^0-9]+([0-9]+)', 1) AS INT) AS total_tokens
  FROM gold_agent_action a
  LEFT JOIN usage u ON u.databricks_request_id = a.trace_id
)
SELECT
  activity_date,
  tool,
  actor,
  COUNT(*)                                                    AS requests,
  COUNT_IF(tool IN ({_write_sql}))                            AS write_actions,
  COUNT(trace_id)                                             AS traced_requests,
  COUNT(status_code)                                          AS scored_requests,
  -- NULL, not 0, when nothing is traced: "we don't know" and "everything failed" are
  -- different answers and a dashboard must not render them the same.
  CASE WHEN COUNT(status_code) = 0 THEN NULL
       ELSE ROUND(100.0 * COUNT_IF(status_code = 200) / COUNT(status_code), 1)
  END                                                         AS success_rate_pct,
  CAST(PERCENTILE(duration_ms, 0.5) AS BIGINT)                AS p50_duration_ms,
  CAST(PERCENTILE(duration_ms, 0.95) AS BIGINT)               AS p95_duration_ms,
  SUM(total_tokens)                                           AS total_tokens
FROM joined
GROUP BY activity_date, tool, actor
""")

n = spark.table("gold_agent_activity_daily").count()
print(f"gold_agent_activity_daily: {n} rows")

# Reconcile against the fact it is derived from — the rollup must account for every action.
recon = spark.sql("""
    SELECT (SELECT COALESCE(SUM(requests), 0) FROM gold_agent_activity_daily) AS rolled_up,
           (SELECT COUNT(*) FROM gold_agent_action)                           AS actions
""").collect()[0]
assert recon.rolled_up == recon.actions, (
    f"gold_agent_activity_daily sums to {recon.rolled_up} requests but gold_agent_action "
    f"holds {recon.actions} rows — the GROUP BY is dropping or duplicating actions"
)
print(f"  reconciles: {recon.rolled_up} requests == {recon.actions} actions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## API usage and failure rates
# MAGIC
# MAGIC The other half of "what did the system do": how the third-party integration is
# MAGIC actually behaving over time. Sourced from `ops_recall_api_sweep`, which
# MAGIC `05_poll_recalls_api.py` writes one row per sweep (I-106).
# MAGIC
# MAGIC **A row is an attempt, not a run.** The poll task retries twice, and each retry is a
# MAGIC real sweep that really called the API, so counting them separately is the honest
# MAGIC reading — a job that needed three attempts did three sweeps' worth of requests.

# COMMAND ----------

if spark.catalog.tableExists(f"{UC}.ops_recall_api_sweep"):
    spark.sql("""
    CREATE OR REPLACE TABLE gold_api_poll_health
    COMMENT 'Recall-API sweep health by day: attempts, combos polled, success and failure rates by class, retry volume, and how often the failure gate blocked the gold_recall_alert rebuild. One source row per sweep attempt, not per job run.'
    AS
    SELECT
      DATE(started_at)                                  AS poll_date,
      COUNT(*)                                          AS sweep_attempts,
      SUM(combos)                                       AS combos_polled,
      SUM(ok)                                           AS combos_ok,
      SUM(retried)                                      AS combos_recovered_by_retry,
      SUM(http_4xx)                                     AS combos_http_4xx,
      SUM(http_5xx)                                     AS combos_http_5xx,
      SUM(malformed)                                    AS combos_malformed,
      SUM(errors)                                       AS combos_errored,
      ROUND(100.0 * SUM(ok) / NULLIF(SUM(combos), 0), 2) AS success_rate_pct,
      ROUND(AVG(sweep_seconds), 1)                      AS avg_sweep_seconds,
      SUM(campaign_rows)                                AS campaign_rows_returned,
      COUNT_IF(NOT alert_table_refreshed)               AS gated_attempts
    FROM ops_recall_api_sweep
    GROUP BY DATE(started_at)
    """)
    h = spark.table("gold_api_poll_health").count()
    print(f"gold_api_poll_health: {h} day(s)")
    display(spark.sql("SELECT * FROM gold_api_poll_health ORDER BY poll_date DESC LIMIT 14"))
else:
    print(
        "ops_recall_api_sweep does not exist yet — run fleetguard-poll-recalls-api first. "
        "Skipping gold_api_poll_health rather than creating an empty table that looks real."
    )
