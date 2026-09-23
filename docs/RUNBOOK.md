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
| index source table | `bootcamp_students.fleetguard.silver_complaint_chunk_indexed` (115,499 rows, fleet make/model scope — I-111) |
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

> **BLOCKED here on the first attempt, 2026-09-23 (I-112) — read before running this again.**
> Two independent fresh endpoint+index creations both stuck at `ready: false` /
> "pending endpoint provisioning" indefinitely (~25 min and ~5 min observed), underlying
> pipeline `IDLE`, zero sync activity, `sync-index` refuses ("not ready"). No cost impact
> (`IDLE` burns no compute) but no progress either. **Both stuck resources were left live** —
> check them first before creating a third:
> ```bash
> databricks vector-search-indexes get-index bootcamp_students.fleetguard.complaint_chunk_smoke_idx --profile abhi
> ```
> If still stuck, next real test per I-112 is pointing a fresh index straight at
> `silver_complaint_chunk_indexed` (skipping the smoke step) to check whether the 10K `LIMIT`
> scratch table itself is the trigger — accepting the larger real-scope cost exposure that
> implies. Full writeup in `docs/ISSUES.md` I-112.

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
1.75M-row run there is no reason to space polls 45 min apart):

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

Done when `status.ready: true` and `indexed_row_count == 115499`.

### 1.3 — Restore the agent endpoint

Current live state (checked 2026-09-23): `DEPLOYMENT_STOPPED`. There is no `start`
subcommand — only `update-config`, rebuilt from the *live* served-entity config so nothing
(especially `environment_vars`, which silently misfiles MLflow tracing if dropped) is lost:

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
live smoke test at the end. Re-assert scale-to-zero afterward — `agents.deploy()` resets it
to `False` on every call, silently, every time it has been checked:

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi
# then update-config again as in 1.3, scale_to_zero_enabled: true
```

Confirm the version that's actually serving matches what you just registered — the mismatch
history here (three silent old-version survivals: v1, v4, v5) is exactly why this checks the
live resource rather than trusting the deploy call's return value:

```bash
databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi \
  | grep -A3 '"entity_version"'
```

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

### 3.1 — Recreate the index, identical config

Exact same `create-index` command as step 1.2, unchanged. If `silver_complaint_chunk_indexed`
was not touched between runs (it should not have been — "change nothing between Run 1 and
Run 2"), this reproduces the same 115,499-row index.

```bash
databricks vector-search-endpoints create-endpoint fleetguard-vs STANDARD --profile abhi
# then the same create-index command as 1.2
```

### 3.2 — Start the App

```bash
databricks apps start fleetguard-console --profile abhi
```

Only re-run `databricks bundle run fleetguard_console -t prod --profile abhi` if code changed
since Run 1 — per the "change nothing" rule, it should not have.

### 3.3 — Agent endpoint

No action — first call wakes it from scale-to-zero (~47 s measured, I-092). If it drifted to
fully `Stopped`, repeat 1.3's `update-config` restore.

### 3.4 — Abbreviated verification

One agent question through the console, one write (e.g. `open_defect_signal` via the
Assistant, or an approval), confirm it reaches `gold_agent_action` / `gold_defect_signal_current`
via the CDF→gold job (already `UNPAUSED`, fires on its own — no command needed, just check
the resulting row):

```bash
databricks experimental aitools tools query "SELECT * FROM bootcamp_students.fleetguard.gold_agent_action ORDER BY _last_updated DESC LIMIT 3" --profile abhi
```

### 3.5 — Fresh screenshots

Only if the UI changed since Run 1 (it should not have). Same two commands as 1.8.

### 3.6 — Update provenance, assemble the zip

Same five-command block as 1.9, then:

```bash
git rev-parse HEAD   # confirm identical to the 1.9 value if "change nothing" held
zip -r fleetguard-submission.zip . -x '.git/*' '*/node_modules/*' '.venv/*' \
  'app/frontend/dist/*' 'capstone-submission-requirement.pdf' 'repo-review.md' \
  'indexing-review.md'
```

`docs/screenshots/` is included (it's gitignored, not zip-ignored) — the requirement PDF and
the external review files are excluded from both. Verify the zip actually contains
`docs/screenshots/*.png` before calling this done — an empty directory from a skipped 1.8/3.5
zips silently with nothing in it.
