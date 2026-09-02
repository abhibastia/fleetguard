# FleetGuard — project status

**Last updated:** 2026-09-02 · **MVP target: 7 September** · **Demo: 25–30 September**

> **MVP = one vertical slice working end to end:** recall lands → exposure ranked → human
> approves → work orders written to Lakebase → visible in UC via CDF → visible in a browser.
> Scope, cut order and the explicit *not-in-MVP* list are in
> [`ENHANCEMENTS.md`](ENHANCEMENTS.md#mvp--target-7-september-2026-6-days). Landing MVP on
> the 7th leaves ~18 days to improve a working system rather than finish one.

One page answering "where are we". **Cold session? Read
[Picking this up tomorrow](#picking-this-up-tomorrow) at the bottom first — it is the
next-actions list in priority order.** Design lives in `FleetGuard_Proposal.md`, sequencing in
`../PLAN.md`, and every problem hit during the build in `ISSUES.md`.

---

## Phase status

| Phase | Status | Detail |
|---|---|---|
| **1 — Ingestion + bronze/silver/gold** | ✅ **DONE** | Ingest job, bronze (4), silver (9) built and validated. Chunking done — `silver_complaint_chunk_indexed`, 1,746,601 chunks. **`gold_emerging_cluster` is descoped, not outstanding** (checked 2026-09-02): it was cluster-grained and there is no clustering — HDBSCAN abandoned (I-048), semantic subdivision falsified (I-049), and the shipping detector keys on `make|model|comp_top`. Replaced by **`gold_emerging_signal`**, the same rule applied to the current corpus. Ingest is **deliberately manual** — no schedule, to avoid consuming shared-workspace compute before it's needed. |
| **2 — Fleet registry** | ✅ **Done** | 20,000 vehicles / 60 depots / ~989k exposure rows. 400 VINs independently vPIC-verified, 400/400 exact. |
| **3 — Chunking + AI Search** | ✅ **DONE** | Index complete: **1,746,601 chunks, `ready: true`**, matching source exactly. Done-when **re-verified at full corpus** — hybrid differs from ANN on 2 of 3 queries, harm filter 10/10, near-duplicates 10/10 distinct. The earlier check ran at 42% and was repeated before being quoted. |
| **4 — Model B + golden set** | ⬜ Not started | Scope now measured: variant matches outnumber exact 3:1 (I-030). |
| **5 — Lakebase + CDF** | ✅ **DONE** | 11 tables, all `REPLICA IDENTITY FULL`; **all 11 CDF history tables exist with exact names, no `_1` suffixes** (I-044 — CDF replicates DDL, correcting an earlier wrong claim). Reference data loaded: depot 60, vehicle 20,000, campaigns 592. **Capture latency measured: 7.1–15.6 s** (I-046). **Exposure loaded** — `EXACT` scope, 263,686 rows deduplicated to **118,323** distinct (vin, campaign) pairs. |
| **6 — OAuth wiring** | ✅ **DONE — with a login** | Auth seam (E-13) tested on both surfaces, 401 on failure, never an SP fallback. **U2M retired (E-14)** — needs an account-admin OAuth registration we do not have, and OBO on Databricks Apps is stronger with less setup. **Render now has a working sign-in (2026-09-02): GitHub OAuth, `app-login` mode, two-tier authorization (read for anyone signed in; approve only for `FLEETGUARD_APPROVERS`).** Verified end to end in a browser. |
| **7 — Agent tools + write path** | ✅ **DONE** | Write path end to end: `POST /campaigns/{id}/service-campaign` → 1 service campaign + N work orders + audit row in **one transaction** → CDF → UC. Agent: `ResponsesAgent`, **4 tools** (complaint search · fleet exposure · **emerging signals** · propose campaign), MLflow tracing, smoke tests pass against live index + warehouse. **The agent proposes, never launches** — asserted in the build, so a change that lets it self-launch fails. **Logged, validated, registered and DEPLOYED** 2026-09-02: endpoint `agents_bootcamp_students-fleetguard-fleetguard_agent`, inference tables on (`fleetguard_agent_payload`). Console chat panel wired (`POST /api/chat`, caller's token, non-streaming). **The first deployed version answered a 25-vehicle recall with "no vehicles affected" (I-050)** — undeclared table resource plus an unchecked statement status. **Fixed in version 2, verified live:** returns 25 vehicles / 22 depots / EXACT with the tier stated, and a nonexistent campaign returns a distinguishable "the lookup ran and found zero". |
| **8 — App + external surface** | 🟡 **Public surface live** | FastAPI serves `/api/*` **and** the built React console from one service — no CORS, SPA deep-link fallback. Four views: queue (+ assistant panel), campaign approval, **Emerging signals**, evidence. Verified end to end against live Lakebase. **Deployed to Render 2026-09-02** — https://fleetguard-console-abhi.onrender.com, chat panel included. Every `/api` route there returns 401 by design (no sign-in; U2M retired), so the panel renders an explanation rather than an error. **Signed-in console live on Render** over a committed snapshot — the host can hold no Databricks credential (SP creation admin-only, PATs disabled, Lakebase auth is OAuth-only; all measured). Anonymous visitors land on Evidence, not a login wall (I-057). **Outstanding:** the Databricks App (~20 Sept), where OBO supplies a real Databricks identity and the data goes live. |
| **9 — Model A + backtest** | ✅ **DONE — result is negative** | **The semantic hypothesis is falsified (I-049).** Subdivision *lowered* detection 13.3% → 11.2%, left lift flat (1.24× → 1.26×), and gave **0.0 days** extra lead on shared detections. Published result stays the volume-anomaly measurement: **16.0% vs 11.1%, 1.44×, p≈0.009**. Done-when explicitly required publishing a possibly-negative number as-is; met. |
| **10 — Governance** | ⬜ Not started | Recommend a visible slice, not the full matrix. |
| **11 — Deployment hardening** | ⬜ Not started | |
| **12 — Second connector** | ❌ **Cut** | Deliberately dropped for schedule. |

---

## What exists in the workspace

**Schema:** `bootcamp_students.fleetguard` (owned by `abhisek.bastia17@gmail.com`, inside a
*shared* bootcamp metastore — never write outside it).
**34 tables** (excluding pipeline materialisations and event logs).

| Layer | Tables | Rows |
|---|---|---|
| bronze (4) | `bronze_complaints` · `bronze_recalls` · `bronze_investigations` · `bronze_tsbs` | 2,240,289 · 244,925 · 154,367 · 5,801,279 |
| silver (10) | `silver_complaint` (+quarantine, +`_chunk`, +`_chunk_indexed`) · `silver_recall` (+q) · `silver_investigation` (+q, +`_case`) · `silver_tsb` (+`_bulletin`) | 2,209,123 · **1,746,601 chunks** · 244,701 · 154,191 · 5,801,279 |
| gold — fleet (3) | `gold_fleet_vehicle` · `gold_fleet_depot` · `gold_fleet_exposure` | 20,000 · 60 · 989,042 |
| gold — backtest (6) | `gold_lead_time_backtest` · `_control` · `_summary` · `gold_backtest_scope` · `_complaint` · `_embedding` | 777 · 67 · 2 · 6,649 · 205,219 · **205,219 vectors** |
| gold — signals (1) | `gold_emerging_signal` — live detector output, same rule as the backtest | **48** (9 live · 2 fleet-relevant) |
| ops (7) | `ops_ingest_watermark` · `ops_recall_poll_state` · `ops_hybrid_query_test` · `ops_lakebase_load` · `ops_cdf_latency` · `ops_psycopg_probe` · `ops_pg_privilege_diagnostic` | measurement + cursor state |
| api (2) | `bronze_recall_api` · `gold_recall_alert` | 2,117 rows / 653 campaigns · 0 alerts (correct — nothing novel) |

**Lakebase** (`databricks_postgres.bootcamp_students`): 11 `fleetguard_*` tables, **139,000+ rows**
(`fleetguard_defect_signal` populated 2026-09-02 — 48 signals; empty since Phase 5 until then)
(vehicle 20,000 · exposure 118,323 · campaign 592 · depot 60 · + service campaigns/work orders/audit).
Per-user identity verified: 25 Databricks identities exist as Postgres login roles, `current_user`
resolves to the caller, and `row_security` is `on`. **CDF** (`bootcamp_students.bootcamp_cdc`): 11 `lb_fleetguard_*_history` tables,
exact names, no collision suffixes.

**Models:** `bootcamp_students.fleetguard.fleetguard_agent` — **3 registered versions**, v3 serving (v1 superseded by I-050's fix, v2 by the signals tool).

**Compute:** 1 pipeline (`fleetguard-bronze-silver`, IDLE) · **19 jobs, all manual** (+`fleetguard-deploy-agent`, `-emerging-signals`, `-load-signals`) ·
1 serverless SQL warehouse · 1 AI Search endpoint.

**APIs integrated:** vPIC (`DecodeVINValuesBatch`, authoritative for make/model/year) ·
recalls (`recallsByVehicle`, 200/200 combos, 100 s sweep) · `static.nhtsa.gov` flat files
(`If-Modified-Since`, verified 304).

⚠️ **Now billing — two things:**
1. AI Search endpoint `fleetguard-vs` (STANDARD, 1 unit) — **~$6.72/day**, started 2026-08-31.
2. Model Serving endpoint `agents_bootcamp_students-fleetguard-fleetguard_agent` (Small CPU),
   started 2026-09-02. **`scale_to_zero_enabled` is `False`** — `agents.deploy()` did not
   enable it — so this bills continuously, not per query. Enabling scale-to-zero would trade
   idle cost for a cold start on the first demo question.
   **The rate cannot be self-served from this workspace:** `system.billing` requires
   `USE SCHEMA`, which a non-admin on a shared metastore does not have, and the public
   pricing pages publish GPU serving DBU rates only — there is no CPU workload-size table.
   Get the grant, use the pricing calculator, or read the bill; do not quote an estimate.
   **Redeploying does not retire the old version.** `agents.deploy()` of v2 left v1
   `DEPLOYMENT_READY` at 0% traffic — two containers billing for one agent. Removed
   2026-09-02 via `serving-endpoints update-config`; endpoint re-verified afterwards
   (25 vehicles / 22 depots / EXACT). Check for this after every redeploy.

Nothing else recurs: no Lakebase tables, no schedules.
Stop it with `databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_idx`
then `databricks vector-search-endpoints delete-endpoint fleetguard-vs` — billing ends 24h
after the last index is deleted.

---

## Measured results

Everything here is measured against live data, not estimated. Full derivations in
`ISSUES.md`.

### Lead-time backtest — the differentiator

The claim is *complaint accumulation → ODI investigation opens*. Volume-anomaly detector
**with no harm weighting** — **this is the final Model A**, not an interim half. (The harm term was designed and documented but never built; corrected 2026-09-02, I-051. The measured result is unaffected — it was always produced by pure volume anomaly.)

| Arm | n | Detected | Rate | Median lead |
|---|---:|---:|---:|---:|
| **Real** (investigated series) | 777 | 124 | **16.0%** | **197 days** |
| **Placebo** (volume-matched control) | 606 | 67 | 11.1% | 343 days |

Two-proportion z ≈ 2.62, **p ≈ 0.009** — statistically real, practically modest (1.44×
lift). A secondary signal is stronger than the headline: real detections cluster near the
open date while control detections scatter toward the window midpoint, which is the shape a
detector tracking a genuine ramp produces.

~~**Implication:** volume anomaly alone does not carry the differentiator. The semantic half
(Phase 3 → 9) is load-bearing, not an enhancement.~~

**SUPERSEDED 2026-09-01 by the experiment it motivated (I-049).** The semantic arm was
built and measured: it *lowered* detection to 11.2% and added **0.0 days** of lead on shared
detections. Volume anomaly does not merely carry the differentiator — it **is** the
differentiator. The figures above are the published result, not a floor awaiting
improvement.

### Corpus

- Complaints **2,240,289** (`LDATE` 1995-01-01 → 2026-08-27) · recalls **244,925** rows /
  **15,211** campaigns · investigations **154,367** rows but **5,344 distinct** · TSBs
  **5,801,279** rows but **258,438 distinct bulletins**.
- Backtest population: **777** post-2010 investigations, **497** with ≥30 prior-year complaints.
- Park It (`DO_NOT_DRIVE`): **211 of 15,211** campaigns; **zero for 2010–2011** — demo from 2015+.
- Harm fields never null; among harm-alleging complaints, police report 39.0%, medical 12.2%.

### Fleet

20,000 vehicles, 47 models, GVWR Class 1D → Class 8. Mix 45% pickup / 40% van / 15% heavy.
Exposure: **EXACT 263,686** rows vs **MODEL_VARIANT 725,356** — variants outnumber exact
~3:1. All 2,116 F-250s match *only* as variants (`F-250 SD` is NHTSA's dominant spelling).

### Cost, sized but not yet incurred

| Item | Cost |
|---|---|
| Embedding ~275M tokens (one-off) | **$28–36** |
| AI Search endpoint (**recurring**) | **~$403/month** (2 standard units) |

Recurring dominates one-off by >10×. Billing stops 24h after the last index is deleted, so
index lifecycle — not corpus trimming — is the lever.

---

## Testing

Three layers, doing different jobs. Full rationale in `src/pipelines/expectations/README.md`.

| Layer | What | Run |
|---|---|---|
| **Unit** (`tests/test_vin.py`, `test_chunking.py`, `test_naming.py`) | Pure logic, no Databricks. **91 tests.** | `pytest` |
| **LDP expectations** (`src/pipelines/**/*.sql`) | Row-level, in-pipeline. Post-routing invariants + explicit `_dq_failures` quarantine split. | runs with the pipeline |
| **Data quality** (`tests/test_data_quality.py`) | Cross-table invariants against live tables. **21 tests, 73 s.** | `pytest -m integration --run-integration` |

Logic that has already been wrong once is extracted into `src/fleetguard/` so it is
testable off platform, and the bugs are encoded as **regressions**:

- `TestRegressionI034` — the single-character-chunk bug (stride vs window)
- `test_transliteration_is_many_to_one` — a VIN checksum property the suite *discovered*:
  `A`/`J`/`1` share a transliterated value, so some substitutions are invisible to the
  check digit. My original test asserted the opposite and failed.
- `test_complaint_parse_is_correct_by_cardinality_not_by_rescued_data` — the check that
  would have caught I-012, where `_rescued_data` read 0 while 143 rows were mis-parsed.
- `test_nothing_is_dropped_silently` — asserts `bronze = silver + quarantine` exactly.
- `test_no_detection_leaks_past_the_open_date` — a detection dated on or after the
  investigation opened is leakage, not lead time.

**The rule:** a check that cannot fail is not a check. Every assertion here has either
failed during the build or exists because something adjacent to it failed silently.

---

## Open decisions blocking progress

| # | Decision | Blocks |
|---|---|---|
| ~~NEW~~ | ~~**Exposure load scope.**~~ **Resolved 2026-09-01 — `EXACT`-only**, 263,686 source rows deduplicated to 118,323 distinct (vin, campaign). Deduplication was mandatory: raw loading would have inflated the queue 2.2×. | — |
| ~~NEW~~ | ~~**Agent endpoint lifecycle.**~~ **Decided 2026-09-02: keep it running.** Detail retained below for when it is revisited. `agents_bootcamp_students-fleetguard-fleetguard_agent` runs with `scale_to_zero_enabled: False`, so it bills continuously. Three options: leave hot to the demo, enable scale-to-zero (idle cost → cold start on the first demo question), or delete it and redeploy nearer the 25th (version 2 stays registered; redeploy is one job run). **The rate cannot be measured from this workspace** — `system.billing` needs `USE SCHEMA` we do not have, and the public pricing pages publish GPU rates only. | Cost |
| **NEW** | **Should the public chat panel answer?** `/api/chat` uses the caller's token, so it 401s on Render. Making it work needs a service identity there — an **amendment** to §8a's "no PAT or SP on Render", not an exception. That rule's stated reason is the write path and `/api/chat` has none, but it would expose workspace-billed LLM inference and complaint retrieval to anyone with the URL. | Demo polish only |
| **I-018** | **Index lifecycle.** How long to leave the AI Search endpoint up: ~$6.72/day, ~23 days to demo ⇒ ~$155 if left running throughout. No longer the *only* recurring cost — the agent serving endpoint now runs alongside it. | Cost |
| ~~I-015~~ | ~~**Streaming vs PII guardrail.**~~ **Resolved by choosing not to stream.** `/api/chat` is non-streaming, so the §4.5 output guardrail claim stays available. Cost: answers appear all at once after a few seconds. | — |
| ~~I-026~~ | ~~Chunking scope.~~ **Resolved by measurement** — the full-corpus hybrid test returned 10/10 distinct `complaint_id`, so the near-1:1 chunk table causes no near-duplicate retrieval problem and needs no read-time dedupe. | — |

---

## Risks, honestly

1. ~~**Phase 5 is completely untested.**~~ **FULLY RETIRED 2026-09-01** — schema, load, CDF
   replication and capture latency are all measured. This was the project's largest risk.
2. ~~**The differentiator is still the risk, and the verdict is imminent.**~~ **RESOLVED
   2026-09-01 — and the answer is no.** The semantic arm was built and measured; it makes
   detection *worse* and adds no lead time (I-049). The fallback is now the position: the
   differentiator is the **measured 16.0% vs 11.1% volume-anomaly result with a control arm
   (1.44×, p≈0.009)**. This is a genuine risk *retired*, not deferred — the number is known,
   defensible, and will not move between now and the demo.

   **The remaining risk is presentational, not technical.** The result is modest, so the
   demo has to sell *rigour* — a control arm, a falsified hypothesis, a published negative —
   rather than a big number. That is a stronger story than an unfalsifiable 10×, but it has
   to be told deliberately.
3. **Scope vs schedule — now the top schedule risk.** Phases 4, 6, 7, 8, 10, 11 are all
   unstarted with **24 days** left, and 6/7/8 (the demo surface) are a dependent chain.
   Phases 3 and 5 are done, so nothing is blocked *technically* — the constraint is purely
   build time. Cut list already agreed: Phase 12 (done), Feature Store online serving,
   Genie Agent, Unity AI Gateway, governance reduced to a visible slice.
4. **A pattern worth naming: the tooling lies about success.** Three distinct variants in
   one day — a watcher exiting `0` at 51% (I-043), `jobs run-now` returning `0` for a
   `FAILED` run, and the CLI reporting `Error: timed out` while the job ran on healthily.
   **Only `state.result_state` describes the job.** Every verification in this project reads
   the live resource, never the client's exit code.

---

## Document map

Each document has exactly one job and a stated lifecycle. A frozen doc that gets edited
loses its integrity; a living doc that doesn't get edited becomes a lie.

| File | Purpose | Lifecycle |
|---|---|---|
| **`ARCHITECTURE.md`** | **What the system is — the living spec** | Living; update with the code |
| `ENHANCEMENTS.md` | Evaluated backlog — adopt / defer / reject, each with a reason | Living |
| `FleetGuard_Proposal.md` | What was *proposed*, before the build | **FROZEN** 2026-08-31 |
| `STATUS.md` | This page — where the build has got to | Living, high-churn |
| `ISSUES.md` | Every problem hit, root cause, resolution. **Silent failures flagged.** | Append-only |
| `../PLAN.md` | Phase sequencing and definitions of done | Living |
| `../CLAUDE.md` | Verified facts that must not be re-derived | Living |
| `fleetguard_e2e.html` / `fleetguard_identity.html` | Diagrams (editable, diffable) | Living |

The proposal is **not** updated to match findings. Its header tabulates the known
contradictions with measured results — that gap is the record of what the build taught us,
and erasing it would destroy the only evidence of what was believed at the outset.

---

## Where we are — 2 Sep

**The agent is deployed, working, and was wrong the first time.**

Logged models-from-code, round-trip validated, registered as
`bootcamp_students.fleetguard.fleetguard_agent`, deployed to
`agents_bootcamp_students-fleetguard-fleetguard_agent` (Small CPU, READY, inference tables
on via `fleetguard_agent_payload`). Deploy lives in its **own** notebook
(`src/agent/15_deploy_agent.py`) so no build re-run can create billing compute as a side
effect.

**I-050 — the finding of the day.** Version 1 answered *"which fleet vehicles does recall
17V629000 affect?"* with **"no fleet vehicles matched."** Ground truth: **25 vehicles across
22 depots.** Two faults, and it needed both: `resources` declared the SQL warehouse but not
the **table** (automatic auth passthrough grants only what is declared, and engine and data
are separate grants), and `execute_statement` **does not raise on failure** — it returns
`status.state = FAILED` with `result = None`, which the tool read as an empty list, which
became "you are not affected". The build passed green throughout, because the smoke test
asserted only that a call returned.

The first hypothesis — cold-warehouse timeout — was **tested and falsified** before fixing
anything. Version 2 now returns 25/22/EXACT with the tier stated, and a nonexistent campaign
returns a *distinguishable* "the lookup ran and returned zero". Smoke tests now pin the
numbers, not the absence of an exception.

**Two platform surprises, both cost-relevant:**
- `agents.deploy()` set `scale_to_zero_enabled: False` — the endpoint bills continuously.
- Redeploying **does not retire the old version**: v1 stayed `DEPLOYMENT_READY` at 0% traffic,
  two containers billing for one agent. Removed with `serving-endpoints update-config`
  (which *replaces* `served_entities` — the surviving entity's `environment_vars`, including
  `MLFLOW_EXPERIMENT_ID`, must be copied verbatim or tracing silently misfiles). Endpoint
  re-verified afterwards. **Check `served_entities` after every redeploy** — `traffic_config`
  looks perfectly correct while the old container keeps running.

**Console.** Agent chat panel wired beside the queue (E-11) — `POST /api/chat`, the
**caller's** token, non-streaming (I-015). Verified locally end to end against the live
endpoint. Deployed to Render: https://fleetguard-console-abhi.onrender.com. `/api` there
401s by design, so the panel explains itself instead of erroring.

**Evidence page stopped hardcoding its numbers.** `GET /api/evidence` serves a snapshot
generated by `scripts/export_evidence.py` from `gold_lead_time_summary`, with lift and the
two-proportion z **recomputed from the arm counts** — reproducing 1.44× / z 2.62 / p 0.0087
exactly. A snapshot rather than a live query because this is the *public* surface and a
request-time read would need a Databricks credential on a public host (§8a). The route is
deliberately unauthenticated; tests pin the asymmetry in both directions.

**The proactive half now exists, and Phase 1 closed with it.** `gold_emerging_cluster` was
checked and **descoped** — cluster-grained for a detector that does not cluster. Replaced by
`gold_emerging_signal`: the *same rule, same thresholds* as the measured backtest, applied to
the current corpus. **48 signals in 12 months, 9 still firing, 2 fleet-relevant** — RAM 2500
service brakes (z 5.62, 64 complaints, **1,256 fleet vehicles**) and Toyota Tundra speed
control (232). Loaded into `fleetguard_defect_signal`, which had been empty since Phase 5,
and surfaced as an **Emerging** tab headlining "N across NHTSA, M affecting your fleet".

**I-051 — the living spec described harm weighting that does not exist.** §5 credited Model A
with a smoothed severity multiplier; there is no harm term in the code. The measured
16.0%/11.1% is pure volume anomaly. Corrected in `ARCHITECTURE.md` and `STATUS.md`.
`harm_share` ships as explicitly *descriptive* triage, never a detection input. The lesson is
new in kind: a living spec accumulates **intentions that read as descriptions**, and no unit
test can see the difference — the check is `grep`, not plausibility.

Runbook for all of it is in the Obsidian vault
(*Databricks — Deploying an MLflow ResponsesAgent*), CLI and UI paths both.

---

## Where we are — 1 Sep

**AI Search index: COMPLETE.** `ready: true`, **1,746,601 of 1,746,601** chunks (100%),
matching `silver_complaint_chunk_indexed` exactly. ~7 h end to end.

**Phase 3 done-when re-confirmed at full corpus** (`fleetguard-hybrid-query-test`, results
in `ops_hybrid_query_test`). The earlier verdict was taken on a 42%-built index, so it had
to be repeated before it could be quoted:

| Query | ANN vs HYBRID |
|---|---|
| component code + symptom | **differs** — hybrid surfaces `FOUNDATION COMPONENTS:HOSES, LINES/PIPING` |
| pure paraphrase | **differs** — hybrid finds `ENGINE`/`VEHICLE SPEED CONTROL`; ANN drifts to `AIR BAGS` |
| `TAKATA airbag inflator` | identical — both saturate on `AIR BAGS` |

Harm filter **10/10 PASS**. Near-duplicate audit **10/10 distinct `complaint_id`** — the
sibling-chunk concern (I-023) does not materialise at full corpus, so the agent's search
tool does not need read-time dedupe after all.

**Billing:** `fleetguard-vs` is the only recurring cost, ~$6.72/day. Everything else is
manual-trigger. Stop it by deleting the index then the endpoint.

---

## Phase 5 — Lakebase loaded and CDF measured (1 Sep)

**Reference tables loaded**, each reconciling exactly against source:

| table | rows | load |
|---|---|---|
| `fleetguard_depot` | 60 | 0.0 s |
| `fleetguard_vehicle` | 20,000 | 0.5 s (~40k rows/s) |
| `fleetguard_recall_campaign` | 592 | 0.0 s |

**CDF replicated all of it.** Depot's history reconciles exactly to the I-038 test —
61 insert / 60 `update_preimage` / 60 `update_postimage` / 1 delete, which is the original
60 inserts plus 59 upsert-conflicts plus the one previously-deleted row re-inserted. That
confirms `ON CONFLICT DO UPDATE` **and** `REPLICA IDENTITY FULL` in one arithmetic check.

**All 11 history tables exist with exact names, no `_1` suffixes** — the naming risk
(I-036) is fully retired across every table, not just the one round-tripped in I-038.

**CDF capture latency MEASURED** (`ops_cdf_latency`, 3/3 true measurements):
**7.1 – 15.6 s, mean 12.5 s.** State it as a *range consistent with a ~15 s flush*, never
as one averaged number; size demos against the **15.6 s worst case**. Comfortably supports
§8.3's sub-minute claim. See I-046 for why the first attempt (21.55 s) was an upper bound
and not a measurement.

---

## Phase 9 — semantic arm: DONE, result is NEGATIVE (1 Sep)

**The hypothesis is falsified.** Grouping complaints by *what they describe* rather than by
NHTSA's component code does **not** surface defect ramps earlier. Both groupings recomputed
on the identical 37-month working set:

| grouping | arm | detected | rate | median lead |
|---|---|---|---|---|
| v2 component | REAL | 103/777 | **13.3%** | 240 d |
| v2 component | PLACEBO | 65/606 | 10.7% | 360 d |
| v3 semantic | REAL | 87/777 | **11.2%** | 202 d |
| v3 semantic | PLACEBO | 54/606 | 8.9% | 273 d |

Lift **1.24× → 1.26×** — unchanged. Detection **fell**.

**The decisive number** is in the paired view: on the **70** investigations both groupings
detect, subdivision produced **0.0 days** of extra lead time (REAL: 70 both, 33 v2-only,
17 v3-only).

Had the mechanism worked and merely been outweighed by fragmentation, those shared
detections would still fire *earlier*. They do not. Subdivision fired on **fewer things,
not the same things sooner** — so this is a falsified mechanism, not an under-tuned one.

**Read the 13.3% correctly.** That is v2 **recomputed on the restricted 37-month embedded
set**, existing only to be a like-for-like comparator for v3. The headline result remains
the full-corpus measurement below. Never quote the 13.3/11.2 pair as the project's result.

### Published result — the differentiator

> **16.0% detection at a median 197-day lead, against 11.1% on a volume-matched placebo.**
> **1.44×, z ≈ 2.62, p ≈ 0.009.** Volume-anomaly detector, full silver corpus.

Modest, real, falsifiable, defended by a control arm — and now also defended by a
*published negative* on the obvious "just add embeddings" improvement.

**Phase 3 is not invalidated.** Hybrid retrieval is verified and load-bearing for the
agent's search tool (§4.3). Retrieval and clustering are different uses of the same
embeddings; only the clustering claim is retired.

**What it cost:** ~$5 of embeddings, one abandoned 85-minute HDBSCAN run (I-048), about
half a session. Cheap for closing the project's central open question 24 days out rather
than discovering it mid-demo.

---

## Picking this up tomorrow

**MVP is complete, five days early.** The vertical slice runs end to end — recall lands →
exposure ranked → human approves → work orders → CDF → UC → browser — the agent is deployed
and verified, the proactive half exists, and the evidence page serves generated figures. The
7 September target is met; everything below is *improving a working system*, which was the
point of landing early.

Every item on yesterday's list is closed: the endpoint decision (keep), `/evidence` (built),
and the proposal corrections (already discharged by the freeze header — the item should never
have been on the list).

**Also closed today:** the agent's signals tool. Version 3 is deployed and verified —
`lookup_emerging_signals` returns the counts alongside the rows, so "2 affecting your fleet"
cannot be read as "2 recalls", and the prompt carries the **three-state distinction**
(signal / investigation / recall). The live endpoint leads with it unprompted: *"these are
emerging signals … not recalls, not open investigations, not confirmed defects … a modest
edge, not an oracle."* v2 dropped after verification (I-052); one entity billing, not two.

### Next, in priority order

1. **Databricks App (~20 Sept).** Still the one thing that makes queue, assistant and
   Emerging live for a real user, via OBO. The auth seam means it is an afternoon. Keep it
   `STOPPED` between sessions — `apps create` provisions billing compute on *create*.
2. **Re-run the evaluation with the corrected scorers.** E-05 is built and has already
   earned its keep — but the first run's failures were all in the *measuring apparatus*
   (I-058), so the agent's real score is unknown beyond the six scorers that were clean.
   Budget ~90 min: 10 cases × (agent tool rounds + LLM judges) is slow and the task timeout
   is set accordingly. Then read it with `17_inspect_eval`, never from the state message.
3. **E-05 evaluation scorers, now with three concrete targets.** `Guidelines` scorers for:
   never call an investigation a recall; always state the match tier; **never report a tool
   failure as a business answer** (I-050). The third only exists as a scorer idea because the
   failure actually happened.
4. **Refresh discipline for the two snapshots.** `gold_emerging_signal` and
   `evidence.json` are both point-in-time and both regenerate by hand
   (`fleetguard-emerging-signals` + `fleetguard-load-signals`; `scripts/export_evidence.py`).
   Decide before the demo whether to refresh them on the day — the signals table is dated
   `as_of 2026-08` and a reviewer will notice.
5. **Phase 4 Model B** — scores the `MODEL_VARIANT` residual (3:1 over exact, I-030). The
   largest remaining *capability* gap; everything else on this list is polish.
6. **Phase 10 governance** — a visible slice (Postgres RLS on depot scoping, making §5.1
   literally true), not the full matrix.

### The public deployment, as it now stands

**https://fleetguard-console-abhi.onrender.com** — `auth_mode: app-login`,
`data_mode: snapshot`. Anonymous: Evidence only. Signed in with GitHub: queue (60 campaigns),
Emerging (48 signals, 2 fleet-relevant), campaign detail. Approval returns **501** by design —
the surface has no credential to write with, and simulating the write would be worse than
refusing. Refresh the snapshot with `scripts/export_demo_snapshot.py --profile abhi`.

Three env vars live only in the Render dashboard, never committed: `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, `FLEETGUARD_APPROVERS`.

### Things that are true and easy to forget

- **The endpoint bills continuously** (`scale_to_zero_enabled: False`), serving **version 3**,
  one entity. Kept up by decision 2026-09-02. To retire it: delete the endpoint; to bring it
  back, `fleetguard-deploy-agent` (job `602170435434673`) with `model_version=3`.
- **`/` on Render is cached** — it served a stale `index.html` for minutes after a successful
  deploy (I-054). Probe an API route to confirm a deploy, not the console page. A 401 from a
  gated route proves it exists; an unknown path returns **200 HTML** via the SPA catch-all.
- **Lakebase notebooks need the `fgenv` serverless environment**, not `%pip` (I-053) — the
  dependency list lives in the *job*, not the notebook, so cloning a working notebook without
  its job spec produces code that cannot run.
- **After every agent redeploy, read `served_entities`, not `traffic_config`** (I-052) — the
  old version stays provisioned and billing while routing looks perfectly correct. Build the
  `update-config` payload by reading the surviving entity's live config **programmatically**;
  hand-copying is how `MLFLOW_EXPERIMENT_ID` gets dropped.
- **An evaluation harness needs its own tests** (I-058) — it is code that judges code, and a
  naive substring check punished the agent for *promising not to* invent a recall. Six of
  eight scorers were perfect; every failure was in the measurement.
- **A login page on a fresh `*.onrender.com` subdomain trips Google Safe Browsing** (I-057) —
  verify integrity by byte-comparing the served bundle before assuming a false positive, then
  remove the signature by landing anonymous visitors on content rather than a form.
- **`StatementParameterListItem` binds values as STRING** (I-056) — fine for `= :id`, rejected
  for `LIMIT :n`. Coerce to a bounded int and interpolate; after `int()` it cannot carry SQL.

### Verification discipline — earned nine times (I-021, I-043, I-050, I-051, I-054, I-055, I-056, I-057, I-058)

Never infer success from an exit code. Never infer correctness from the absence of an
exception. **Assert a number.** And for documents: a living spec accumulates *intentions that
read as descriptions* — when it says the system does X, `grep` for X rather than judging
whether it sounds right.

Corollary, now enforced in the agent: **any tool an LLM can call must distinguish "I looked
and found nothing" from "I could not look."**

That guard paid for itself within hours. The signals tool's first build **failed** on a
string-vs-int `LIMIT` bug (I-056) — nothing to do with permissions. Under the old code it
would have returned an empty list and had the agent report *"no emerging defects affect your
fleet"*: the same false all-clear as I-050, in the newest tool, the same day the fix landed.
**A rule that turns silent failures loud catches classes of bug you did not anticipate** —
which is the argument for adding them even after the specific known failure is fixed.

**Do not** rebuild the AI Search index inside the demo window — it is most of a working day.
And don't add `-o json` to `vector-search-indexes get-index`; it breaks output that is
already JSON.
