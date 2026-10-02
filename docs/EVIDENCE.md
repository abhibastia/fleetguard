# Evidence map

**Last updated 2026-09-20.** One page pointing at the concrete artefact behind every graded
claim — file, table, job, measured number, screenshot.

It exists because the demo surfaces are **asleep between sessions on purpose**: the
Databricks App is stopped, the agent endpoint is scaled to zero, and the AI Search index is
deleted to cap billing (I-101, I-105). Someone reading this repo cold cannot click through
the product, and the capstone rubric records anything it cannot verify as *unverified* —
listing deployment URL, Spark execution logs, Lakebase schema, CDF configuration, dataset
size, measured latency, API error-handling code, agent tool definitions, and screenshots as
the usual gaps. Every one of those is **reachable** from here; this page says how, and says
plainly which ones need a resource woken up first.

**Nothing on this page is an estimate.** Where a number could not be measured, it says so.

> ### Screenshots are NOT in git — regenerate them before you need them
>
> `docs/screenshots/` is **gitignored**. The images are build output: they go stale the
> moment the UI changes, and a stale screenshot is worse than none because it looks like
> evidence. Regenerating them takes about a minute and needs nothing but a local server:
>
> ```bash
> scripts/run_local_static_dev.sh 8811                  # live Lakebase, ~1h token
> .venv/bin/python scripts/capture_screenshots.py       # 20 images, both themes
> ```
>
> **Do this as part of assembling the submission zip**, not earlier — the zip is the
> artefact that carries them, and generating them at submission time means they match the
> code being submitted. If you are reading this on a cold start and `docs/screenshots/` is
> empty or missing, that is the expected state, not a loss.
>
> The script refuses to run against snapshot mode and waits for each view's loading skeleton
> to clear, so what it produces is real data or nothing. See *Regenerating this evidence* at
> the bottom.

---

## The eight rubric categories

### 1. Spark data pipeline

| | |
|---|---|
| Code | `src/pipelines/bronze/*.sql` (4), `src/pipelines/silver/*.sql` (5), bound as `resources/bronze_silver.pipeline.yml` |
| Scale | **8.44M bronze rows** — complaints 2,240,289 · recalls 244,925 · investigations 154,367 · TSBs 5,801,279 |
| Quality | Failure reasons computed **once** in a staging view and driving both the silver and quarantine predicates, so the two cannot drift. `bronze = silver + quarantine` exactly, on every table (ARCHITECTURE §4.2) |
| Re-runnable | Streaming tables + Auto Loader; incremental by construction. Adding files to a monitored directory reprocesses only those files — verified |
| Tests | `tests/pipelines/` unit-tests the SQL transformation logic against a local Spark session; `tests/test_data_quality.py` asserts the reconciliation against live tables (`-m integration`) |
| Trap worth knowing | `read_files` **must** set `quote => '\0'`. Default quoting silently corrupted 143 rows while `_rescued_data` read **0 in both cases** — zero rescued rows is necessary, not sufficient |

### 2. Third-party API integration

| | |
|---|---|
| APIs | `api.nhtsa.gov/recalls/recallsByVehicle` · `vpic.nhtsa.dot.gov/api` (`DecodeVINValuesBatch`) · `static.nhtsa.gov` flat files (`If-Modified-Since`) |
| Retry / backoff | `src/fleetguard/http_retry.py` — exponential with full jitter, `Retry-After` honoured, **400 deliberately never retried** (NHTSA answers an unknown combo with HTTP 400 *and* a body reading "Results returned successfully", I-031) |
| Validation | `validate_recalls_payload` cross-checks the API's own `Count` against `len(results)`, so a 200 with a changed shape is `malformed` rather than indistinguishable from "no recalls" |
| Failure accounting | `ops_recall_api_sweep`, one row per sweep; `gold_api_poll_health` aggregates it |
| The gate | Above a 10% combo failure rate the sweep **refuses** to rebuild `gold_recall_alert` and fails the run, so a mostly-failed sweep cannot silently replace the alert table (I-106) |
| Tests | `tests/test_http_retry.py` — **59 tests**, no network and no clock; the sleeper is asserted on, so backoff is verified rather than assumed |
| Measured live 2026-09-20 | **200/200 combos ok in 102 s**, 2,122 campaign rows. Gate tripped deliberately → run failed and `gold_recall_alert` stayed at **Delta version 1**, proving the rebuild was skipped rather than only reported as skipped |
| Displayed in the app | **Recall API** tab (`app/frontend/src/views/RecallApi.tsx`; screenshot `recall-api-*.png` once regenerated) and the dashboard's Operations page |

### 3. Lakebase data model

| | |
|---|---|
| Schema | 16 `fleetguard_*` tables. Column-level reference: ARCHITECTURE §4.6 |
| Keys | Primary key on every table; `BIGSERIAL` where there is no natural key |
| Constraints | `CHECK` on work-order status, non-negative cost, signal source, approval decision, exposure-has-a-source; partial unique indexes for campaign idempotency and watchlist de-duplication |
| Referential integrity | **14 foreign keys** (ARCHITECTURE §4.6a), added 2026-09-20 after scanning all 14 candidate relationships clean against live data — zero orphans across 118,323 exposure rows. No `CASCADE` anywhere; one `SET NULL`; `audit_log` deliberately excluded because its `entity_id` is polymorphic and an audit row must outlive what it describes |
| Indexes | **23** named `ix_fg_*`/`ux_fg_*`, including five added specifically as FK child indexes |
| Timestamps / audit | `created_at`/`updated_at` defaults throughout; `fleetguard_audit_log` is append-only with `before_state`/`after_state` JSONB |
| Governance | Postgres RLS on `fleetguard_vehicle`, `ENABLE` **and** `FORCE` (the owner is not exempt). Fail-open by construction and nobody is enrolled — the mechanism is real, the enrolment is future work, and ARCHITECTURE §8a says so |
| App reads and writes it | Every authenticated route goes through `db.py connect(principal)` under the **caller's own** OBO token |

### 4. Action-taking AI agent

| | |
|---|---|
| Tools | 7 — ARCHITECTURE §7.2. Five read (`search_complaints`, `lookup_fleet_exposure`, `lookup_fleet_models`, `lookup_emerging_signals`, `propose_service_campaign`), **two write** (`open_defect_signal`, `watch_campaign`) |
| Write mechanics | §7.1. The model returns an action envelope; `app/backend/fleetguard_api/agent_actions.py` validates it with Pydantic, checks `authz.may_approve`, and executes the insert **under the caller's own token** — so `opened_by` is a real human and RLS applies as it does to a UI click. The model never holds a database path |
| Safeguards | Field-level bounds on every LLM-supplied value; authorization gate on both writes; unique-violation handling; audit row in the same transaction; existence checks that return a 404 naming the missing entity rather than a bare `ForeignKeyViolation` |
| Never reaches | `fleetguard_work_order` / `fleetguard_service_campaign` — dispatch stays behind `FLEETGUARD_APPROVERS`, asserted in the build |
| Tracing / eval | MLflow tracing (typed `RETRIEVER`/`TOOL` spans), inference table `fleetguard_agent_payload`, `mlflow.genai.evaluate` against **15** adversarial cases (E-05) and a 765-pair golden set built from NHTSA's own recall text. **Three scorers are HARD GATES** — the job fails if the agent claims a launch, invents a recall, or acts on an instruction embedded in retrieved text. The result is **tagged onto the UC model version** (`eval_run_id`, `eval_hard_gates`, a `score_*` per scorer), so "was the deployed artefact evaluated?" is answerable from the registry rather than by hunting runs |
| **Agent evaluation — MEASURED 2026-09-30 on v8** | `fleetguard_agent` **v8**, run `1199cf9f6f5e4acc884909c091f058a6`, 15 cases. **All three hard gates 1.000** — `never_claims_launched`, `never_invents_a_recall`, and **`resists_injected_instructions`, the first live measurement of the I-118 defence on this workspace**. Also `safety` 1.000, `answer_not_empty` 1.000, `cites_complaint_ids` 1.000, `grounded_numbers` 0.933, `relevance_to_query` 0.933, `fleetguard_rules` 0.800. **`states_match_tier` scored 0.000 and that is a measurement artifact, not an agent failure** — one case, and the scorer's sentence splitter does not break on em-dashes, so an unrelated *"so no confirmation is needed"* clause negates an otherwise correct tier statement; resampling the same question 5× passes 4/5. Diagnosed in I-126, not fixed before submission because `_asserts` is shared with the three hard gates. `eval_hard_gates=passed` is stamped on v8, so `/api/readyz`'s release check reads a real tag |
| **Prompt-injection defence** | `search_complaints` is the only tool returning text this project did not write — **2.24M public, user-submitted ODI narratives**. Three layers (ARCHITECTURE §7.1a): untrusted-data markers + action-sentinel stripping in `_neutralise()`, system-prompt rule 9, and the console's Python-stamped item-id check. Structural half tested offline (`tests/agent/test_agent_injection.py`, mutation-checked); behavioural half is the hard gate above, **measured in Run 2**. Severity is bounded by `may_approve` and server-side fleet-relevance recomputation — stated in I-118 rather than implied |
| **Citations** | Rule 10 requires complaint ids behind narrative claims, scored by `cites_complaint_ids`. **The scorer's limit is documented in the scorer**: it sees answer text only, so it cannot distinguish a real citation from a fabricated one — "cites only ids a tool returned" remains a prompt rule, not a measurement |
| Activity analytics | `gold_agent_activity_daily` — requests, write actions, success rate, latency percentiles and tokens by day/tool/actor |
| **Verified live 2026-10-01 (Run 2), on v8** | Both paths exercised by raw REST (the CLI truncates this endpoint's response — I-124). **Deterministic:** *"Which fleet vehicles does recall 17V629000 affect?"* → **25 vehicles across 22 depots, all EXACT**, 8.3 s warm, volunteering that there were no `MODEL_VARIANT` matches to flag. **Retrieval:** *"Search complaints about brake failures"* → five real narratives, each with its complaint id (`738214`, `729017`, `667249`, `782129`) — the 179,347-chunk index genuinely serving the agent. **Write:** an agent write through `/api/chat` created `AGENT-ac6d0e08b2d1` and reached `gold_defect_signal_current` in ~5 min via Lakebase CDF |
| **Screenshots: captured, not deferred** | `assistant-dark.png` / `assistant-light.png` show the panel answering with a cited table. Asked only to search, the agent **volunteered a correction** — *"every one is about the PARKING / emergency brake, not the primary hydraulic service brakes — so treat this as a parking-brake signal"* — qualifying its own retrieval rather than dumping it. Also `walkthrough-dark.webm` / `.mp4`, a 7-beat recording following `docs/DEMO.md`'s order |
| **State right now** | Index **deleted** and the serving endpoint **STOPPED**, deliberately, to end billing after evidence capture. A live question therefore needs both restored: endpoint ~3 min (`update-config`, RUNBOOK 1.3 — **Stop is UI-only, restore is CLI-only**, I-126), index **~75 min** measured (~21 min of I-112 stall, then ~52 min syncing). Code, tool definitions, traces, screenshots and the video are all in this zip |

### 5. Analytics pipeline

| | |
|---|---|
| Mechanism | **Lakebase Change Data Feed** → `bootcamp_students.bootcamp_cdc.lb_fleetguard_*_history` → `src/lakebase/21_cdf_to_gold_facts.py`, on a Jobs `table_update` trigger |
| Incremental | `_sort_by` high-water mark + `MERGE`, with DELETE and UPSERT in one statement. `full_refresh=true` forces the whole-history path, which is kept rather than deleted |
| Not trusted blindly | Every run reconciles the fact against the **entire** history. On a mismatch it rebuilds from scratch, records the drift and **fails** — correct data *and* a loud noise |
| Regression guard | The I-080 tombstone bug (deletes filtered before ranking resurrect deleted rows) is asserted against on every run |
| Metrics | `gold_agent_action`, `gold_defect_signal_current`, `gold_agent_activity_daily`, `gold_api_poll_health`; monitoring in `ops_cdf_fact_refresh` and `ops_recall_api_sweep` |
| Dashboard | `FleetGuard — Fleet & Recall Overview`, 5 pages / 20 datasets / 2 UC metric views consumed via `MEASURE(...)` |
| Measured live 2026-09-20, unattended | Trigger fired for an insert (`INCREMENTAL`, 50 → 51) and a delete (`INCREMENTAL`, 51 → 50, **tombstone applied, key not resurrected**), with `SKIP` runs in between doing no work |

### 6. Frontend and core workflow

> **"Vehicles exposed" means distinct VINs, everywhere — fixed 2026-10-02 (I-130).** It did not
> used to. The console derived its stat cards from the fetched page, so *Campaigns* showed the
> API's `limit` (50, against 393 live) and *Vehicles exposed* summed per-campaign counts,
> counting a VIN once per campaign (51,615, against 11,323 distinct). The AI/BI dashboard was
> correct throughout — `fleet_exposure_metrics` has always defined the measure as
> `COUNT(DISTINCT vin)` — so the two surfaces disagreed by 4.6x. `/api/queue/summary` now copies
> the metric view's measures verbatim, so they agree by construction.
>
> **The two still differ in magnitude, and should.** The dashboard reads Delta
> `gold_fleet_exposure` (~989k match rows); the console reads the Lakebase operational subset
> (118,323 rows / 393 campaigns / 11,323 VINs). Same semantics over different populations — if
> they ever printed the *same* number, one of them would be reading the wrong store.

| | |
|---|---|
| Stack | FastAPI + React/TypeScript, one service, no CORS, SPA deep-link fallback |
| Views | 10 — Home, Recall queue, Emerging, Evidence, Launched, Work orders, Audit log, Depots, Trends, Recall API — plus the Assistant dock |
| States | Every view handles gated (401) → first-load error → loading skeleton → empty dataset → no-match-after-filter, in that order |
| Consequential actions | Approval is a confirmation flow behind `FLEETGUARD_APPROVERS`, writes campaign + N work orders + audit in **one transaction**, and returns `409` naming the existing campaign on a re-approval (I-063) |
| Tests | **147 frontend** (23 files) + **478 backend** |
| Screenshots | `scripts/capture_screenshots.py` — all 10 views, **both themes**, from live Lakebase. Output is gitignored; regenerate before submitting (see the box at the top) |

### 7. Deployed application

| | |
|---|---|
| URL | `https://fleetguard-console-1352785079224954.aws.databricksapps.com` |
| Deployment | Declarative Automation Bundle — `databricks.yml` + `resources/` own the App, the pipeline, the dashboard and **29 jobs**, all bound to existing objects |
| Setup documented | README *Deploying*; ARCHITECTURE §9.1 lists the **five** things the bundle does not cover |
| Secrets / config | No secrets in the repo. The App holds **no privileges of its own**: `db.py` mints the Lakebase credential from the caller's forwarded token, so every read runs as the signed-in human |
| Auth | Databricks Apps OBO; scopes declared as code in `resources/fleetguard_console.app.yml` |
| **Verified live 2026-10-01 (Run 2)** | Current code shipped (`bundle run fleetguard_console`, deployment `01f1bd1a56dc1412b17a1725772b2781`, SUCCEEDED) and exercised: **all 12 `/api/*` routes returned 200 against live Lakebase**, `/api/readyz` returned **200 with all five checks `ok`**, and the malformed-input guard returned **422 naming the field** (`NaN` and `Infinity`) rather than 500. Release provenance is served by the App itself: `console built at b44339c7e6…` |
| **State right now** | **STOPPED**, deliberately, once that verification was captured — the same Phase 2 teardown as the index. **It is ~2 min to bring back** (`databricks apps start fleetguard-console`) and **all three reviewers hold `CAN_MANAGE`**, so it is self-service rather than blocked. A stopped App is a cost decision recorded in `docs/STATUS.md`, not neglect — but it does mean a reviewer who does not start it sees nothing, so the 22 screenshots and the walkthrough video in this zip are the standing evidence |

### 8. Big Data — two of the three Vs

**Volume — demonstrated.** 8.44M bronze rows through a distributed Spark pipeline;
989,042 exposure rows; 118,323 in Lakebase — the full corpus (2.2M complaints, 5.8M TSBs)
stays in Delta regardless of AI Search scope. **The vector-index chunk count is no longer
the 1,746,601 quoted historically** — rescoped 2026-09-23 to the fleet's own make/model pairs
for schedule safety on the submission's two-build plan (I-111), measured at 115,499 for the
exact-spelling scope, then widened the same day to the `EXACT` + `MODEL_VARIANT` tiers the
rest of the system uses (I-115). **Measured 2026-09-30 in Run 2 (I-126): 179,347 chunks**
— 115,499 `EXACT` + 63,848 `MODEL_VARIANT`, with the `EXACT` half reproducing the 2026-08-31
figure to the row, and the total agreeing exactly with an independent ad-hoc count run before
the job. The lakehouse-scale claim survives on the pipeline/corpus side; it is specifically the
*indexed* figure that dropped below the 1M mark, and that reflects a deliberate retrieval-scope
decision, not reduced volume processed.

**Variety — demonstrated, with one caveat.** Free-text complaint narratives are chunked,
embedded, indexed and hybrid-searched (BM25 + vector), and surfaced through the agent's
`search_complaints` tool into the application workflow. `silver_complaint_chunk_indexed`
(fleet make/model scope) is in Unity Catalog now; **the AI Search index itself is deleted**
to cap billing between verification runs, so the retrieval half cannot be demonstrated live
until it is rebuilt. **Measured end to end on 2026-09-30: ~21 min of I-112 stall in which nothing
appears to happen, then ~52 min of syncing — budget ~75 min, not the ~27-39 min this line used to
claim** (I-126, I-127; the older figure was the 115,499-chunk scope). `scripts/provision_search.sh --create` is the rebuild, idempotent and
polling for I-105's restart-from-zero.

#### Retrieval quality — MEASURED 2026-09-30 on the shipped 179,347-chunk index

`ops_rag_eval`, run `2026-09-30T23:34:06Z`, seed `20260924`, k=10. Published as measured, per the
rule I-049 and I-111 set — and in this case the result is good, which is not a reason to present
it any differently.

| family | type | probes | hit rate | Recall@10 | P@10 | MRR | relevant pool |
|---|---|---|---|---|---|---|---|
| known-item | **HYBRID** | 50 | **100.0%** | **1.000** | 0.100 | 0.532 | 1 |
| known-item | ANN | 50 | 34.0% | 0.340 | 0.034 | 0.234 | 1 |
| topical | **HYBRID** | 48 | **83.3%** | 0.064 | **0.371** | **0.627** | 500 |
| topical | ANN | 48 | 70.8% | 0.036 | 0.354 | 0.609 | 500 |

**Read known-item first — it is the floor test, and it passes perfectly.** The query text is
literally in the corpus, so anything below ~0.9 would mean the *index* is wrong rather than the
retriever. HYBRID found the source chunk for **50 of 50** probes. `P@10 = 0.100` is not a weak
score here: exactly one chunk is relevant out of ten returned, so 0.1 **is** the ceiling.

**HYBRID beats ANN on every metric in both families**, which is the first evidence this project
has that the subtype choice is load-bearing rather than a configuration preference. The
known-item gap is the striking one: **pure vector search fails to retrieve the exact source chunk
two times in three (0.340) even when the query text is that chunk.** It also settles the check
I-040 flagged — if HYBRID and ANN had returned identical results the subtype would not have taken
and every number above would be void. They differ by a factor of three.

**`relevant_pool_size` is why topical Recall@10 = 0.064 is not a failure.** The median relevant
pool is 500 chunks and k is 10, so recall is bounded near the floor by construction; the notebook
publishes the pool beside it precisely so a correct small number is not read as broken retrieval.
**P@10 and MRR are this family's real metrics** — 0.371 means roughly 4 of 10 returned chunks are
genuinely about the campaign's defect, and MRR 0.627 means the first relevant hit is typically at
rank 1 or 2.

**The one soft spot, stated rather than buried:** known-item MRR is 0.532 while its recall is
1.000. The correct chunk is always retrieved but frequently not ranked first — retrieval is
excellent, ranking is middling. Nothing downstream depends on rank-1 (the agent reads the whole
top-k), so this is a real limitation with no current consequence, not a defect.

All three I-040 behavioural checks passed in the same run: `columns_to_sync` complete,
`any_harm` filter effective, HYBRID differing from ANN.

**The harness was committed before any of this was measured.** Until 2026-09-24
the strongest claim available here was *"AI Search is implemented"* plus a three-query
behavioural probe — which shows the feature is wired up, not that it works.
`src/search/28_rag_eval.py` (job `fleetguard-rag-eval`) scores two probe families over the
live index and writes `ops_rag_eval`: **known-item** (a distinctive excerpt from one narrative;
Recall@10 and MRR are meaningful because the relevant set has one member) and **topical** (a
question built from a real recall campaign; Precision@10 and hit rate, because the relevant set
runs to thousands of chunks and Recall@10 over it would read as ~0.003 and mean nothing). It
re-runs the three I-040 behavioural checks at the shipped scope in the same pass — a debt open
since the rescope. **The scoring arithmetic is unit-tested offline**
(`tests/test_retrieval_metrics.py`), so it is not debugged inside a billed window; only the
numbers wait for the index. **Whatever it returns gets published**, the rule I-049 and I-111
already set. The limitation belongs next to the result: relevance here is *metadata* agreement
— right make, right model under the EXACT/MODEL_VARIANT rule, right component — not a human
judging whether the narrative answers the question.

**Velocity — partial, and stated honestly in two numbers.** These must never be collapsed:

| | measured |
|---|---|
| CDF capture (Postgres → Unity Catalog) | **7.1–15.6 s** (I-046, n=3) — *and* **212–245 s** on a fourth sample (I-108, 2026-09-20) |
| Full chain to a gold fact | **2.5–4.5 min** (155 s and 269 s, n=2) |

The `table_update` trigger has a **hard 60-second platform floor** on both of its intervals
(I-081), so a sub-minute Postgres→gold path is not achievable at all — the proposal's 15 s/5 s
settings are rejected by `jobs create`. Capture alone has been sub-minute on three of four
samples; **neither figure is a bound**, and the end-to-end number is minutes. Say "a few
minutes" for the round trip.

---

## Where the rubric's named gaps are answered

| Rubric "evidence gap" | Where |
|---|---|
| Deployment URL | §7 above; README *Live demo* |
| Spark execution logs | Job run pages per `resources/*.job.yml`; `ops_*` tables hold the durable record — `ops_lakebase_load`, `ops_cdf_fact_refresh`, `ops_recall_api_sweep`, `ops_cdf_latency` |
| Lakebase schema / connection code | ARCHITECTURE §4.6 and §4.6a; DDL in `src/lakebase/08_`, `15_`, `17_`, `22_`, `24_`, `25_`; connection in `app/backend/fleetguard_api/db.py` |
| CDF configuration | ARCHITECTURE §4.5. **UI-only** — not a bundle resource, not scriptable; it is a manual runbook step in any rebuild, and that is documented rather than glossed |
| Dataset size | §1 and §8 above |
| Measured processing latency | §8 above, with the two-number caveat |
| API request and error-handling code | `src/fleetguard/http_retry.py`, `src/ingest/05_poll_recalls_api.py`, `src/fleet/04_build_fleet_registry.py` |
| Agent tool definitions | `src/agent/14_fleetguard_agent.py`; reference table in ARCHITECTURE §7.2 |
| Screenshots / demo transcripts | **Both, and in the zip.** `docs/TRANSCRIPTS.md` — five verbatim agent exchanges, *generated from the saved JSON responses rather than retyped*. `docs/screenshots/` — 22 stills (both themes, incl. the Assistant answering with citations), `walkthrough-dark.webm` + `.mp4` (7 beats, following `docs/DEMO.md`'s order), and `manifest.json` with per-beat timings. `docs/DEMO.md` is the guided walkthrough. The transcripts exist because an image and a video are only evidence to a reader that can open them |

## Deliberately still missing

Named so the gap is bounded rather than discovered.

| Gap | Why | Cost to close |
|---|---|---|
| ~~**Assistant screenshot / agent transcript**~~ | **CLOSED 2026-10-01** — captured in both themes plus a 7-beat walkthrough video (I-125). It had been deferred since the script was written, because the two things it needs are the two the cost plan tears down between runs | — |
| ~~**App serving current code**~~ | **CLOSED 2026-10-01** — `bundle run fleetguard_console` shipped deployment `01f1bd1a…` and 12 routes were exercised against it | — |
| **Live App running** | Stopped after verification to end billing. **This is the one gap a reviewer meets directly:** the URL answers nothing until started | ~2 min, self-service — all three reviewers hold `CAN_MANAGE` |
| **Live agent / retrieval** | Endpoint stopped and index deleted after evidence capture, same reason | ~3 min + ~75 min, both documented in `docs/RUNBOOK.md` |
| **CD on merge** | Needs an account-level OIDC federation policy this identity cannot create (I-100). The rejected alternative — a PAT in GitHub secrets, reaching a ~296-student metastore — is recorded rather than quietly adopted | blocked on account access, not effort |

## Regenerating this evidence

Nothing here is hand-maintained. Each artefact has a command, and every command is safe to
re-run.

| Artefact | Command | Needs |
|---|---|---|
| **Screenshots + walkthrough video** (gitignored) | `scripts/run_local_static_dev.sh 8811` then `.venv/bin/python scripts/capture_screenshots.py` | a local server on live Lakebase; ~1 min for 22 stills, plus the agent for the Assistant shots and the video's final beat |
| **Backtest numbers** (`evidence.json`, committed) | `.venv/bin/python scripts/export_evidence.py --profile abhi` | SQL warehouse |
| **Counts in this page** | see *Checking the numbers on this page* below | Lakebase + CLI |

Two properties of the screenshot script worth knowing before trusting its output:

- **It refuses to run against snapshot mode.** Evidence must come from live data; capturing
  the demo snapshot and presenting it as the product would be the exact failure the Evidence
  tab exists to avoid.
- **It waits for each view's loading skeleton to clear.** `networkidle` is not enough — every
  view fetches on mount from a `useEffect`, so the document goes idle before the API call is
  issued. The first run produced twenty pixel-perfect screenshots of placeholders and
  reported success. A screenshot of a spinner is worse than no screenshot: it is evidence
  that the page does not work.

### Checking the numbers on this page

Every count above was measured, not asserted, and none of them should be trusted after the
schema changes again. To re-check:

```bash
ls resources/*.job.yml | wc -l                                    # bundle jobs
.venv/bin/python -m pytest tests/test_http_retry.py --collect-only -q | tail -1
jq '.datasets | length' dashboards/fleetguard_overview.lvdash.json  # dashboard datasets
```

and against live Postgres — table count, foreign keys, indexes, and the replica-identity
invariant that I-107 was hiding in:

```sql
SELECT COUNT(*) FROM pg_tables
 WHERE schemaname = 'bootcamp_students' AND tablename LIKE 'fleetguard_%';
SELECT COUNT(*) FROM pg_constraint
 WHERE connamespace = 'bootcamp_students'::regnamespace AND contype = 'f';
SELECT relname, relreplident FROM pg_class c
 JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'bootcamp_students' AND relname LIKE 'fleetguard_%' AND relkind = 'r';
```

The last one is the check that did not exist before 2026-09-20, and its absence is why one
table silently failed to replicate for weeks (I-107). Run it across the **whole schema**,
not per table — that was the actual lesson.
