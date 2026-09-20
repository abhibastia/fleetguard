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
| Tracing / eval | MLflow tracing, inference table `fleetguard_agent_payload`, `mlflow.genai.evaluate` against 10 adversarial cases (E-05) and a 765-pair golden set built from NHTSA's own recall text |
| Activity analytics | `gold_agent_activity_daily` — requests, write actions, success rate, latency percentiles and tokens by day/tool/actor |
| **Not verifiable right now** | The serving endpoint is `DEPLOYMENT_STOPPED` and the AI Search index is deleted, so a live question cannot be asked without restoring both (~3 min and ~7 h respectively). The code, tool definitions and past traces are all in the repo; the Assistant screenshot is **deferred** — see below |

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
| Deployment | Declarative Automation Bundle — `databricks.yml` + `resources/` own the App, the pipeline, the dashboard and **27 jobs**, all bound to existing objects |
| Setup documented | README *Deploying*; ARCHITECTURE §9.1 lists the **five** things the bundle does not cover |
| Secrets / config | No secrets in the repo. The App holds **no privileges of its own**: `db.py` mints the Lakebase credential from the caller's forwarded token, so every read runs as the signed-in human |
| Auth | Databricks Apps OBO; scopes declared as code in `resources/fleetguard_console.app.yml` |
| **State right now** | **STOPPED** to avoid idle billing. `databricks apps start fleetguard-console`, ~2 min; all three reviewers hold `CAN_MANAGE` and can start it themselves. Also **not redeployed since 2026-09-14**, so the live App lags `main` |

### 8. Big Data — two of the three Vs

**Volume — demonstrated.** 8.44M bronze rows through a distributed Spark pipeline;
1,746,601 narrative chunks; 989,042 exposure rows; 118,323 in Lakebase. Well past the 1M
threshold and processed, not merely stored.

**Variety — demonstrated, with one caveat.** Free-text complaint narratives are chunked,
embedded, indexed and hybrid-searched (BM25 + vector), and surfaced through the agent's
`search_complaints` tool into the application workflow. `silver_complaint_chunk_indexed`
(1.75M chunks) is in Unity Catalog now; **the AI Search index itself is deleted** to cap
billing, so the retrieval half cannot be demonstrated live until it is rebuilt (~7 h, I-105).

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
| Screenshots / demo transcripts | `scripts/capture_screenshots.py` produces 20 images + a `manifest.json` into the gitignored `docs/screenshots/`; `docs/DEMO.md` is the guided walkthrough. **Generate these into the submission zip** — they are not in the repo |

## Deliberately still missing

Named so the gap is bounded rather than discovered.

| Gap | Why | Cost to close |
|---|---|---|
| **Assistant screenshot / agent transcript** | Needs the serving endpoint restored *and* the AI Search index rebuilt — `search_complaints` is the agent's primary retrieval tool and fails first without it | ~3 min + ~7 h, with a demonstrated risk of a mid-sync restart |
| **Live App running** | Stopped to avoid idle billing | ~2 min, self-service for all three reviewers |
| **App serving current code** | Shipping restarts the App under whoever is using it, so it is deliberately manual (I-097) | one command, after the agent work |
| **CD on merge** | Needs an account-level OIDC federation policy this identity cannot create (I-100). The rejected alternative — a PAT in GitHub secrets, reaching a ~296-student metastore — is recorded rather than quietly adopted | blocked on account access, not effort |

## Regenerating this evidence

Nothing here is hand-maintained. Each artefact has a command, and every command is safe to
re-run.

| Artefact | Command | Needs |
|---|---|---|
| **Screenshots** (gitignored) | `scripts/run_local_static_dev.sh 8811` then `.venv/bin/python scripts/capture_screenshots.py` | a local server on live Lakebase; ~1 min |
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
