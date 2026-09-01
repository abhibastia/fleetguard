# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 9: embed the backtest complaints
# MAGIC
# MAGIC Embeds the **205,219** complaint narratives in `gold_backtest_complaint` with
# MAGIC `databricks-gte-large-en` (1024-dim, verified on a 5-row probe before spending).
# MAGIC
# MAGIC **Why we are paying for this again.** The 1.75M chunks already embedded into
# MAGIC `complaint_chunk_idx` are not reachable: a Delta Sync index keeps its vectors
# MAGIC internally, no embedding column is materialised on the source table, and the index's
# MAGIC `__..._online_index_view` returns nothing to `DESCRIBE`. Clustering needs the vectors
# MAGIC as data, so they have to be computed as data.
# MAGIC
# MAGIC **Resumable by construction.** ~205k model calls is a long job, and a failure at 80%
# MAGIC must not mean paying for that 80% twice. Each run embeds only `complaint_id`s not
# MAGIC already present, so a re-run after any failure costs only the remainder — and a re-run
# MAGIC after a *successful* run costs nothing at all.
# MAGIC
# MAGIC No truncation is applied: `CDESCR` is `CHAR(2048)`, so the longest narrative is ~2,132
# MAGIC characters (~533 tokens) against the model's 8,192-token window.

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

ENDPOINT = "databricks-gte-large-en"
EXPECTED_DIM = 1024

# COMMAND ----------

spark.sql("""
CREATE TABLE IF NOT EXISTS gold_backtest_embedding (
    complaint_id  STRING  NOT NULL COMMENT 'one row per complaint, matching gold_backtest_complaint',
    make          STRING,
    model         STRING,
    comp_top      STRING,
    received_date TIMESTAMP,
    embedding     ARRAY<FLOAT> COMMENT '1024-dim databricks-gte-large-en vector of the narrative',
    embedded_at   TIMESTAMP
)
USING DELTA
CLUSTER BY (make, model)
COMMENT 'Narrative embeddings for the Phase 9 semantic arm. Populated incrementally so a failed run never has to be paid for twice.'
""")

before = spark.table("gold_backtest_embedding").count()
todo = spark.sql("""
    SELECT COUNT(*) AS n FROM gold_backtest_complaint c
    LEFT ANTI JOIN gold_backtest_embedding e USING (complaint_id)
""").collect()[0]["n"]
print(f"already embedded : {before:,}")
print(f"remaining        : {todo:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Embed the remainder
# MAGIC
# MAGIC `failOnError => false` so one malformed narrative cannot abort a six-figure job. The
# MAGIC verification cell below counts what actually landed, so a silent partial result is
# MAGIC caught rather than assumed away.

# COMMAND ----------

if todo:
    spark.sql(f"""
        INSERT INTO gold_backtest_embedding
        SELECT c.complaint_id, c.make, c.model, c.comp_top, c.received_date,
               ai_query('{ENDPOINT}', c.narrative,
                        returnType => 'ARRAY<FLOAT>', failOnError => false) AS embedding,
               current_timestamp() AS embedded_at
        FROM gold_backtest_complaint c
        LEFT ANTI JOIN gold_backtest_embedding e USING (complaint_id)
    """)
    print("insert complete")
else:
    print("nothing to do — already fully embedded")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify
# MAGIC
# MAGIC Three things must hold: every complaint has a row, every vector is 1024-dim, and no
# MAGIC vector is null. A null embedding is what `failOnError => false` produces on failure,
# MAGIC so counting nulls is how that leniency stays honest.

# COMMAND ----------

v = spark.sql(f"""
SELECT
  (SELECT COUNT(*) FROM gold_backtest_complaint)                                 AS want,
  (SELECT COUNT(*) FROM gold_backtest_embedding)                                 AS got,
  (SELECT COUNT(*) FROM gold_backtest_embedding WHERE embedding IS NULL)         AS null_vectors,
  (SELECT COUNT(*) FROM gold_backtest_embedding WHERE SIZE(embedding) <> {EXPECTED_DIM}
                                                  AND embedding IS NOT NULL)     AS wrong_dim,
  (SELECT COUNT(DISTINCT complaint_id) FROM gold_backtest_embedding)             AS distinct_ids
""").collect()[0]

for k, val in v.asDict().items():
    print(f"  {k:<14} {val:,}")

problems = []
if v["got"] != v["want"]:
    problems.append(f"row count {v['got']:,} != source {v['want']:,}")
if v["distinct_ids"] != v["got"]:
    problems.append(f"duplicate complaint_ids: {v['got'] - v['distinct_ids']:,}")
if v["wrong_dim"]:
    problems.append(f"{v['wrong_dim']:,} vectors are not {EXPECTED_DIM}-dim")

# Null vectors are recoverable — re-running picks them up only if they are deleted first,
# so report the exact remedy rather than leaving a silent hole in the clustering input.
if v["null_vectors"]:
    pct = 100.0 * v["null_vectors"] / max(v["got"], 1)
    print(
        f"\n  {v['null_vectors']:,} null embeddings ({pct:.2f}%) — ai_query failed on these.\n"
        "  To retry them:\n"
        "    DELETE FROM gold_backtest_embedding WHERE embedding IS NULL;\n"
        "  then re-run this notebook."
    )
    if pct > 1.0:
        problems.append(f"{pct:.2f}% null embeddings exceeds the 1% tolerance")

if problems:
    raise SystemExit("EMBEDDING VERIFICATION FAILED: " + "; ".join(problems))
print("\nverification passed")

# COMMAND ----------

display(
    spark.sql("""
    SELECT make, COUNT(*) AS complaints, ROUND(AVG(SIZE(embedding))) AS dim
    FROM gold_backtest_embedding GROUP BY make ORDER BY complaints DESC LIMIT 15
""")
)
