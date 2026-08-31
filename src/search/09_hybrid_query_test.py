# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 3 done-when: hybrid retrieval test
# MAGIC
# MAGIC Phase 3's definition of done is specific: *"a hybrid query returns component-code
# MAGIC exact matches AND semantically related narratives in the same result set."* This
# MAGIC tests exactly that, and does it by **comparing ANN against HYBRID on the same
# MAGIC queries** — otherwise "hybrid works" is unfalsifiable.
# MAGIC
# MAGIC Run against a partially-built index (~42% synced). That is fine for a behavioural
# MAGIC test: retrieval quality per query is what is being checked, not recall over the full
# MAGIC corpus. Re-run after the sync completes to confirm nothing changes qualitatively.
# MAGIC
# MAGIC Note the CLI cannot read this endpoint — `databricks vector-search-indexes
# MAGIC query-index` gets HTTP 200 and then fails to unmarshal the body
# MAGIC (`invalid character 'r' after top-level value`), which is a Go SDK bug. The Python
# MAGIC path works.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

INDEX = "bootcamp_students.fleetguard.complaint_chunk_idx"
COLS = ["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"]

w = WorkspaceClient()

st = w.vector_search_indexes.get_index(index_name=INDEX).status
print(f"indexed rows: {st.indexed_row_count:,}   ready: {st.ready}")


def search(text, query_type, k=5, filters=None):
    r = w.vector_search_indexes.query_index(
        index_name=INDEX,
        columns=COLS,
        query_text=text,
        query_type=query_type,
        num_results=k,
        filters_json=filters,
    )
    rows = (r.result.data_array or []) if r.result else []
    cols = [c.name for c in r.manifest.columns] if r.manifest else COLS
    return [dict(zip(cols, row, strict=False)) for row in rows]


# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 1 — does `columns_to_sync` actually work?
# MAGIC
# MAGIC Open question from index creation: `columns_to_sync` did not appear in the returned
# MAGIC spec. If it was ignored, results carry only the key and embedded text, and
# MAGIC harm-filtered retrieval (a §4.3 requirement) is impossible.

# COMMAND ----------

probe = search("brake failure", "HYBRID", k=1)
if not probe:
    print("no results yet — index may not have enough rows synced")
else:
    print("columns returned:")
    for k_, v in probe[0].items():
        shown = str(v)[:70].replace("\n", " ")
        print(f"  {k_:<14} = {shown}")
    missing = [c for c in COLS if c not in probe[0]]
    print(f"\nmissing requested columns: {missing or 'NONE — columns_to_sync worked'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 2 — the actual done-when
# MAGIC
# MAGIC An exact component-code token that pure semantic search handles poorly, alongside
# MAGIC natural-language symptom text. Hybrid should surface both; ANN should favour the
# MAGIC paraphrase and under-weight the literal token.

# COMMAND ----------

QUERIES = [
    # exact token + symptom language together — the case §4.3 says hybrid exists for
    ("SERVICE BRAKES, HYDRAULIC pedal went to floor", "component code + symptom"),
    # pure paraphrase, no corpus vocabulary — semantic must carry it
    ("car suddenly sped up on its own while I was driving", "paraphrase, no exact terms"),
    # a literal part-style token
    ("TAKATA airbag inflator rupture", "proper noun / part token"),
]

results = []
for q, why in QUERIES:
    print(f"\n{'=' * 78}\nQUERY: {q}\n  ({why})")
    for qt in ("ANN", "HYBRID"):
        hits = search(q, qt, k=5)
        print(f"\n  --- {qt} ---")
        for h in hits:
            comp = str(h.get("component"))[:34]
            txt = str(h.get("chunk_text"))[:88].replace("\n", " ")
            print(f"    [{h.get('make')}/{h.get('model')}] {comp}")
            print(f"       {txt}...")
            results.append(
                {
                    "query": q,
                    "why": why,
                    "query_type": qt,
                    "make": str(h.get("make")),
                    "model": str(h.get("model")),
                    "component": str(h.get("component")),
                    "any_harm": str(h.get("any_harm")),
                    "snippet": txt,
                }
            )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test 3 — harm-filtered retrieval
# MAGIC
# MAGIC §4.3: *"so the agent can restrict a semantic search to complaints that involved a
# MAGIC fire or an injury."* Filtering is not decorative — it is how the agent narrows to
# MAGIC complaints with real consequences.

# COMMAND ----------

try:
    unfiltered = search("engine compartment fire while parked", "HYBRID", k=10)
    filtered = search(
        "engine compartment fire while parked", "HYBRID", k=10, filters='{"any_harm": true}'
    )
    u_harm = sum(1 for h in unfiltered if str(h.get("any_harm")).lower() == "true")
    f_harm = sum(1 for h in filtered if str(h.get("any_harm")).lower() == "true")
    print(f"unfiltered: {u_harm}/{len(unfiltered)} results involve harm")
    print(f"filtered  : {f_harm}/{len(filtered)} results involve harm")
    print(
        "filter works"
        if filtered and f_harm == len(filtered)
        else "filter did NOT constrain results"
    )
    for h in filtered[:3]:
        print(
            f"  any_harm={h.get('any_harm')} [{h.get('make')}] {str(h.get('chunk_text'))[:80]}..."
        )
except Exception as e:
    print(f"filtered query failed: {type(e).__name__}: {str(e)[:200]}")

# COMMAND ----------

# Near-duplicate audit. Silver deliberately keeps one row per CMPLID, so a complaint
# filed against several components yields several near-identical chunks (I-023). That is
# correct for clustering but shows up as near-duplicate retrieval — the agent's search
# tool has to dedupe by complaint_id at read time.
dup = search("car suddenly sped up on its own while I was driving", "ANN", k=10)
ids = [h.get("complaint_id") for h in dup]
print(f"\nANN top-10 distinct complaint_id: {len(set(ids))}/{len(ids)}")
results.append(
    {
        "query": "DUPLICATE_CHECK",
        "why": "near-duplicate audit",
        "query_type": "ANN",
        "make": "-",
        "model": "-",
        "component": f"distinct_complaint_ids={len(set(ids))}/{len(ids)}",
        "any_harm": "-",
        "snippet": "multi-component complaints yield sibling chunks",
    }
)

# Harm-filtered retrieval (§4.3) — persist the verdict rather than only printing it.
try:
    f = search("engine compartment fire while parked", "HYBRID", k=10, filters='{"any_harm": true}')
    n_true = sum(1 for h in f if str(h.get("any_harm")).lower() == "true")
    verdict = f"filtered={len(f)} any_harm_true={n_true} " + (
        "PASS" if f and n_true == len(f) else "FAIL/UNCONSTRAINED"
    )
except Exception as e:
    verdict = f"ERROR {type(e).__name__}: {str(e)[:90]}"
print(f"harm filter: {verdict}")
results.append(
    {
        "query": "HARM_FILTER",
        "why": "§4.3 harm-filtered retrieval",
        "query_type": "HYBRID",
        "make": "-",
        "model": "-",
        "component": verdict,
        "any_harm": "-",
        "snippet": "-",
    }
)

if results:
    (
        spark.createDataFrame(results)
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable("bootcamp_students.fleetguard.ops_hybrid_query_test")
    )
    print(f"persisted {len(results)} result rows for inspection")
