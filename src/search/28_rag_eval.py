# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — RAG retrieval evaluation (I-115)
# MAGIC
# MAGIC **Why this exists.** Until now the strongest claim this project could make about
# MAGIC retrieval was *"AI Search is implemented"*, plus a three-query behavioural probe
# MAGIC (`09_hybrid_query_test.py`) showing HYBRID differs from ANN. That proves the feature is
# MAGIC wired up. It does not say whether it **works**, and a graded category called "RAG" is
# MAGIC not answered by a screenshot of an index that reports `ready: true`.
# MAGIC
# MAGIC This was deferred at I-110 with a stated cost: *"~7 h to re-embed 1,746,601 chunks, no
# MAGIC retry budget before 4 October."* That reason expired the day I-111 rescoped the index to
# MAGIC 115,499 chunks (~39 min) and nobody re-read it — the lesson I-115 was filed for. The
# MAGIC index is rebuilt in Run 2 regardless, so this now costs nothing extra to measure.
# MAGIC
# MAGIC ## What is measured, and what is NOT
# MAGIC
# MAGIC Two probe families, because either alone is misleading:
# MAGIC
# MAGIC **A — known-item.** Query is a distinctive excerpt from the middle of one complaint
# MAGIC narrative; the relevant set is that single complaint. Recall@k and MRR are meaningful
# MAGIC here because the relevant set has exactly one member. This is a **floor test**: the text
# MAGIC is literally in the corpus, so a hybrid retriever should find it nearly always. A system
# MAGIC that fails this is broken, and a system that passes it has not yet proved anything
# MAGIC interesting — which is why family B exists.
# MAGIC
# MAGIC **B — topical.** Query is natural language built from a real recall campaign (its
# MAGIC component and vehicle); relevant = every chunk for that vehicle and component. **Recall@k
# MAGIC is deliberately not the headline for this family** — the relevant set can run to
# MAGIC thousands of chunks, so Recall@10 would read as ~0.003 and mean nothing. The honest
# MAGIC metrics here are **Precision@k** (how much of the top k is on target) and **hit rate**
# MAGIC (did anything on target surface at all). Both are reported for both families anyway, so
# MAGIC nobody has to take this notebook's word for which one to read.
# MAGIC
# MAGIC **The limitation, stated rather than buried:** relevance is judged by *metadata*
# MAGIC agreement — same make, same model under the project's EXACT/MODEL_VARIANT rule, matching
# MAGIC component — not by a human reading the narrative. So this measures whether retrieval
# MAGIC surfaces evidence about the right vehicle and the right system. It does **not** measure
# MAGIC whether a returned narrative is a good answer to the question. That would need human
# MAGIC adjudication, which is the same thing the Model B golden set is honest about lacking.
# MAGIC
# MAGIC ## The rule this notebook inherits
# MAGIC
# MAGIC **Publish what it measures, including if it is poor.** I-049 published a falsified
# MAGIC semantic hypothesis; I-111 required the hybrid probe be re-run and republished at the
# MAGIC smaller scope *including if it came back worse*. A retrieval evaluation that only gets
# MAGIC written up when the numbers flatter the system is not evidence.
# MAGIC
# MAGIC ## Also closes the outstanding Phase 3 debt
# MAGIC
# MAGIC `ops_hybrid_query_test` was measured at the 1,746,601-chunk scope and has never been
# MAGIC re-run at 115,499+. Section 5 re-runs the three I-040 behavioural checks here, with
# MAGIC probes the **fleet scope can actually answer** — the old paraphrase probe (*"car suddenly
# MAGIC sped up on its own"*) leans on unintended-acceleration complaints concentrated in makes
# MAGIC this fleet may not operate, so a poor result there would have been unreadable.
# MAGIC
# MAGIC **Runs only with a live index.** Everything scoreable is in
# MAGIC `src/fleetguard/retrieval_metrics.py` and unit-tested off platform
# MAGIC (`tests/test_retrieval_metrics.py`), so the logic is not debugged inside a billed window.

# COMMAND ----------

import os
import sys

# Same pattern as 04_build_fleet_registry.py: the bundle uploads the whole `src/` tree, so the
# shared package is importable from a notebook one directory down.
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), "..")))

import datetime as _dt  # noqa: E402
import random  # noqa: E402

from databricks.sdk import WorkspaceClient  # noqa: E402

from fleetguard.retrieval_metrics import (  # noqa: E402
    is_relevant,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarise,
)

dbutils.widgets.text("catalog", "bootcamp_students")
dbutils.widgets.text("schema", "fleetguard")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
INDEX = f"{CATALOG}.{SCHEMA}.complaint_chunk_idx"
SOURCE = f"{CATALOG}.{SCHEMA}.silver_complaint_chunk_indexed"
FLEET = f"{CATALOG}.{SCHEMA}.gold_fleet_vehicle"
RECALLS = f"{CATALOG}.{SCHEMA}.silver_recall"
OPS_TABLE = f"{CATALOG}.{SCHEMA}.ops_rag_eval"

COLS = ["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"]
K = 10
# Fixed so two runs of this notebook over the same index are comparable. A different seed is a
# different evaluation, not a re-run of this one.
SEED = 20260924
N_KNOWN_ITEM = 50
N_TOPICAL = 50

w = WorkspaceClient()

status = w.vector_search_indexes.get_index(index_name=INDEX).status
INDEXED_ROWS = status.indexed_row_count
print(f"index {INDEX}: ready={status.ready} indexed_row_count={INDEXED_ROWS:,}")
# Hard stop rather than a warning. Scoring a partially-synced index produces numbers that look
# like a result and are not one — the exact shape of failure this project keeps finding.
assert status.ready, "index is not ready — scoring a partial sync produces a fake result"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Retrieval helper
# MAGIC
# MAGIC One function, both query types, so HYBRID and ANN cannot accidentally be measured under
# MAGIC different conditions. The CLI cannot read this endpoint (Go SDK unmarshalling bug,
# MAGIC I-040); the Python path works.

# COMMAND ----------


def search(text: str, query_type: str, k: int = K) -> list[dict]:
    r = w.vector_search_indexes.query_index(
        index_name=INDEX,
        columns=COLS,
        query_text=text,
        query_type=query_type,
        num_results=k,
    )
    rows = (r.result.data_array or []) if r.result else []
    cols = [c.name for c in r.manifest.columns] if r.manifest else COLS
    return [dict(zip(cols, row, strict=False)) for row in rows]


# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Probe family A — known-item
# MAGIC
# MAGIC An excerpt from the **middle** of a narrative, not its opening. Openings in this corpus
# MAGIC are formulaic (`THE CONTACT OWNS A ...`), so a query built from one measures how well the
# MAGIC index retrieves boilerplate. The middle is where the actual defect description lives.
# MAGIC
# MAGIC Long narratives only (≥600 characters), because a short one's excerpt is most of the
# MAGIC document and the test degenerates into an exact-text lookup.

# COMMAND ----------

known_rows = spark.sql(f"""
    SELECT complaint_id, chunk_id, make, model, component, chunk_text
    FROM {SOURCE}
    WHERE length(chunk_text) >= 600
    ORDER BY xxhash64(chunk_id, {SEED})
    LIMIT {N_KNOWN_ITEM}
""").collect()

print(f"known-item probes: {len(known_rows)}")

known_probes = []
for row in known_rows:
    text = row.chunk_text
    # Middle slice, ~220 chars. Enough to be distinctive; short enough that the retriever is
    # doing work rather than matching the whole document.
    start = max(0, len(text) // 2 - 110)
    excerpt = text[start : start + 220].strip()
    known_probes.append(
        {"query": excerpt, "relevant": {row.complaint_id}, "complaint_id": row.complaint_id}
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Probe family B — topical, from real recall campaigns
# MAGIC
# MAGIC Campaigns whose vehicle the fleet actually operates, matched with the project's own
# MAGIC two-tier rule. **The predicate is the one from `10_emerging_signals.py`** — the same SQL
# MAGIC I-115 ported into the index source, for the same reason: an exact-spelling join here
# MAGIC would quietly drop every F-250 probe and the evaluation would look fine.

# COMMAND ----------

topical_rows = spark.sql(f"""
    WITH fleet AS (
        SELECT DISTINCT make, model FROM {FLEET}
    ),
    campaigns AS (
        SELECT DISTINCT r.make, r.model, r.component, r.campaign_number AS campaign_id
        FROM {RECALLS} r
        WHERE r.component IS NOT NULL AND length(trim(r.component)) > 0
          AND EXISTS (
              SELECT 1 FROM fleet f
              WHERE f.make = r.make
                AND (f.model = r.model
                     OR r.model LIKE f.model || ' %'
                     OR f.model LIKE r.model || ' %')
          )
    )
    SELECT * FROM campaigns
    ORDER BY xxhash64(campaign_id, {SEED})
    LIMIT {N_TOPICAL}
""").collect()

print(f"topical probes: {len(topical_rows)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### The relevance set must come from the CORPUS, not from the results
# MAGIC
# MAGIC **This was wrong when first written, and wrong in a way that could not fail.** The
# MAGIC first version built each probe's relevant set out of the retrieved hits — *"the things
# MAGIC the retriever returned that I consider relevant"* — and then computed Recall@10 against
# MAGIC it. Since the relevant set was a subset of what was retrieved, the intersection was
# MAGIC always the whole set: **Recall@10 was exactly 1.0 whenever anything on target came
# MAGIC back, and 0.0 otherwise.** A duplicate of the hit-rate indicator wearing the name
# MAGIC "recall", and it would have been written to `ops_rag_eval` and published as a retrieval
# MAGIC result. Caught by external review (I-117).
# MAGIC
# MAGIC Precision@k and MRR were never affected — they depend only on *which returned items are
# MAGIC on target*, not on the relevant set being complete. Recall is the one metric that needs
# MAGIC ground truth independent of the retriever, so it gets one.
# MAGIC
# MAGIC The pool is every complaint in the index source matching the probe's vehicle and
# MAGIC component, by the **same** EXACT + MODEL_VARIANT predicate the rest of the system uses.
# MAGIC One query for all probes, not one per probe.
# MAGIC
# MAGIC **`relevant_pool_size` is published beside recall, and that matters.** A pool of
# MAGIC thousands makes Recall@10 tiny *by construction* — ten slots cannot cover three thousand
# MAGIC documents — and a reader who sees 0.004 without the denominator will read it as broken
# MAGIC retrieval rather than as arithmetic. Precision@10 and hit rate stay the headline.

# COMMAND ----------

pool_rows = spark.sql(f"""
    WITH probes AS (
        SELECT * FROM VALUES
        {
    ",".join(
        "('{}','{}','{}','{}')".format(
            r.campaign_id,
            r.make.replace("'", "''"),
            r.model.replace("'", "''"),
            r.component.replace("'", "''"),
        )
        for r in topical_rows
    )
}
        AS t(campaign_id, make, model, component)
    )
    SELECT p.campaign_id, collect_set(c.complaint_id) AS pool
    FROM probes p
    JOIN {SOURCE} c
      ON c.make = p.make
     AND (c.model = p.model
          OR c.model LIKE p.model || ' %'
          OR p.model LIKE c.model || ' %')
     AND (upper(c.component) LIKE '%' || upper(p.component) || '%'
          OR upper(p.component) LIKE '%' || upper(c.component) || '%')
    GROUP BY p.campaign_id
""").collect()

POOLS = {r.campaign_id: set(r.pool) for r in pool_rows}
_sizes = sorted(len(v) for v in POOLS.values())
print(
    f"relevance pools built for {len(POOLS)}/{len(topical_rows)} probes; "
    f"sizes min={_sizes[0] if _sizes else 0} "
    f"median={_sizes[len(_sizes) // 2] if _sizes else 0} max={_sizes[-1] if _sizes else 0}"
)
# A probe with no pool has no ground truth. It is a defective PROBE, not a retrieval failure,
# and scoring it as zero would understate the system for a reason unrelated to retrieval.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Score
# MAGIC
# MAGIC Both families, both query types. A probe whose relevant set turns out to be empty is
# MAGIC **dropped and counted**, never scored as a zero — a defective probe is not a retrieval
# MAGIC failure, and silently folding one into the mean would understate the system for a reason
# MAGIC that has nothing to do with it.

# COMMAND ----------

random.seed(SEED)
results = []

for query_type in ("HYBRID", "ANN"):
    # --- family A ------------------------------------------------------------------------
    per_probe = []
    for probe in known_probes:
        hits = search(probe["query"], query_type)
        ranked = [h.get("complaint_id") for h in hits]
        per_probe.append(
            {
                "recall_at_k": recall_at_k(ranked, probe["relevant"], K),
                "precision_at_k": precision_at_k(ranked, probe["relevant"], K),
                "reciprocal_rank": reciprocal_rank(ranked, probe["relevant"]),
            }
        )
    results.append({"family": "known_item", "query_type": query_type, **summarise(per_probe, K)})

    # --- family B ------------------------------------------------------------------------
    per_probe, dropped, pool_sizes = [], 0, []
    for row in topical_rows:
        question = f"{row.component.lower()} problem on a {row.make.title()} {row.model}"
        # GROUND TRUTH FROM THE CORPUS, not from `hits`. See the section above for what the
        # first version did instead and why it could not fail.
        relevant = POOLS.get(row.campaign_id, set())
        if not relevant:
            dropped += 1
            continue
        hits = search(question, query_type)
        ranked = [h.get("complaint_id") for h in hits]
        if not ranked:
            # Retrieval returned nothing for a probe that HAS ground truth. That is a real
            # miss and scores zero — unlike the no-pool case above, which is dropped.
            per_probe.append({"recall_at_k": 0.0, "precision_at_k": 0.0, "reciprocal_rank": 0.0})
            pool_sizes.append(len(relevant))
            continue
        pool_sizes.append(len(relevant))
        per_probe.append(
            {
                "recall_at_k": recall_at_k(ranked, relevant, K),
                "precision_at_k": precision_at_k(ranked, relevant, K),
                "reciprocal_rank": reciprocal_rank(ranked, relevant),
            }
        )
    summary = summarise(per_probe, K)
    summary["dropped_probes"] = dropped
    # Published beside recall so a tiny figure is legible as arithmetic rather than failure.
    summary["relevant_pool_size"] = sorted(pool_sizes)[len(pool_sizes) // 2] if pool_sizes else 0
    results.append({"family": "topical", "query_type": query_type, **summary})

for r in results:
    print(
        f"{r['family']:<12} {r['query_type']:<7} "
        f"probes={r['probes']:<4} hit={r['probes_with_a_hit']:<4} "
        f"recall@{K}={r['recall_at_k']:.3f}  P@{K}={r['precision_at_k']:.3f}  "
        f"MRR={r['mrr']:.3f}  pool={r.get('relevant_pool_size', 1)}"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The three I-040 behavioural checks, re-run at the current scope
# MAGIC
# MAGIC These passed at 1,746,601 chunks and again at a 10K smoke scope (I-112), but never at the
# MAGIC scope that ships. Re-run here with probes the fleet corpus can answer.

# COMMAND ----------

behaviour = {}

probe = search("brake failure", "HYBRID", k=1)
behaviour["columns_to_sync"] = bool(probe) and not [c for c in COLS if c not in probe[0]]
print(f"1. columns_to_sync complete: {behaviour['columns_to_sync']}")

harm = w.vector_search_indexes.query_index(
    index_name=INDEX,
    columns=COLS,
    query_text="fire while driving",
    query_type="HYBRID",
    num_results=10,
    filters_json='{"any_harm": true}',
)
harm_rows = (harm.result.data_array or []) if harm.result else []
harm_cols = [c.name for c in harm.manifest.columns] if harm.manifest else COLS
harm_dicts = [dict(zip(harm_cols, r, strict=False)) for r in harm_rows]
behaviour["harm_filter"] = bool(harm_dicts) and all(d.get("any_harm") for d in harm_dicts)
print(f"2. any_harm filter holds on {len(harm_dicts)} rows: {behaviour['harm_filter']}")

# A component-code token beside symptom language — the case §4.3 says HYBRID exists for, and
# chosen from a system this fleet actually runs rather than the old unintended-acceleration
# paraphrase, which leaned on makes the fleet may not operate.
q = "SERVICE BRAKES, HYDRAULIC pedal went to the floor"
hybrid_ids = [h.get("chunk_id") for h in search(q, "HYBRID")]
ann_ids = [h.get("chunk_id") for h in search(q, "ANN")]
behaviour["hybrid_differs_from_ann"] = hybrid_ids != ann_ids
overlap = len(set(hybrid_ids) & set(ann_ids))
print(f"3. HYBRID != ANN: {behaviour['hybrid_differs_from_ann']} ({overlap}/{K} shared)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Persist
# MAGIC
# MAGIC To a table, not to notebook output. Every `ops_*` table in this schema is the evidence
# MAGIC behind a numbered issue, and a figure that exists only in a cell output is gone the next
# MAGIC time the cluster is recycled.

# COMMAND ----------

run_ts = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
rows = [
    {
        "run_ts": run_ts,
        "index_name": INDEX,
        "indexed_row_count": int(INDEXED_ROWS or 0),
        "family": r["family"],
        "query_type": r["query_type"],
        "k": int(r["k"]),
        "probes": int(r["probes"]),
        "probes_with_a_hit": int(r["probes_with_a_hit"]),
        "recall_at_k": float(r["recall_at_k"]),
        "precision_at_k": float(r["precision_at_k"]),
        "mrr": float(r["mrr"]),
        # Median ground-truth pool size. 1 for known-item by construction; for topical it is
        # the denominator that makes Recall@k readable, and omitting it is how a correct small
        # number gets mistaken for a broken retriever.
        "relevant_pool_size": int(r.get("relevant_pool_size", 1)),
        "seed": SEED,
        "behaviour_columns_to_sync": bool(behaviour["columns_to_sync"]),
        "behaviour_harm_filter": bool(behaviour["harm_filter"]),
        "behaviour_hybrid_differs": bool(behaviour["hybrid_differs_from_ann"]),
    }
    for r in results
]

spark.createDataFrame(rows).write.mode("append").option("mergeSchema", "true").saveAsTable(
    OPS_TABLE
)

print(f"wrote {len(rows)} rows to {OPS_TABLE}")
display(spark.table(OPS_TABLE).where(f"run_ts = '{run_ts}'"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. What to do with the result
# MAGIC
# MAGIC Record the four headline figures in `docs/EVIDENCE.md` **as measured**, next to the
# MAGIC Phase 3 row, and name the limitation in the same breath: relevance is metadata
# MAGIC agreement, not human judgement, and family A is a floor test rather than a hard one.
# MAGIC
# MAGIC If family A's hit rate is below ~0.9, something is wrong with the index rather than with
# MAGIC the retriever — the query text is literally present in the corpus. Check
# MAGIC `indexed_row_count` against `silver_complaint_chunk_indexed` before concluding anything
# MAGIC about retrieval quality.
# MAGIC
# MAGIC If HYBRID and ANN come back identical on check 3, the index subtype did not take. That is
# MAGIC a configuration failure, not a finding, and it invalidates every number above it.
