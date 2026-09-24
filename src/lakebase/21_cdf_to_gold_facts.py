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

from datetime import UTC, datetime

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
# Kept, not discarded, because the reconciliation below needs to know which slice this run was
# responsible for. Before the I-115 split it did not — it re-checked everything every time.
watermarks: dict[str, int | None] = {}
for fact, (history, key) in FACTS.items():
    wm = None if FULL_REFRESH else last_watermark(fact)
    watermarks[fact] = wm
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
# MAGIC
# MAGIC ### Counting rows is not checking them
# MAGIC
# MAGIC That invariant, and the `fact_rows == live_keys` check below it, are both **cardinality**
# MAGIC tests — and this cell used to describe them as though they proved the fact table matched
# MAGIC the history. They do not. Consider:
# MAGIC
# MAGIC ```
# MAGIC history: signal X -> OPEN, then signal X -> COMPLETED
# MAGIC gold:    signal X -> OPEN
# MAGIC ```
# MAGIC
# MAGIC One key, one row, on both sides. Every count agrees. The fact table is wrong.
# MAGIC
# MAGIC This is exactly the drift an incremental path can produce and a full rebuild cannot —
# MAGIC a missed `update_postimage` above the watermark changes a value without changing a
# MAGIC count — so the one check guarding the watermark has to compare **content**, not just how
# MAGIC much of it there is. Below, each fact is fingerprinted against the full-history truth:
# MAGIC every column, hashed per row, combined with `bit_xor` so the comparison does not depend
# MAGIC on row order. Same rule as the counts — on a mismatch, rebuild, record, and still fail.
# MAGIC
# MAGIC ### The fingerprint runs in two tiers, not on every row every time (I-115)
# MAGIC
# MAGIC The check above was written to run **unscoped on every trigger**, which handed back much
# MAGIC of what the incremental path had just bought: a one-row agent write caused every column
# MAGIC of every row of both sides to be hashed. External review flagged it, and the deferral
# MAGIC reason recorded at the time — *"the trigger is PAUSED so the cost is not being paid"* —
# MAGIC was **wrong**: `resources/cdf_to_gold.job.yml` declares `pause_status: UNPAUSED` and has
# MAGIC since 2026-09-08. The cost was near zero only because the App was stopped.
# MAGIC
# MAGIC So it is split:
# MAGIC
# MAGIC | | scope | when | catches |
# MAGIC |---|---|---|---|
# MAGIC | cardinality | full history | **every run** | a key count that does not add up |
# MAGIC | content fingerprint — scoped | keys above the previous watermark | **every run** | a wrong value written by *this* run's MERGE |
# MAGIC | content fingerprint — unscoped | everything | every `FULL_RECONCILE_HOURS` (24), and on any full rebuild | an event that arrived **below** an advanced watermark |
# MAGIC
# MAGIC **The scoped check is not a weaker version of the unscoped one.** It is a complete check
# MAGIC of the rows the incremental path actually touched. What it cannot see is the failure the
# MAGIC watermark itself causes — a late event below the mark is not in a slice defined by that
# MAGIC mark — and that is exactly why the unscoped one is kept on a clock rather than dropped.
# MAGIC A guard that shares its subject's assumption is not a guard.
# MAGIC
# MAGIC After a drift rebuild the re-check is **always unscoped**, whatever the run was using:
# MAGIC a rebuild replaces every row, so verifying one slice of it would leave the rest
# MAGIC unexamined at the exact moment there is reason to doubt it.

# COMMAND ----------

drift: dict[str, str] = {}

# How often the EXPENSIVE check runs. The per-trigger check below is scoped to the keys a run
# actually touched; this is the unscoped one, and 24 h is chosen so that a fact table cannot sit
# wrong for longer than a day even if every incremental run's scope missed the damage.
FULL_RECONCILE_HOURS = 24


def full_reconcile_due(fact: str) -> tuple[bool, str]:
    """Has the unscoped fingerprint run for this fact recently enough?

    Reads `ops_cdf_fact_refresh`, the same table the watermark comes from, so "when did this
    last fully reconcile?" is answerable from a query rather than from someone's memory. An
    unreadable or absent history means **due** — the safe direction, and the same reading
    `last_watermark()` gives a NULL.
    """
    if not table_exists("ops_cdf_fact_refresh"):
        return True, "no refresh history"
    cols = {f.name for f in spark.table(f"{UC}.ops_cdf_fact_refresh").schema.fields}
    if "full_reconcile" not in cols:
        # The column is new; every row written before it exists carries NULL, which cannot be
        # read as "a full reconcile happened".
        return True, "refresh history predates full_reconcile"
    # Age computed in SQL, not in Python. `refreshed_at` comes back through the Spark session
    # timezone, so subtracting it from a `datetime.now(UTC)` would be right only as long as
    # that timezone happens to be UTC — a silent, environment-dependent way to get the clock
    # wrong, and this project has already been bitten once by a timezone comparison that was
    # invisible from the timezone it was written in (I-087).
    row = spark.sql(f"""
        SELECT ROUND(
                 (BIGINT(current_timestamp()) - BIGINT(MAX(refreshed_at))) / 3600.0, 2
               ) AS age_h
        FROM {UC}.ops_cdf_fact_refresh
        WHERE fact_table = '{fact}' AND full_reconcile = true
    """).collect()[0]
    if row.age_h is None:
        return True, "never fully reconciled"
    if row.age_h >= FULL_RECONCILE_HOURS:
        return True, f"last full reconcile {row.age_h} h ago"
    return False, f"last full reconcile {row.age_h} h ago"


def fingerprints(
    fact: str, history: str, key: str, *, only_keys: str | None = None
) -> tuple[int | None, int | None]:
    """(truth, fact) content fingerprints — order-independent, over every column.

    `bit_xor` rather than a sum: it cannot overflow, and it does not depend on the order
    rows come back in. Columns are listed **explicitly and sorted**, not `struct(*)`, so
    the two sides cannot disagree merely because a MERGE left the fact table's column
    order different from the history's.

    `only_keys` is a SQL predicate restricting BOTH sides to the same key set — that
    symmetry is what keeps a scoped comparison meaningful. Applied to one side only it
    would compare a subset against a whole and fail every time.

    Returns `(None, None)` for an empty pair — `bit_xor` over no rows is NULL, and two
    empty tables genuinely do agree. A scoped run where nothing changed lands here, which
    is correct: there is nothing to disagree about.
    """
    cols = ", ".join(f"`{c}`" for c in sorted(spark.table(fact).columns))
    where = f" WHERE {only_keys}" if only_keys else ""
    spark.sql(latest_per_key(history, key)).createOrReplaceTempView("_truth")
    row = spark.sql(f"""
        SELECT
          (SELECT bit_xor(xxhash64(to_json(struct({cols})))) FROM _truth{where})  AS truth_fp,
          (SELECT bit_xor(xxhash64(to_json(struct({cols})))) FROM {fact}{where})  AS fact_fp
    """).collect()[0]
    return row.truth_fp, row.fact_fp


scopes: dict[str, str] = {}

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

    # THE WATERMARK CHECK, in two tiers. `live_keys` above is computed over the ENTIRE history
    # on every run, so the cardinality half is always unscoped and always cheap. The content
    # half is the expensive one, and it is the one that got split (I-115): hashing every column
    # of every row of both sides on each trigger gave back much of what the incremental path
    # bought.
    #
    # Per trigger: fingerprint ONLY the keys this run could have got wrong — those above the
    # previous watermark. That is not a weaker check of the same thing, it is a complete check
    # of the thing the incremental path actually risks.
    #
    # Periodically: the unscoped fingerprint, because the scoped one shares the watermark's own
    # assumption. An event arriving BELOW a watermark that has already advanced is invisible to
    # a slice defined by that watermark — which is precisely the failure being guarded against,
    # so it cannot be the only guard.
    wm = watermarks.get(fact)
    due, why = full_reconcile_due(fact)
    do_full = due or FULL_REFRESH or modes[fact].startswith("FULL") or wm is None
    if do_full:
        scope_sql, scopes[fact] = None, "FULL"
        reason = "forced" if FULL_REFRESH else ("rebuilt this run" if modes[fact].startswith("FULL") else why)
        print(f"    full reconcile ({reason})")
    else:
        # The keys touched above the watermark, as a predicate both sides are filtered by.
        scope_sql = (
            f"{key} IN (SELECT DISTINCT {key} FROM {history} WHERE _sort_by > {wm})"
        )
        scopes[fact] = "SCOPED"
        print(f"    scoped reconcile (keys above watermark {wm}) — {why}")

    truth_fp, fact_fp = fingerprints(fact, history, key, only_keys=scope_sql)
    print(f"    fingerprint truth={truth_fp} fact={fact_fp}")

    reasons = []
    if r.fact_rows != r.live_keys:
        reasons.append(f"cardinality ({r.fact_rows} rows for {r.live_keys} live keys)")
    if truth_fp != fact_fp:
        # The case counts cannot see: right number of rows, wrong values in them.
        reasons.append(f"content ({scopes[fact].lower()} fingerprint {fact_fp} != {truth_fp})")

    if reasons:
        drift[fact] = " and ".join(reasons)
        print(f"    DRIFT — {drift[fact]}. Rebuilding {fact} from full history.")
        spark.sql(f"CREATE OR REPLACE TABLE {fact} AS {latest_per_key(history, key)}")
        rebuilt = spark.table(fact).count()
        assert rebuilt == r.live_keys, (
            f"{fact}: even a full rebuild wrote {rebuilt} rows for {r.live_keys} live keys "
            "— the ranking itself is wrong, not the watermark"
        )
        # Re-fingerprint after the rebuild, and UNSCOPED regardless of what the check above
        # used: a rebuild replaces every row, so verifying only the slice would leave the rest
        # of the table unexamined at exactly the moment there is reason to doubt it. If THIS
        # disagrees the derivation itself is broken, not the watermark, and there is nothing
        # left to fall back on.
        rebuilt_truth_fp, rebuilt_fact_fp = fingerprints(fact, history, key)
        assert rebuilt_truth_fp == rebuilt_fact_fp, (
            f"{fact}: a full rebuild still does not match the history it was built from "
            f"({rebuilt_fact_fp} != {rebuilt_truth_fp}) — `latest_per_key` is wrong"
        )
        print(f"    rebuilt to {rebuilt} rows, fingerprint {rebuilt_fact_fp}")
        scopes[fact] = "FULL"
        fact_rows = rebuilt
    else:
        fact_rows = r.fact_rows

    # The regression that motivated this notebook. The old pattern filtered deletes before
    # ranking; if it ever creeps back, this fires rather than silently inflating the fact.
    #
    # Runs on FULL reconciles only, and that is a deliberate scoping rather than a saving: this
    # guards against a **code** change, not against data drift, so it can only start failing
    # after a deploy — and a deploy is always followed by a run that reconciles fully, because
    # `full_reconcile_due` reads a history the new code has not written to yet.
    if scopes[fact] == "FULL":
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
        + "; ".join(f"{f} drifted on {why}" for f, why in drift.items())
        + ". The fact tables are correct now — the rebuild fixed them — but the watermark "
        "path produced a wrong answer, which means an event arrived below a watermark that "
        "had already advanced past it. Investigate before trusting the incremental path; "
        "re-run with full_refresh=true in the meantime. A CONTENT drift is the more serious "
        "of the two: the row count was right, so every check this job had before 2026-09-20 "
        "would have passed while the fact table held stale values."
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

# `watermark` and `mode` are new as of 2026-09-20. `mergeSchema` is required because the
# table predates them; the rows written before carry NULL, which `last_watermark()` reads
# as "no usable watermark" and therefore as "rebuild fully" — the safe direction.
# `reconcile_scope` and `full_reconcile` are new as of 2026-09-24 (I-115) and are what
# `full_reconcile_due()` reads back on the next run — so the 24 h clock is stored in the same
# table as the watermark rather than kept in anyone's head. Rows written before these columns
# existed carry NULL, which reads as "never fully reconciled" and therefore as "do it now".
rows = []
for fact, (history, _) in FACTS.items():
    m = spark.sql(f"SELECT MAX(_timestamp) AS newest FROM {history}").collect()[0].newest
    wm = spark.sql(f"SELECT MAX(_sort_by) AS wm FROM {history}").collect()[0].wm
    n = spark.table(fact).count()
    rows.append((
        fact, history, n, m, datetime.now(UTC), wm, modes[fact].split(" ")[0],
        scopes[fact], scopes[fact] == "FULL",
    ))

spark.createDataFrame(
    rows,
    "fact_table STRING, source_history STRING, fact_rows BIGINT, "
    "newest_change TIMESTAMP, refreshed_at TIMESTAMP, watermark BIGINT, mode STRING, "
    "reconcile_scope STRING, full_reconcile BOOLEAN",
).write.mode("append").option("mergeSchema", "true").saveAsTable("ops_cdf_fact_refresh")

display(
    spark.sql("""
        SELECT fact_table, mode, reconcile_scope, fact_rows, watermark,
               newest_change, refreshed_at,
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
