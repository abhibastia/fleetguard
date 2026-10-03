# FleetGuard — Runbook

How to run and operate this project: local development, deploying, and operating the live
Databricks resources (AI Search index, agent serving endpoint, the App). For installing
dependencies, running the test suite, and running the backend/frontend locally, `README.md`'s
*Getting set up* / *Run it locally* / *Deploying* sections are the source of truth — this file
cross-references them rather than duplicating them, so the two cannot drift apart.

**Profile is always `abhi`** (`dbc-7b106152-caf3.cloud.databricks.com`). Never omit
`--profile` — this is a shared metastore (~296 other identities' work lives in it) and the CLI
must never silently pick a default.

**Names and values that recur, fixed once:**

| name                     | value                                                                                                                                                                                                    |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| catalog.schema           | `bootcamp_students.fleetguard`                                                                                                                                                                           |
| AI Search endpoint       | `fleetguard-vs` (`STANDARD`)                                                                                                                                                                             |
| AI Search index          | `complaint_chunk_idx`                                                                                                                                                                                    |
| index source table       | `bootcamp_students.fleetguard.silver_complaint_chunk_indexed` (fleet make/model scope; **179,347 rows** = 115,499 `EXACT` + 63,848 `MODEL_VARIANT`, measured 2026-09-30, pinned per tier in the builder) |
| primary key              | `chunk_id`                                                                                                                                                                                               |
| embedding source column  | `chunk_text`                                                                                                                                                                                             |
| embedding model endpoint | `databricks-gte-large-en`                                                                                                                                                                                |
| index subtype            | `HYBRID`                                                                                                                                                                                                 |
| pipeline type            | `TRIGGERED`                                                                                                                                                                                              |
| columns_to_sync          | `chunk_id, complaint_id, make, model, component, any_harm, chunk_text` — the exact list `src/agent/14_fleetguard_agent.py` and `src/search/09_hybrid_query_test.py` both query                           |
| agent UC model           | `bootcamp_students.fleetguard.fleetguard_agent`                                                                                                                                                          |
| agent serving endpoint   | `agents_bootcamp_students-fleetguard-fleetguard_agent`                                                                                                                                                   |
| App                      | `fleetguard-console`                                                                                                                                                                                     |

**Before scaling up:** run a smoke-index first, and verify its `get-index` output shows all
seven `columns_to_sync` columns and `HYBRID` before trusting the full build to the command in
§3.2.

---

## 1. Local development

Source of truth: `README.md` → *Getting set up* and *Run it locally*. Summary, for
self-containedness:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm --prefix app/frontend install

.venv/bin/python -m ruff check src tests app/backend scripts
.venv/bin/python -m pytest
npm --prefix app/frontend run typecheck && npm --prefix app/frontend run test

scripts/run_local_static_dev.sh    # backend at :8811 against live Lakebase
```

`run_local_static_dev.sh` mints a token that lives one hour — if Lakebase-backed routes start
500ing mid-session, restart the script rather than debugging application code.

## 2. Deploying

Source of truth: `README.md` → *Deploying*. Summary:

```bash
databricks bundle summary -t prod --profile abhi   # nothing should read "to be created"
./scripts/deploy.sh abhi prod                      # guards a dirty tree, deploys, checks provenance
```

Shipping the App specifically needs its own two commands — `bundle deploy` alone does **not**
ship App code:

```bash
databricks bundle deploy -t prod --profile abhi
databricks bundle run fleetguard_console -t prod --profile abhi   # ← ships the code
```

## 3. Operating the live Databricks resources

### 3.1 Check current state

```bash
git rev-parse HEAD
databricks bundle summary -t prod --profile abhi | grep -i "deployed\|version"
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi | grep -A3 entity_version
databricks apps get fleetguard-console --profile abhi | grep -A2 active_deployment
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi | grep -i "indexed_row_count\|ready"
```

These five values together (git SHA, bundle deployment, agent model version, App deployment id,
index row count) are the release provenance — nothing else in this project puts them in one
place. Record them together after any deploy, not just once.

### 3.2 Create or restore the AI Search index

**Use `scripts/provision_search.sh` rather than the raw commands.**

```bash
./scripts/provision_search.sh --check  --profile abhi   # free: what exists right now
./scripts/provision_search.sh --create --profile abhi   # BILLS. prompts before the first charge
```

It runs the same configuration as the manual commands below and adds three things a command
block cannot:

- **idempotent** — `get-endpoint`/`get-index` first, so a re-run after a dropped connection
  resumes instead of erroring on line one.
- **reads the expected row count** from the source table rather than carrying a literal. Expect
  **179,347** (115,499 `EXACT` + 63,848 `MODEL_VARIANT`, measured 2026-09-30) — a result near
  115K means the `EXACT`+`MODEL_VARIANT` widening did not take, not success. See
  `docs/EVIDENCE.md` §8 for how this figure was validated.
- **polls with drop detection** and treats a *decrease* in `indexed_row_count` as fatal — that is
  I-105's only visible symptom, and the failure that costs a day if missed.

The manual path still works and is what the script wraps, if it ever needs to be run by hand.
Scope a 10K-row smoke subset first on a fresh endpoint (free, Delta-only):

```bash
databricks experimental aitools tools query "
CREATE OR REPLACE TABLE bootcamp_students.fleetguard.silver_complaint_chunk_smoke10k
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS SELECT * FROM bootcamp_students.fleetguard.silver_complaint_chunk_indexed LIMIT 10000
" --profile abhi

databricks vector-search-endpoints create-endpoint fleetguard-vs STANDARD --profile abhi
databricks vector-search-endpoints get-endpoint fleetguard-vs --profile abhi   # wait for ONLINE
```

**`--json` and positional args are mutually exclusive** — passing both errors with `when --json
flag is specified, no positional arguments are allowed`. `name`, `endpoint_name`, `primary_key`,
`index_type` all go inside the JSON body instead:

```bash
databricks vector-search-indexes create-index \
  --index-subtype HYBRID \
  --json '{
    "name": "bootcamp_students.fleetguard.complaint_chunk_smoke_idx",
    "endpoint_name": "fleetguard-vs",
    "primary_key": "chunk_id",
    "index_type": "DELTA_SYNC",
    "delta_sync_index_spec": {
      "source_table": "bootcamp_students.fleetguard.silver_complaint_chunk_smoke10k",
      "pipeline_type": "TRIGGERED",
      "embedding_source_columns": [
        {"name": "chunk_text", "embedding_model_endpoint_name": "databricks-gte-large-en"}
      ],
      "columns_to_sync": ["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"]
    }
  }' \
  --profile abhi

databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_smoke_idx --profile abhi   # poll until ready, ~2 min at 10K rows
```

**Verify before trusting it** — re-run the three checks I-040 already proved once, so a config
regression surfaces here and not hours into the real build:
1. Query returns `make`/`model`/`component`/`any_harm` alongside `chunk_text` (not just the key
   and embedded text) — proves `columns_to_sync` actually took.
2. `filters_json={"any_harm": true}` returns only harm rows.
3. A HYBRID query for a component-code term surfaces a literal keyword match a pure-ANN query
   would rank lower. Reuse `src/search/09_hybrid_query_test.py`, pointed at the smoke index.

Delete the smoke index and its scratch table (endpoint stays — the real build reuses it):

```bash
databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_smoke_idx --profile abhi
databricks experimental aitools tools query "DROP TABLE bootcamp_students.fleetguard.silver_complaint_chunk_smoke10k" --profile abhi
```

Then build the real index — same shape, full scope, no `smoke10k` suffix:

```bash
databricks vector-search-indexes create-index \
  --index-subtype HYBRID \
  --json '{
    "name": "bootcamp_students.fleetguard.complaint_chunk_idx",
    "endpoint_name": "fleetguard-vs",
    "primary_key": "chunk_id",
    "index_type": "DELTA_SYNC",
    "delta_sync_index_spec": {
      "source_table": "bootcamp_students.fleetguard.silver_complaint_chunk_indexed",
      "pipeline_type": "TRIGGERED",
      "embedding_source_columns": [
        {"name": "chunk_text", "embedding_model_endpoint_name": "databricks-gte-large-en"}
      ],
      "columns_to_sync": ["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"]
    }
  }' \
  --profile abhi
```

**Poll roughly every 5 min** at ~4,336 rows/min from the source-table count (179,347 rows ≈
41 min). Watch `status.indexed_row_count`:

```bash
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi
```

**A drop, not just a plateau, is the failure signal (I-105)** — if a poll reads *lower* than the
previous one, the platform silently restarted the sync from zero after an internal failure. On a
drop, find the pipeline id from the `get-index` response and check events:

```bash
databricks pipelines list-pipeline-events <PIPELINE_ID> --profile abhi
```

**Index creation on a fresh endpoint can stall** at `ready: false` with zero visible sync
activity for ~20 minutes before it starts — this has recurred more than once and self-cleared
each time. Watch the `message` field, not `indexed_row_count` (it reads `None` then `0`
throughout the stall, indistinguishable from a dead index). **If this happens: wait, do not
delete-and-recreate** — recreation was tried once and did not clear it; inaction did.

Done when `status.ready: true` and `indexed_row_count` equals the count the source-table rebuild
(§3.6) printed — not a number hard-coded here, since the fleet scope and corpus both change over
time.

**Cost while this exists:** ~$6.72/day, and billing continues for 24h after the last index is
deleted.

### 3.3 Restore the agent serving endpoint

> ### The index must exist first — there is no way around this (I-129).
>
> Both `update-config` **and** the Serving UI's **Start** button are refused while
> `complaint_chunk_idx` is absent:
>
> ```
> User cannot serve registered model '…fleetguard_agent' version '8'.
> Dependencies do not exist: table 'bootcamp_students.fleetguard.complaint_chunk_idx'
> ```
>
> Model Serving validates the model's **logged resource dependencies at start**, not lazily at
> tool-call time. The agent would run perfectly — it answers deterministic questions with the
> index gone (`docs/TRANSCRIPTS.md` §4) — but it cannot be *started*. So if the index was torn
> down, the real cost of restoring the agent is §3.2 first (~75 min) and then this step — don't
> quote the agent-only timing to anyone waiting.

There is no `start` subcommand for the agent endpoint — only `update-config`, rebuilt from the
*live* served-entity config so nothing (especially `environment_vars`, which silently misfiles
MLflow tracing if dropped) is lost:

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi
```

Read that JSON's `config.served_entities` block, then re-apply it with
`scale_to_zero_enabled: true` added (or confirmed) on each entity:

```bash
databricks serving-endpoints update-config agents_bootcamp_students-fleetguard-fleetguard_agent \
  --json '<served_entities block copied from get, with "scale_to_zero_enabled": true on each entity>' \
  --profile abhi
```

**This step is redundant with §3.4 if a rebuild is also needed** — §3.4 deploys a *new* model
version to this same endpoint regardless of its current state. If a rebuild is already planned,
skip straight to §3.4; this step exists on its own for unsticking a *stopped* endpoint without a
version bump.

**The asymmetry worth knowing: Stop is UI-only, restore is CLI-only.** `POST .../stop` is absent
from the CLI, the Python SDK and the public REST reference alike (`ENDPOINT_NOT_FOUND`), yet
present as a button in the Serving UI — that is how an endpoint reaches `DEPLOYMENT_STOPPED` in
the first place. Restore is `update-config` only, so the two halves of the lifecycle live in two
different places.

**Stopped is not scale-to-zero, and the difference matters for a live demo:**

```
deployment         : DEPLOYMENT_STOPPED
deployment_message : 'Stopped'
scale_to_zero_enabled : true        <-- still true. I-092.

POST .../invocations -> HTTP 400
{"error_code":"BAD_REQUEST","message":"The given endpoint is stopped,
 please retry after starting the endpoint."}
```

A *scaled-to-zero* endpoint wakes in ~47 s; a *stopped* one refuses outright.
`scale_to_zero_enabled` reads `true` in **both** states, so it distinguishes nothing — check
`deployment_state_message` instead (never `scale_to_zero_enabled` alone — I-092).

### 3.4 Rebuild and redeploy the agent

Run this after any change to `src/agent/14_fleetguard_agent.py` — waking an old served version
does not pick up source changes.

```bash
databricks bundle run agent_build -t prod --profile abhi
```

This logs, validates and registers a new UC model version (`deploy` widget defaults to `false` —
nothing bills yet). Then:

```bash
databricks bundle run deploy_agent -t prod --profile abhi
```

`src/agent/15_deploy_agent.py` resolves the **latest** registered version automatically (the
`model_version` widget default is blank = latest, not a pinned value — I-098) and runs its own
live smoke test at the end (hard `assert`s — a `SUCCESS` job result means the live answer
actually passed, not just that the notebook ran).

**Check for an old version still being served** — this has recurred repeatedly across versions
(I-050/I-092): the previous version can stay `DEPLOYMENT_READY` at 0% traffic and the endpoint
looks healthy either way, so a passing health check doesn't mean the new version is live.

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi \
  | grep -A3 '"entity_version"\|"deployment"'
```

If an old version is still `DEPLOYMENT_READY`, remove it and re-assert `scale_to_zero_enabled`
in one call (replace the version number and the two `_<version>` names with the actual values):

```bash
databricks serving-endpoints update-config agents_bootcamp_students-fleetguard-fleetguard_agent \
  --json '{
    "served_entities": [{
      "entity_name": "bootcamp_students.fleetguard.fleetguard_agent",
      "entity_version": "7",
      "name": "bootcamp_students-fleetguard-fleetguard_agent_7",
      "workload_size": "Small",
      "workload_type": "CPU",
      "scale_to_zero_enabled": true,
      "environment_vars": {
        "ENABLE_LANGCHAIN_STREAMING": "true",
        "ENABLE_MLFLOW_TRACING": "true",
        "MLFLOW_EXPERIMENT_ID": "127427013652374",
        "RETURN_REQUEST_ID_IN_RESPONSE": "true"
      }
    }],
    "traffic_config": {
      "routes": [{"served_entity_name": "bootcamp_students-fleetguard-fleetguard_agent_7", "traffic_percentage": 100}]
    }
  }' \
  --profile abhi
```

**Verifying a live answer: use a raw REST call, not the CLI or the SDK's `query()` helper.**
The CLI's `serving-endpoints query` truncates this endpoint's response to `{"id": ..., "object":
"response"}` with no `output` field, and the Python SDK's `w.serving_endpoints.query()` sends the
wrong body shape for this endpoint's `agent/v1/responses` task (`inputs` instead of the
top-level `input` key it actually expects — `400 Bad Request`). What works:

```bash
HOST=$(databricks auth env --profile abhi | grep -o '"DATABRICKS_HOST": *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
TOKEN=$(databricks auth token --profile abhi | grep -o '"access_token": *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
curl -s -X POST "$HOST/serving-endpoints/agents_bootcamp_students-fleetguard-fleetguard_agent/invocations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "Which fleet vehicles does recall 17V629000 affect?"}]}'
```

Expected answer: 25 vehicles, 22 depots, all `EXACT`.

### 3.5 Evaluate the agent

Run this after every rebuild/redeploy — a working live answer is not the same as a passing
evaluation, and skipping this step means `16_evaluate_agent.py`'s hard gates never actually run
against the live version.

```bash
databricks bundle run evaluate_agent -t prod --profile abhi
```

The job fails on a safety regression: claiming a campaign was launched, inventing a recall, or
(I-118) acting on an instruction embedded in a retrieved complaint narrative. It also **stamps
the model version** with its result (`eval_run_id`, `eval_hard_gates`, a `score_*` tag per
scorer) — skip this and the deployed version carries no evidence it was ever evaluated, the same
provenance gap `/api/readyz` closes for the live system, one layer down.

Runs against the **latest registered version** on its own — do not pass `model_version`; a
pinned widget is what silently scored a stale model once before (I-094/I-098).

**Reading the result:**
- The job **raises on a hard-gate failure** — that's the intended behaviour, not a broken run.
  Use `17_inspect_eval` with the printed run id to see which case and why.
- `resists_injected_instructions` and `cites_complaint_ids` are assertion-based scorers
  (I-058) — if injection resistance fails, read the answer before assuming the agent complied: a
  refusal that *names* the action it is declining is the correct answer, not a failure.
- Expect ~15 cases and roughly 10-20 minutes; it calls the serving endpoint once per case.

### 3.6 Rebuild the AI Search source table (when the fleet scope or join changes)

Run this — and let it finish — **before** §3.2, whenever the fleet roster or match-tier logic in
`src/search/27_build_chunk_index_source.py` changes. Building the index off a stale source table
silently drops vehicles from retrieval (I-115 — the fleet's most numerous model, the F-250, was
missing from retrieval for a time because the source table used an exact-spelling-only join).

This is Delta-only — free, no endpoint involved, a few minutes:

```bash
databricks bundle run build_chunk_index_source -t prod --profile abhi
```

**Pinned per tier (I-126):** `EXACT` **115,499** + `MODEL_VARIANT` **63,848** = **179,347**. The
job asserts each tier exactly, not a bounded range — a regression that moved rows *between*
tiers while preserving the sum would otherwise pass undetected. A `MODEL_VARIANT` count of zero
means the exact-only scope is back in force and the fleet's 2,116 F-250s are absent from
retrieval.

Then refresh what the console shows:

```bash
.venv/bin/python scripts/export_corpus.py --profile abhi   # updates Home's scale strip
```

### 3.7 Ship the App

If the App is currently stopped, start it first:

```bash
databricks apps start fleetguard-console --profile abhi
```

Then ship the code — `apps start` alone re-deploys whatever source path was last active, which
is stale if the console changed:

```bash
./scripts/deploy.sh abhi prod
databricks bundle run fleetguard_console -t prod --profile abhi
```

The **second** command is what actually ships App code (`bundle deploy` alone does not —
I-097). `deploy.sh` refuses a dirty tree; commit first if it stops you.

**Abbreviated verification:** one agent question through the console, one write (e.g.
`open_defect_signal` via the Assistant, or an approval), then confirm it reached
`gold_agent_action` / `gold_defect_signal_current` via the CDF→gold job (already `UNPAUSED`,
fires on its own — no command needed beyond checking the resulting row):

```bash
databricks experimental aitools tools query "SELECT * FROM bootcamp_students.fleetguard.gold_agent_action ORDER BY _last_updated DESC LIMIT 3" --profile abhi
```

### 3.8 Run the RAG retrieval evaluation

Needs the index and nothing else.

```bash
databricks bundle run rag_eval -t prod --profile abhi
```

Writes `ops_rag_eval` and re-runs the three I-040 behavioural checks at the shipped scope.
**Record the figures in `docs/EVIDENCE.md` as measured, including if they are poor** (I-049 /
I-111's rule).

Read family A (known-item) first: it is a floor test, the query text is literally in the corpus,
and a hit rate below ~0.9 means the *index* is wrong rather than the retriever. Check
`indexed_row_count` against `silver_complaint_chunk_indexed` before concluding anything about
retrieval quality. If HYBRID and ANN return identical results on check 3, the index subtype did
not take and every number above it is void.

### 3.9 Take fresh screenshots

Run this any time the built UI has changed — do it after §3.7, or the images show older UI than
what's deployed.

```bash
./scripts/run_local_static_dev.sh 8811 &     # separate shell/background; live Lakebase
.venv/bin/python scripts/capture_screenshots.py
```

`run_local_static_dev.sh` mints a token that expires after an hour — if Lakebase routes start
500ing mid-capture, restart it rather than debugging.

### 3.10 Record release provenance

Same five-command block as §3.1 — run it again after any deploy, since a changed git SHA means
the earlier recording is stale:

```bash
git rev-parse HEAD
databricks bundle summary -t prod --profile abhi | grep -i "deployed\|version"
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi | grep -A3 entity_version
databricks apps get fleetguard-console --profile abhi | grep -A2 active_deployment
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi | grep -i "indexed_row_count\|ready"
```

## 4. Cost control / teardown

```bash
databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi
databricks vector-search-endpoints delete-endpoint fleetguard-vs --profile abhi
databricks apps stop fleetguard-console --profile abhi
```

**Verify billing actually stopped, the next day.** `system.billing` is not queryable from this
account (no `USE SCHEMA` grant on a shared metastore) — this can only be checked indirectly:
confirm `vector-search-endpoints list-endpoints` no longer shows `fleetguard-vs` the day after
deletion, and treat "24h after last index deletion" as the vendor's stated rule, not something
independently confirmable from here.

**Leave the agent endpoint alone.** Do not stop or delete it — scale-to-zero is free idle and
removes a restore step later. No command needed unless it drifted back to `Stopped`; check with
the same `get` command as §3.3.

**Touch nothing else.**
