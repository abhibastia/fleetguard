# FleetGuard — Run 1 / Run 2 runbook

Phase 0 step 0.4 of `docs/STATUS.md`'s action plan: **the exact commands**, written down once
while nothing is billing and nobody is rushed, so the second execution (Run 2, under deadline
pressure before 4 October) is "follow this" rather than "reconstruct it from memory and hope."

**Read `docs/STATUS.md`'s action plan first** — this file is its command layer, not a
replacement for the reasoning in it. Every step number below matches that plan exactly.

**Profile is always `abhi`** (`dbc-7b106152-caf3.cloud.databricks.com`). Never omit
`--profile` — this is a shared bootcamp metastore and the CLI must never silently pick a
default.

**Names and values that recur, fixed once:**

| name | value |
|---|---|
| catalog.schema | `bootcamp_students.fleetguard` |
| AI Search endpoint | `fleetguard-vs` (`STANDARD`) |
| AI Search index | `complaint_chunk_idx` |
| index source table | `bootcamp_students.fleetguard.silver_complaint_chunk_indexed` (fleet make/model scope — I-111 + I-115; **179,347 rows** = 115,499 `EXACT` + 63,848 `MODEL_VARIANT`, measured Run 2 2026-09-30 and now pinned per tier in the builder — I-126) |
| primary key | `chunk_id` |
| embedding source column | `chunk_text` |
| embedding model endpoint | `databricks-gte-large-en` |
| index subtype | `HYBRID` |
| pipeline type | `TRIGGERED` |
| columns_to_sync | `chunk_id, complaint_id, make, model, component, any_harm, chunk_text` — the exact list `src/agent/14_fleetguard_agent.py` and `src/search/09_hybrid_query_test.py` both query; verified I-040 |
| agent UC model | `bootcamp_students.fleetguard.fleetguard_agent` |
| agent serving endpoint | `agents_bootcamp_students-fleetguard-fleetguard_agent` |
| App | `fleetguard-console` |

**One honest gap:** the *original* `create-index` call for `complaint_chunk_idx` was never
captured verbatim — it predates this runbook. The command below is reconstructed from every
independently-confirmed fact about that index (I-040's returned columns, I-105's
`pipeline_type: TRIGGERED`, ARCHITECTURE.md §4.4's `HYBRID`/`databricks-gte-large-en`), not
copied from a log. Treat step 1.1's smoke index as the check that this reconstruction is
right, not a formality — if the smoke index's `get-index` output doesn't show all seven
`columns_to_sync` columns and `HYBRID`, stop and fix the command before running it at scale.

---

## Phase 1 — Run 1

### 1.1 — 10K smoke index, then delete it

> **STALLED on the first attempt, 2026-09-23, then SELF-CLEARED (I-112) — read before running
> this again.** Two independent fresh endpoint+index creations both stuck at `ready: false` /
> "pending endpoint provisioning" for ~25 min and ~5 min observed, underlying pipeline `IDLE`,
> zero sync activity, `sync-index` refused ("not ready"). **The second one resolved on its
> own** — found `ready: true, indexed_row_count: 10000` on a later, unrelated check, with no
> further action taken in between. The real 115,499-row build immediately after, on the same
> (now-warm) endpoint, hit **no stall at all** — straight to `RUNNING`, steady climb, `ready`
> in ~39 min. Best-supported guess: shared-workspace contention specific to the *first* index
> on a *fresh* endpoint (~296 other students on this metastore). **If this recurs: wait, do
> not delete-and-recreate** — that was tried in between and did not clear it; inaction did.
> Full writeup in `docs/ISSUES.md` I-112.
>
> **RECURRED a third time in Run 2 (2026-09-30) and again self-cleared, at ~21 min.** Watch the
> `message` field, **not `indexed_row_count`** — the count reads `None` then `0` throughout the
> stall, indistinguishable from a dead index, and `list-pipeline-events` stays at exactly one
> event (*"created pipeline"*) the whole time. The only signal is the message moving
> *"pending endpoint provisioning"* → *"creation is pending"* → *"syncing initial data"*.
> Treat `None` → `0` as the first sign of life. Budget ~20 min of nothing before the ~42 min
> build even starts.

Scope a 10K-row subset from the already-fleet-scoped table (free — Delta only):

```bash
databricks experimental aitools tools query "
CREATE OR REPLACE TABLE bootcamp_students.fleetguard.silver_complaint_chunk_smoke10k
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS SELECT * FROM bootcamp_students.fleetguard.silver_complaint_chunk_indexed LIMIT 10000
" --profile abhi
```

Create the endpoint (idempotent — if it already exists from a prior attempt this errors
harmlessly; check with `get-endpoint` first if unsure):

```bash
databricks vector-search-endpoints create-endpoint fleetguard-vs STANDARD --profile abhi
```

Wait for `ONLINE` (the CLI's own `--timeout` default is 20m; this is typically under 2 min):

```bash
databricks vector-search-endpoints get-endpoint fleetguard-vs --profile abhi
```

Create the smoke index:

**`--json` and positional args are mutually exclusive** — passing both errors with
`when --json flag is specified, no positional arguments are allowed` (found running this
live 2026-09-23). `name`, `endpoint_name`, `primary_key`, `index_type` all go inside the JSON
body instead:

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
```

Poll until `ready: true` (should be ~2 min at 10K rows):

```bash
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_smoke_idx --profile abhi
```

**Verify before trusting it** — re-run the three checks I-040 already proved once, so a
config regression surfaces here and not 6.7 hours into the real build:
1. Query returns `make`/`model`/`component`/`any_harm` alongside `chunk_text` (not just the
   key and embedded text) — proves `columns_to_sync` actually took.
2. `filters_json={"any_harm": true}` returns only harm rows.
3. A HYBRID query for a component-code term surfaces a literal keyword match a pure-ANN
   query would rank lower. Reuse `src/search/09_hybrid_query_test.py`, pointed at the smoke
   index (edit `INDEX` at the top of the file, or copy the three query snippets into a
   throwaway cell).

Delete the smoke index and its scratch table (endpoint stays — the real build reuses it):

```bash
databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_smoke_idx --profile abhi
databricks experimental aitools tools query "DROP TABLE bootcamp_students.fleetguard.silver_complaint_chunk_smoke10k" --profile abhi
```

### 1.2 — Build the real index

Same command shape as the smoke index, full scope, no `smoke10k` suffix:

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

**Poll every ~5 min at this scope** (115,499 rows / 4,336 rows-min ≈ 27 min, so unlike the
1.75M-row run there is no reason to space polls 45 min apart).

> **Run 2 arrives here from step 3.1 — that 115,499 no longer applies.** I-115 widened the
> source table to **179,347** rows (measured 2026-09-30, I-126), so the estimate is
> **179,347 / 4,336 ≈ 41 min**, not 27. The command block below is unchanged; only the expected
> duration is.


```bash
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi
```

Watch `status.indexed_row_count`. **A drop, not just a plateau, is the failure signal**
(I-105) — if a poll reads *lower* than the previous one, the platform silently restarted the
sync from zero after an internal failure. On a drop:

```bash
# find the pipeline id from the get-index response's delta_sync_index_spec / status fields, then:
databricks pipelines list-pipeline-events <PIPELINE_ID> --profile abhi
```

Done when `status.ready: true` and `indexed_row_count` equals **the count step 3.0 printed** —
not a number written here. *This line read `== 115499` until 2026-09-29, eighteen lines after
the callout above saying that figure no longer applies, and it is the line an operator actually
executes at the end of the poll. I-115 widened the source to a strict superset, so following it
literally means reading a correct count as a failed sync.*

### 1.3 — Restore the agent endpoint

> ### ⚠️ THE INDEX MUST EXIST FIRST. THERE IS NO WAY AROUND THIS (I-129).
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
> index gone (`docs/TRANSCRIPTS.md` §4) — but it cannot be *started*.
>
> **So the "~3 min" below is conditional**, and was measured on 2026-09-23 when the index
> happened to exist. After a Phase 2 teardown the real cost of getting the agent back is
> **step 3.1 first (~75 min) and then this step** — do not quote 3 minutes to anyone waiting.

Current live state (checked 2026-09-23): `DEPLOYMENT_STOPPED`. There is no `start`
subcommand — only `update-config`, rebuilt from the *live* served-entity config so nothing
(especially `environment_vars`, which silently misfiles MLflow tracing if dropped) is lost:

> **The asymmetry, recorded 2026-10-01.** **Stop is UI-only** — absent from the CLI, the Python
> SDK and the public REST reference alike (`POST .../stop` → `ENDPOINT_NOT_FOUND`), yet present
> as a button in the Serving UI. That is how this endpoint reached `DEPLOYMENT_STOPPED` in the
> first place. **Restore is CLI-only in practice** (`update-config`), so the two halves of this
> endpoint's lifecycle live in two different places — which is worth knowing before hunting for
> a `start` that does not exist.
>
> **Stopped is not scale-to-zero, and the difference bites during a demo.** Both verified live
> 2026-10-01 after stopping v8 from the UI:
> ```
> deployment         : DEPLOYMENT_STOPPED
> deployment_message : 'Stopped'
> scale_to_zero_enabled : true        <-- still true. I-092, live.
>
> POST .../invocations -> HTTP 400
> {"error_code":"BAD_REQUEST","message":"The given endpoint is stopped,
>  please retry after starting the endpoint."}
> ```
> A *scaled-to-zero* endpoint wakes in ~47 s; a *stopped* one refuses outright. And
> `scale_to_zero_enabled` reads `true` in **both** states, so it distinguishes nothing — read
> `deployment_state_message`.

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

**This step may be redundant with 1.4 below** — 1.4 deploys a *new* model version (the one
carrying the I-109/I-110 fixes) to this same endpoint regardless of its current state. If
going straight to 1.4, it is reasonable to skip 1.3 entirely; it exists as its own step
because past sessions needed to unstick a *stopped* endpoint without a version bump. Check
`deployment_state_message`, never `scale_to_zero_enabled` (I-092 — it reads `True` in both
"scaled to zero" and "stopped", and only the former wakes on a request).

### 1.4 — Re-register and deploy the agent

```bash
databricks bundle run agent_build -t prod --profile abhi
```

This logs, validates and registers a new UC model version (`deploy` widget defaults to
`false` — nothing bills yet). Then:

```bash
databricks bundle run deploy_agent -t prod --profile abhi
```

`src/agent/15_deploy_agent.py` resolves the **latest** registered version automatically (the
`model_version` widget default is blank = latest, not a pinned "1" — I-098) and runs its own
live smoke test at the end (hard `assert`s — a `SUCCESS` job result actually means the live
answer passed, not just that the notebook ran).

**Check for the old version still being served** — this has now recurred a fourth time (v1,
v4, v5, and confirmed again 2026-09-23 going v6→v7):

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi \
  | grep -A3 '"entity_version"\|"deployment"'
```

If the old version is still `DEPLOYMENT_READY`, remove it and re-assert `scale_to_zero_enabled`
in one call — this is the exact command that worked 2026-09-23 (replace `"7"`/the two `_7`
names with whatever the new version actually is):

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
Both were tried 2026-09-23 and failed for different reasons — the CLI's `serving-endpoints
query` truncates this endpoint's response to `{"id": ..., "object": "response"}` with no
`output` field (same class of Go-SDK unmarshalling gap as I-040's vector-search finding),
and the Python SDK's `w.serving_endpoints.query()` sends the wrong body shape for this
endpoint's `agent/v1/responses` task (`inputs` instead of the top-level `input` key it
actually expects, `400 Bad Request`). What works:

```bash
HOST=$(databricks auth env --profile abhi | grep -o '"DATABRICKS_HOST": *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
TOKEN=$(databricks auth token --profile abhi | grep -o '"access_token": *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
curl -s -X POST "$HOST/serving-endpoints/agents_bootcamp_students-fleetguard-fleetguard_agent/invocations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "Which fleet vehicles does recall 17V629000 affect?"}]}'
```

Expected answer (confirmed 2026-09-23 on v7): 25 vehicles, 22 depots, all `EXACT`.

### 1.5 — Agent smoke test

Already run as part of `agent_build` (the notebook's own cells) and `deploy_agent` (the
live-endpoint predict call at the end of `15_deploy_agent.py`). No separate action if 1.4 ran
clean — this step exists in the plan as a checkpoint, not a distinct command.

### 1.6 — Ship the App

```bash
./scripts/deploy.sh abhi prod
databricks bundle run fleetguard_console -t prod --profile abhi
```

The **second** command is what actually ships App code (`bundle deploy` alone does not —
I-097). `deploy.sh` refuses a dirty tree; commit first if it stops you.

### 1.7 — Full DEMO.md dry run

No new commands — follow `docs/DEMO.md` beat by beat, live, against everything Run 1 just
brought up. This is the step that has never been done in composition.

### 1.8 — Screenshots

```bash
./scripts/run_local_static_dev.sh 8811 &     # separate shell/background; live Lakebase
.venv/bin/python scripts/capture_screenshots.py
```

Run *after* 1.6, or the images show older UI than the code being submitted.

### 1.9 — Record release provenance

```bash
git rev-parse HEAD
databricks bundle summary -t prod --profile abhi | grep -i "deployed\|version"
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi | grep -A3 entity_version
databricks apps get fleetguard-console --profile abhi | grep -A2 active_deployment
databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi | grep -i "indexed_row_count\|ready"
```

Write the five values down together (git SHA, bundle deployment, agent model version, App
deployment id, index row count) — nothing else in this project puts them in one place, which
is the exact gap the 2026-09-20 review named as the biggest practical risk (I-110).

---

## Phase 2 — teardown

```bash
databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi
databricks vector-search-endpoints delete-endpoint fleetguard-vs --profile abhi
databricks apps stop fleetguard-console --profile abhi
```

**2.2 — verify billing actually stopped, the next day.** `system.billing` is not queryable
from this account (no `USE SCHEMA` grant on a shared metastore) — this can only be checked
indirectly: confirm `vector-search-endpoints list-endpoints` no longer shows `fleetguard-vs`
the day after deletion, and treat "24h after last index deletion" as the vendor's stated
rule, not something this project can independently confirm from here.

**2.4 — leave the agent endpoint alone.** Do not stop or delete it — scale-to-zero is free
idle and removes a restore step from Run 2. No command needed unless it drifted back to
`Stopped`; check with the same `get` command as 1.3.

**2.5 — touch nothing else.** No command for this step is the point.

---

## Phase 3 — Run 2 (before submission)

> ### ⚠️ "CHANGE NOTHING" DID NOT HOLD. READ THIS BEFORE RUNNING ANY STEP BELOW.
>
> This phase was written assuming no code changed between Run 1 and Run 2, and every step
> said so. **That assumption was broken on 2026-09-23**, deliberately, by the I-115 round
> (merged as `f400331`). Run 2 is therefore **not** the abbreviated restore this section
> originally described — three things must actually be rebuilt, and the steps below have been
> rewritten to say so.
>
> **Following the original wording would have shipped the pre-I-115 build**: an AI Search
> index rebuilt from the old exact-match source table, in which every one of the fleet's
> 2,116 F-250s is missing from retrieval, plus an agent endpoint still on v7. Both would have
> looked like a clean run.
>
> | What changed | Consequence for Run 2 |
> |---|---|
> | `src/search/27_build_chunk_index_source.py` — join widened to `EXACT` + `MODEL_VARIANT` | The **source table must be rebuilt first** (new step 3.0). The index is **179,347** rows, not 115,499 (measured 2026-09-30, I-126). |
> | `src/agent/14_fleetguard_agent.py` — 4 fixes (sentinel, evidence TTL, over-fetch, docstrings) | The agent **must** be rebuilt and redeployed (3.3). v7 does not carry them. |
> | `app/frontend/` + `app/backend/` — Home redesign, `/api/corpus` | `bundle run fleetguard_console` is **required**, not optional (3.2). Screenshots must be retaken (3.5). |
>
> The "change nothing" rule itself was right and is worth keeping *after* this line: do not
> add further changes between Run 2 and submission.

### 3.0 — Rebuild the AI Search source table FIRST (new; no equivalent in Run 1)

**Run 1 had no such step because the table already existed from Phase 0.** It is now stale:
I-115 widened the fleet match from exact model spelling to the `EXACT` + `MODEL_VARIANT` tiers
the rest of the system uses. Build the index off the old table and the retrieval corpus is
silently missing the fleet's most numerous vehicle.

This is Delta-only — free, no endpoint involved, a few minutes — and it must complete before
3.1.

```bash
databricks bundle run build_chunk_index_source -t prod --profile abhi
```

**Measured 2026-09-30 (I-126) and now pinned per tier:** `EXACT` **115,499** +
`MODEL_VARIANT` **63,848** = **179,347**. The job asserts each tier exactly, not a bounded
range — a regression that moved rows *between* tiers while preserving the sum would otherwise
pass, and that is precisely the I-115 failure. A `MODEL_VARIANT` count of zero means the
exact-only scope is still in force and the fleet's 2,116 F-250s are absent from retrieval.

The four documents that said "measured in Run 2" (`STATUS.md`, `ARCHITECTURE.md`,
`EVIDENCE.md`, `DEMO.md`) now carry the figure. Then refresh what the console shows:

```bash
.venv/bin/python scripts/export_corpus.py --profile abhi   # updates Home's scale strip
```

### 3.1 — Recreate the index

**Use the script (added 2026-09-24, I-116). It did not exist when this runbook was written.**

```bash
./scripts/provision_search.sh --check  --profile abhi   # free: what exists right now
./scripts/provision_search.sh --create --profile abhi   # BILLS. prompts before the first charge
```

It runs exactly the step-1.2 commands below — the *config* is unchanged and was proven in Run 1
— and adds the three things a command block cannot:

- **idempotent**: `get-endpoint`/`get-index` first, so a re-run after a dropped connection
  resumes instead of erroring on line one;
- **reads the expected count** from the table 3.0 just rebuilt, rather than carrying a literal.
  **Expect 179,347** (measured 2026-09-30, I-126) — not 115,499, which was the exact-only scope,
  and a result near 115K means *the widening did not take* rather than success;
- **polls with drop detection**, and treats a *decrease* in `indexed_row_count` as fatal. That
  is I-105's only visible symptom, and it is the failure that costs a day.

> **CONFIRMED on abhi 2026-09-30 at 179,347 — the prediction below held to 0.82%.** Kept because
> the reasoning, not just the number, is what made the figure trustworthy before it was measured.
>
> **Where "roughly 180K" came from — measured on free edition 2026-09-30 (I-123), not guessed.**
> That workspace runs the *widened* builder and its index decomposes as **116,252 `EXACT` +
> 64,577 `MODEL_VARIANT` = 180,829**. Its `EXACT` half is within **0.65%** of abhi's recorded
> 115,499, so the two workspaces agree on the *old* basis and the whole gap is the widening.
> The 753-chunk residual is a fresher corpus: free edition holds **2,249,908** complaints vs
> abhi's 2,240,289 (+9,619), which also shifts the generated roster from 47 to 49 make/model
> pairs. So abhi's rebuild should land near 180K — **a few percent lower**, since its corpus
> and roster are slightly smaller.
>
> **It is now a pin.** Step 3.0 printed 179,347 and an independent ad-hoc count of the same
> predicate, run before the job, returned the same three numbers — so the builder asserts each
> tier exactly (I-126). If a future run comes back near 115K the `EXACT`-only join is back in
> force and the F-250s are excluded again, which is the exact failure I-115 was filed for.

The manual path still works and is what the script wraps, if it ever needs to be run by hand:

```bash
databricks vector-search-endpoints create-endpoint fleetguard-vs STANDARD --profile abhi
# then the same create-index command as 1.2
```

Poll interval at ~4,336 rows/min from 3.0's count, not Run 1's ~27 min.

### 3.1a — Run the RAG retrieval evaluation (NEW, 2026-09-24)

Needs the index and nothing else, so it goes here rather than after the App work.

```bash
databricks bundle run rag_eval -t prod --profile abhi
```

Writes `ops_rag_eval` and re-runs the three I-040 behavioural checks at the shipped scope — a
debt open since the rescope. **Record the figures in `docs/EVIDENCE.md` as measured, including
if they are poor** (I-049 / I-111's rule).

Read family A (known-item) first: it is a floor test, the query text is literally in the corpus,
and a hit rate below ~0.9 means the *index* is wrong rather than the retriever. Check
`indexed_row_count` against `silver_complaint_chunk_indexed` before concluding anything about
retrieval quality. If HYBRID and ANN return identical results on check 3, the index subtype did
not take and every number above it is void.

### 3.2 — Start the App

```bash
databricks apps start fleetguard-console --profile abhi
```

**`bundle run fleetguard_console` IS REQUIRED this time** — the console changed (Home
redesign, the new `/api/corpus` route). `apps start` alone re-deploys the *old* source path
(I-097), so skipping this ships the Run 1 UI:

```bash
databricks bundle deploy -t prod --profile abhi          # or ./scripts/deploy.sh abhi prod
databricks bundle run fleetguard_console -t prod --profile abhi
```

### 3.3 — Agent: REBUILD and REDEPLOY, not just wake

**Not "no action" any more.** v7 predates I-115's four agent-source fixes — the forgeable
action sentinel, the permanently-poisoned evidence cache, the lossy `search_complaints`
dedupe, and the stale corpus docstrings. Waking v7 ships none of them.

```bash
databricks bundle run agent_build -t prod --profile abhi     # logs a new UC model version
databricks bundle run deploy_agent -t prod --profile abhi    # serves it
```

Then confirm the **new** version is the one taking traffic. I-050/I-092 have recurred four
times: the previous version stays `DEPLOYMENT_READY` at 0% traffic and the endpoint looks
healthy either way.

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi
```

Only if it drifted to fully `Stopped` rather than scale-to-zero, repeat 1.3's `update-config`
restore first.

### 3.3a — Evaluate the agent (NEW, 2026-09-24 — and it was missing entirely)

```bash
databricks bundle run evaluate_agent -t prod --profile abhi
```

**This step did not exist, in Run 1 or Run 2.** The agent was rebuilt, redeployed and
demonstrated without ever being scored — so `16_evaluate_agent.py`'s **hard gates were inert
through the whole submission**. They fail the job on a safety regression (claiming a campaign
was launched, inventing a recall, and as of I-118 **acting on an instruction embedded in a
retrieved complaint narrative**), which is worth nothing if nothing runs them.

It is also what **stamps the model version** with its evaluation result (`eval_run_id`,
`eval_hard_gates`, a `score_*` tag per scorer). Skip this and the deployed version carries no
evidence it was ever evaluated — the same provenance gap `/api/readyz` closes for the live
system, one layer down.

Runs against the **latest registered version** on its own — do not pass `model_version`; a
pinned widget is what silently scored a three-version-stale model once (I-094/I-098).

**Reading the result:**

- The job **raises on a hard-gate failure**. That is the intended behaviour, not a broken run —
  `databricks bundle run` will report failure. Use `17_inspect_eval` with the printed run id to
  see which case and why.
- `resists_injected_instructions` and `cites_complaint_ids` are **new and have never scored
  against a live model.** If injection resistance fails, read the answer before assuming the
  agent complied: the scorer is assertion-based (I-058), and a refusal that *names* the action
  it is declining is the correct answer.
- Expect ~15 cases and roughly 10-20 minutes; it calls the serving endpoint once per case.

### 3.4 — Abbreviated verification

One agent question through the console, one write (e.g. `open_defect_signal` via the
Assistant, or an approval), confirm it reaches `gold_agent_action` / `gold_defect_signal_current`
via the CDF→gold job (already `UNPAUSED`, fires on its own — no command needed, just check
the resulting row):

```bash
databricks experimental aitools tools query "SELECT * FROM bootcamp_students.fleetguard.gold_agent_action ORDER BY _last_updated DESC LIMIT 3" --profile abhi
```

### 3.5 — Fresh screenshots — REQUIRED, the UI changed

Home was redesigned on 2026-09-23 (new hero, flow diagram, corpus scale strip), so every Run 1
screenshot of it is stale. Same two commands as 1.8, and note `scripts/run_local_static_dev.sh`
mints a token that expires after an hour — if Lakebase routes start 500ing mid-capture, restart
it rather than debugging.

### 3.6 — Update provenance, assemble the zip

Same five-command block as 1.9, then:

```bash
git rev-parse HEAD   # WILL NOT match the 1.9 value — I-115 landed in between (see the
                     # banner at the top of Phase 3). Record the new SHA; a mismatch here is
                     # the expected outcome, not an error.
zip -r fleetguard-submission.zip . -x '.git/*' '*/node_modules/*' '.venv/*' \
  'app/frontend/dist/*' 'capstone-submission-requirement.pdf' 'repo-review.md' \
  'indexing-review.md'
```

`docs/screenshots/` is included (it's gitignored, not zip-ignored) — the requirement PDF and
the external review files are excluded from both. Verify the zip actually contains
`docs/screenshots/*.png` before calling this done — an empty directory from a skipped 1.8/3.5
zips silently with nothing in it.
