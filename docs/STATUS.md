# FleetGuard — project status

**Last updated:** 2026-09-08 · **MVP target: 7 September — MET** · **Demo: 25–30 September**

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
| **4 — Model B + golden set** | ✅ **DONE** | 765-pair golden set from NHTSA's own recall text (real, not synthetic, E-08). Precision **83.7%**, recall **96.3%**, ROC-AUC 0.925 — published on the evidence page. Caught and fixed its own training-feature leakage before reporting (I-060). |
| **5 — Lakebase + CDF** | ✅ **DONE** | 11 tables, all `REPLICA IDENTITY FULL`; **all 11 CDF history tables exist with exact names, no `_1` suffixes** (I-044 — CDF replicates DDL, correcting an earlier wrong claim). Reference data loaded: depot 60, vehicle 20,000, campaigns 592. **Capture latency measured: 7.1–15.6 s** (I-046). **Exposure loaded** — `EXACT` scope, 263,686 rows deduplicated to **118,323** distinct (vin, campaign) pairs. |
| **6 — OAuth wiring** | ✅ **DONE — with a login** | Auth seam (E-13) tested on both surfaces, 401 on failure, never an SP fallback. **U2M retired (E-14)** — needs an account-admin OAuth registration we do not have, and OBO on Databricks Apps is stronger with less setup. **Render now has a working sign-in (2026-09-02): GitHub OAuth, `app-login` mode, two-tier authorization (read for anyone signed in; approve only for `FLEETGUARD_APPROVERS`).** Verified end to end in a browser. **U2M code built and flipped live 2026-09-03** (`auth/databricks_oauth.py` + `routers/databricks_auth_routes.py`) — the account-admin OAuth registration landed the same day, closing E-14's blocker, so `render.yaml` on `main` now runs `FLEETGUARD_AUTH_MODE=render-u2m` instead of `app-login`. **The judges have Databricks identities in this shared workspace**, so this is the strong path for them: sign in as themselves, UC/Postgres enforce for real, not simulated. Found and closed the same day, before flipping: the approver allowlist was silently skipped for any real-Databricks-token principal, which would have let any workspace identity (judges included) approve, not just view — `approval.py`'s gate is now unconditional on `FLEETGUARD_APPROVERS`, tested across all four principal sources (`tests/test_approval_gate.py`). **Not yet confirmed:** a real browser completing the login round-trip live — see "Next" item 0. |
| **7 — Agent tools + write path** | ✅ **DONE** | Write path end to end: `POST /campaigns/{id}/service-campaign` → 1 service campaign + N work orders + audit row in **one transaction** → CDF → UC. Agent: `ResponsesAgent`, **6 tools** (complaint search · fleet exposure · **fleet models** · **emerging signals** · propose campaign · **open defect signal — the write**), MLflow tracing, smoke tests pass against live index + warehouse. *(The sixth, `lookup_fleet_models`, is written and live-verified but **not yet deployed** — the endpoint still serves v5; see "Next" item 0.)* **The agent proposes, never launches** — asserted in the build, so a change that lets it self-launch fails. **The agent now performs a real business write (v5, 2026-09-08):** `open_defect_signal` returns an action envelope; the FastAPI app validates it and executes the insert under the **caller's own OBO token** (the serving endpoint has no Postgres path, and all three routes to giving it one are closed on this account). Verified live from the browser: question → complaint retrieval → agent decision → Lakebase insert + audit row + the first-ever `fleetguard_agent_action` row in one transaction → CDF → UC → Emerging tab. See `ARCHITECTURE.md` §7.1. **Logged, validated, registered and DEPLOYED** 2026-09-02: endpoint `agents_bootcamp_students-fleetguard-fleetguard_agent`, inference tables on (`fleetguard_agent_payload`). Console chat panel wired (`POST /api/chat`, caller's token, non-streaming). **The first deployed version answered a 25-vehicle recall with "no vehicles affected" (I-050)** — undeclared table resource plus an unchecked statement status. **Fixed in version 2, verified live:** returns 25 vehicles / 22 depots / EXACT with the tier stated, and a nonexistent campaign returns a distinguishable "the lookup ran and found zero". |
| **8 — App + external surface** | 🟡 **Public surface live** | FastAPI serves `/api/*` **and** the built React console from one service — no CORS, SPA deep-link fallback. Four views: queue (+ assistant panel), campaign approval, **Emerging signals**, evidence. Verified end to end against live Lakebase. **Deployed to Render 2026-09-02** — https://fleetguard-console-abhi.onrender.com, chat panel included. **End-to-end review + visual redesign, 2026-09-03** — 8 bugs found and fixed, 49 new tests, console restyled (same colour-rationing rule, more depth/craft); verified against a live headless-browser check of both the local build and the redeployed Render site. **Auth mode flipped 2026-09-03** from `app-login`+snapshot to `render-u2m`+live Lakebase (E-14's account-admin blocker resolved) — the host now holds real per-user Databricks credentials via U2M OAuth rather than none at all. Anonymous visitors still land on Evidence, not a login wall (I-057) — that route is unauthenticated regardless of mode. **THE DATABRICKS APP IS BUILT, DEPLOYED AND VERIFIED IN A REAL BROWSER — 2026-09-08.** `fleetguard-console`, url `https://fleetguard-console-1352785079224954.aws.databricksapps.com`, **currently STOPPED** (brought up to fix I-086, stopped again once verified; `databricks apps start fleetguard-console`, ~2 min, to bring it back). Deployed from `app/backend/` with `app/backend/app.yaml`; the console bundle is committed so no Node build runs on the Apps runtime. **Seven routes verified under a programmatic OBO token that morning** — `/api/me` resolved the caller with `token_source: databricks-apps`, then queue 50 · signals 50 (9 live, 4 fleet-relevant) · service-campaigns 1 · depot-risk 60 · work-orders 25 · evidence 1.44×/z 2.62 — matching the local surface exactly. **That evening the first real *browser* sign-in failed on every Lakebase route** with `403 Forbidden — Invalid scope, required scopes: postgres`, surviving restart, sign-out/sign-in and an explicit re-application of `user_api_scopes`. **Root-caused and FIXED the same evening (I-086):** OBO scope lives in **three** planes, not two — workspace allowlist, app resource, **and a sticky per-user consent grant** that was captured before I-083's scope fix landed and never widened afterwards. Revoking it via the self-service `DELETE /api/2.0/oauth-app-integrations/<id>/user-consent/me` and re-consenting in a fresh browser session fixed it; `user_consented_scopes` now carries `postgres`/`sql`/`model-serving` and the console works end to end in the browser. **Two corrections came out of this:** it was never a regression (the consent record proves no browser session had ever held `postgres` — the morning pass was programmatic, and "verified live" had not recorded which client produced it), and it never needed account-admin access (`/user-consent/me` is self-service; one `Not Found` on a different object had been generalised into a wall on the whole problem). **The App holds no privileges of its own:** `db.py` mints the Lakebase credential from the *caller's* forwarded token, so there is no `database` or `serving-endpoint` resource on the app and every read runs as the signed-in human, with Postgres RLS applying as it does to a UI click. E-13's auth seam did its job — the move needed configuration plus one line, not a rewrite. Three obstacles, none reproducible off-platform: **I-082** (Apps injects `DATABRICKS_CLIENT_ID`/`SECRET`, so passing the caller's token makes the SDK refuse — needs `auth_type="pat"`), **I-083** (`user_authorization` in `app.yaml` is silently ignored; scopes go on the app resource, **and the app must be restarted** or a granted scope returns the identical 403 as a missing one). **HOSTING DIRECTION CHANGED 2026-09-08 — read this before the Render detail above.** Render is **no longer the assumed demo surface**: near-term verification is the **local browser** against live Lakebase (`scripts/run_local_static_dev.sh`, verified end to end and screenshotted 2026-09-08), and **Databricks Apps is now the primary target (~20 Sept)**, not a second surface alongside Render. The Render deployment and all three of its auth paths (`app-login`, `render-u2m`, the GitHub OAuth wiring) are **deliberately kept and still working** — the cost of keeping them is zero, re-adding them later is not — so everything above remains true, just no longer the plan of record. The `render-u2m` browser login is still unconfirmed live and no longer blocks anything. See Phase 11 for the rescope this triggered. |
| **9 — Model A + backtest** | ✅ **DONE — result is negative** | **The semantic hypothesis is falsified (I-049).** Subdivision *lowered* detection 13.3% → 11.2%, left lift flat (1.24× → 1.26×), and gave **0.0 days** extra lead on shared detections. Published result stays the volume-anomaly measurement: **16.0% vs 11.1%, 1.44×, p≈0.009**. Done-when explicitly required publishing a possibly-negative number as-is; met. |
| **10 — Governance** | ✅ **Visible slice DONE** | Postgres RLS on `fleetguard_vehicle`, `ENABLE`+`FORCE`, proved under real toggled states including the exposure join. Fail-open, nobody enrolled yet — mechanism real, enrollment is future work. Not the full ABAC/DQ-monitor matrix, by design. |
| **11 — Deployment hardening** | 🟡 **Rescoped 2026-09-08** | **Hosting direction changed: Render is no longer the assumed demo surface.** Near-term verification is the **local browser** against live Lakebase (`scripts/run_local_static_dev.sh`); the eventual target is **Databricks Apps**. **Render integration is deliberately KEPT, not removed** — `render.yaml`, the GitHub-OAuth `app-login` path and `render-u2m` all stay in the tree and working, because the cost of keeping them is zero and re-adding them later is not. This removes "Render always-on + pinger" from the phase. **The `table_update` trigger is BUILT** (`fleetguard-cdf-to-gold`, job `851598550157757`, notebook `src/lakebase/21_cdf_to_gold_facts.py`) — the last unbuilt link in the data loop. It derives `gold_agent_action` and `gold_defect_signal_current` from the CDF history tables and reconciles exactly against live Postgres (**2 = 2**, **50 = 50**). **UNPAUSED and verified firing on its own, 2026-09-08** — the project's first non-manual job, and the done-when is now met by observation rather than by construction: a real agent write and a real delete each propagated to UC **with no manual intervention**. Two documented "facts" turned out to be wrong on contact (**I-080**, **I-081**). **Measured commit→gold-fact: 155 s and 269 s (n=2, report as ≈2.5–4.5 min).** Still outstanding: **seeded demo state**. |
| **12 — Second connector** | ❌ **Cut** | Deliberately dropped for schedule. |
| **13 — Commercial-fleet-value roadmap (work orders, cost, audit, depots, trends)** | ✅ **6/8 DONE, 1 deliberately shelved (2026-09-04/05, post-MVP)** | Not in the original 12 phases — grew out of a session UX walkthrough plus a "does this give a commercial fleet real value" analysis. Work-order lifecycle + technician roster; "Launched" campaigns view + table sort/filter; per-work-order actual-cost logging + component/depot breakdown (I-069); audit log made readable; depot risk heatmap (no blended score, real component numbers); recall trend chart (this app's first chart, hand-rolled SVG). All writes gated by `FLEETGUARD_APPROVERS`, fully audited, verified end to end against live Lakebase + CDF, not just API responses. **All merged to `main` and pushed.** **Role-based views was built, verified live, then deliberately not merged** — see "Next" item 0.5 for the full reasoning (little new judging credit for re-proving Phase 10's RLS, zero visible footprint in the default demo state, and a real schema-migration cost to fix a live-discovered gap). Kept on `feature/role-based-views`, available later. Only its local `static-dev` setup script landed on `main` (`c5b3af0`). Remaining, not started: notification digest. |

---

## What exists in the workspace

**Schema:** `bootcamp_students.fleetguard` (owned by `abhisek.bastia17@gmail.com`, inside a
*shared* bootcamp metastore — never write outside it).
**46 objects**, counted 2026-09-08 from `databricks tables list` — *everything* the schema
holds, including the two materialized views, the metric view and the foreign vector index.
The previous figure ("34 tables, excluding pipeline materialisations and event logs") named an
exclusion it never listed, so it could not be reproduced; the table below now sums to the
live count exactly.

| Layer | Tables | Rows |
|---|---|---|
| bronze (4) | `bronze_complaints` · `bronze_recalls` · `bronze_investigations` · `bronze_tsbs` | 2,240,289 · 244,925 · 154,367 · 5,801,279 |
| silver (11) | `silver_complaint` (+quarantine, +`_chunk`, +`_chunk_indexed`) · `silver_recall` (+q) · `silver_investigation` (+q, +`_case`) · `silver_tsb` (+`_bulletin`) | 2,209,123 · **1,746,601 chunks** · 244,701 · 154,191 · 5,801,279 |
| gold — fleet (3) | `gold_fleet_vehicle` · `gold_fleet_depot` · `gold_fleet_exposure` | 20,000 · 60 · 989,042 |
| gold — backtest (10) | `gold_lead_time_backtest` · `_control` · `_summary` · `gold_backtest_scope` · `_complaint` · `_embedding` · **plus the falsified semantic arm** `gold_lead_time_v3` · `_v3_summary` · `gold_backtest_subcluster` · `gold_backtest_cluster` | 777 · 67 · 2 · 6,649 · 205,219 · **205,219 vectors**; the v3/cluster tables back the *published negative* (I-049) and are kept deliberately |
| gold — signals (1) | `gold_emerging_signal` — live detector output, same rule as the backtest | **48** (9 live · 2 fleet-relevant) |
| gold — CDF facts (2) | `gold_agent_action` · `gold_defect_signal_current` — current state derived from the Lakebase CDF history by `fleetguard-cdf-to-gold` | 2 · 50, reconciling exactly with live Postgres |
| gold — model B (1) | `gold_model_b_golden_set` | 765 pairs |
| ops (9) | `ops_ingest_watermark` · `ops_recall_poll_state` · `ops_hybrid_query_test` · `ops_lakebase_load` · `ops_cdf_latency` · `ops_cdf_fact_refresh` · `ops_psycopg_probe` · `ops_pg_privilege_diagnostic` · `ops_hdbscan_sweep` | measurement + cursor state; each is the evidence behind a numbered issue |
| api (2) | `bronze_recall_api` · `gold_recall_alert` | 2,117 rows / 653 campaigns · 0 alerts (correct — nothing novel) |
| not tables (3) | `complaint_chunk_idx` (foreign — the AI Search index) · `evidence_metrics` (metric view) · `fleetguard_agent_payload` (inference table, auto-created by `agents.deploy()`) | — |

**Lakebase** (`databricks_postgres.bootcamp_students`): 12 `fleetguard_*` tables, **139,000+ rows**
(`fleetguard_defect_signal` populated 2026-09-02 — 48 signals; empty since Phase 5 until then)
(vehicle 20,000 · exposure 118,323 · campaign 592 · depot 60 · + service campaigns/work orders/audit).
Per-user identity verified: 25 Databricks identities exist as Postgres login roles, `current_user`
resolves to the caller, and `row_security` is `on`. **CDF** (`bootcamp_students.bootcamp_cdc`): 12 `lb_fleetguard_*_history` tables,
exact names, no collision suffixes. **`fleetguard_technician` added 2026-09-04** (120 rows, ~2 per
depot, real roster backing work-order assignment) — picked up by CDF automatically via
`REPLICA IDENTITY FULL`, same as every other table, no extra CDF configuration step needed.
`fleetguard_work_order.status` also gained a real `CHECK` constraint the same day (previously bare
`TEXT`, only `'OPEN'` ever written) — see I-066.

**Models:** `bootcamp_students.fleetguard.fleetguard_agent` — **6 registered versions**, **v6 serving** (v1 superseded by I-050's fix, v2 by the signals tool, v3 by the write action, v4 by I-075's match tiers, v5 by the fleet-vocabulary tool + prompt split, I-076/I-077). Only v6 is provisioned; the rest are registered but not served.

**Compute:** 1 pipeline (`fleetguard-bronze-silver`, IDLE) · **24 `fleetguard-*` jobs — 23
manual, 1 event-triggered** (`fleetguard-cdf-to-gold`, `table_update`, UNPAUSED 2026-09-08;
"all manual" stopped being true then) ·
1 serverless SQL warehouse · 1 AI Search endpoint.

**AI/BI Dashboard & metric view (added 2026-09-03):** `FleetGuard — Fleet & Recall Overview`
(`dashboard_id 01f1a7257e801a2ebb71bdc18fc2113a`, published), 4 pages — Overview, Emerging
Signals, Evidence, Trust — 9 datasets, all against `bootcamp_students.fleetguard` on the
existing `Serverless Starter Warehouse` (`b15d3d6f837ba428`). No new compute, no scheduled
auto-refresh (queries run only on view). `bootcamp_students.fleetguard.evidence_metrics` — a
UC Metric View governing `Detection Rate %` / `Lift` / `Median Lead Days` once, sourced from
`gold_lead_time_summary`; the dashboard's Evidence page now reads it via `MEASURE(...)`
instead of carrying its own copy of the rate/lift SQL. See I-064/I-065.

**APIs integrated:** vPIC (`DecodeVINValuesBatch`, authoritative for make/model/year) ·
recalls (`recallsByVehicle`, 200/200 combos, 100 s sweep) · `static.nhtsa.gov` flat files
(`If-Modified-Since`, verified 304).

⚠️ **Now billing — two things:**
1. AI Search endpoint `fleetguard-vs` (STANDARD, 1 unit) — **~$6.72/day**, started 2026-08-31.
2. Model Serving endpoint `agents_bootcamp_students-fleetguard-fleetguard_agent` (Small CPU),
   started 2026-09-02, serving **v6**. **`scale_to_zero_enabled` is `True`** as of 2026-09-08,
   so it bills per use rather than continuously and the first question after an idle period
   pays a cold start. **This has to be re-asserted after every deploy:** `agents.deploy()`
   set it back to `False` on both v5 and v6, silently reverting the decision.
   **The rate cannot be self-served from this workspace:** `system.billing` requires
   `USE SCHEMA`, which a non-admin on a shared metastore does not have, and the public
   pricing pages publish GPU serving DBU rates only — there is no CPU workload-size table.
   Get the grant, use the pricing calculator, or read the bill; do not quote an estimate.
   **Redeploying does not retire the old version.** `agents.deploy()` of v2 left v1
   `DEPLOYMENT_READY` at 0% traffic — two containers billing for one agent. Removed
   2026-09-02 via `serving-endpoints update-config`; endpoint re-verified afterwards
   (25 vehicles / 22 depots / EXACT). Check for this after every redeploy. **It has now
   happened three times** (v1, v4, v5) — assume it, do not check for it hopefully.

3. **`fleetguard-cdf-to-gold` (job `851598550157757`) is the first job that runs without being
   asked** — `table_update` trigger, UNPAUSED 2026-09-08. It is **event-driven, not scheduled**:
   it fires only when `fleetguard_agent_action` or `fleetguard_defect_signal` change, i.e. on an
   agent write or a signals load, never on browsing, approvals or work-order edits.
   `min_time_between_triggers_seconds: 60` caps it at one run/minute and each run is ~55 s of
   serverless. Two runs observed across a full verification cycle. **To stop it:**
   `databricks jobs update --json '{"job_id":851598550157757,"new_settings":{"trigger":{"pause_status":"PAUSED",...}}}'`
   — pass the whole `table_update` block, `new_settings` replaces the trigger wholesale.

Nothing else is scheduled: no cron, no Lakebase-side jobs.
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

**Full end-to-end run in `static-dev` against live Lakebase — 2026-09-08, PASSED.** The whole
vertical slice, both write paths, exercised in one session and cleaned up afterwards:

| step | result |
|---|---|
| 8 read routes | all 200 — queue 50 · signals 50 · work-orders 25 · depots 60 · technicians 120 · evidence 1.44×/z 2.62 |
| **Write 1** — approval gate | `SC-21V037000-e583bc8b`, **205 work orders** + audit, one transaction, **3 s**, attributed |
| **Write 2** — agent | `AGENT-19098b28700a` FREIGHTLINER/CASCADIA, 1,217 vehicles, `EXACT`, real `opened_by` |
| CDF | all 207 rows replicated (1 signal · 1 campaign · 205 work orders) |
| `table_update` trigger | fired **unattended**; `gold_agent_action` 2→3, `gold_defect_signal_current` 50→51 |
| Console | new signal renders badged `AGENT` with its human opener; counts 51 / 5 |
| **Cleanup** | 214 rows removed, verified from a **fresh connection** (I-073) — back to 1 / 25 / 50 exactly |
| Delete propagation | trigger fired again; gold facts returned to 2 / 50 — the I-080 tombstone path, proved a second time |

Two things confirmed rather than assumed. **I-063 is genuinely closed**: re-approving `17V629000`
returned a clear `409` naming the existing campaign and its launch time, instead of silently
creating a duplicate. And the agent again used the **fleet's** spelling (`CASCADIA`) unprompted,
so I-076's vocabulary tool is working on the real path, not just in a probe.

CDF retains the test rows as **1 insert + 1 delete** — append-only by design, and exactly what
the corrected latest-per-key pattern consumes to drop them from the gold facts.

Three layers, doing different jobs. Full rationale in `src/pipelines/expectations/README.md`.

| Layer | What | Run |
|---|---|---|
| **Unit — backend** (`tests/*.py`, 23 files) | Pure logic, no Databricks: VIN/chunking/naming, the auth seam, scoping, db helpers, the evaluation scorer's negation logic. **341 tests, ~1 s.** | `pytest` |
| **Unit — frontend** (`app/frontend/src/lib/*.test.ts`, 4 files) | `api.ts`'s error handling, `theme.ts`, `markdown.ts`, and `dates.ts`'s overdue comparison (I-087 — mutation-checked to fail in **both** UTC+2 and UTC-7, so it catches a timezone bug from the timezone where that bug is invisible). Zero frontend tests existed before 2026-09-02. **30 tests.** | `npm --prefix app/frontend run test` |
| **LDP expectations** (`src/pipelines/**/*.sql`) | Row-level, in-pipeline. Post-routing invariants + explicit `_dq_failures` quarantine split. | runs with the pipeline |
| **Data quality** (`tests/test_data_quality.py`) | Cross-table invariants against live tables. **21 tests, 73 s.** | `pytest -m integration --run-integration` |

**CI added 2026-09-09** (`.github/workflows/ci.yml`): the first two layers now run on every push
to `main` and every PR — `ruff check` + `pytest` on one job, frontend typecheck + tests + build
on another. Until now a suite whose whole purpose is catching *silent* failures only ran when
someone remembered to run it. It **never touches the workspace**: no credentials are configured,
and the integration layer self-skips without `--run-integration` (`tests/conftest.py`), which is
what makes it safe on every push. It does not deploy — deployment stays manual, because it
restarts the app under whoever is using it. `requirements-dev.txt` pins what the checks need and
sources runtime versions from `app/backend/requirements.txt`, so local and CI cannot drift.
Immediate motivation is **B3**: the pre-demo refresh is expected to break pinned numbers, and
that is only useful if it surfaces on the push rather than on demo morning.

**End-to-end repo review, 2026-09-02.** Read every backend router, the auth seam, `db.py`,
`scoping.py`, and every stateful frontend view, adversarially — not just "does it run" but
"what happens on the second transition through this effect, the second concurrent request,
the reachable-but-untested branch." Found and fixed **8 real bugs**, none previously known:

- **I-062** — session cookies had no server-side expiry; a captured/replayed session was
  honoured forever, and the provider Render actually runs (`AppLoginTokenProvider`) had
  **zero** tests before this review. The two facts are connected, not coincidental.
- `chat.py` returned a confusing 502 instead of the frontend's dedicated 503 "offline" state
  for any signed-in Render user — live and reachable, not theoretical.
- **App.tsx**: the anonymous-visitor auto-redirect had no one-shot guard — after firing once,
  clicking "Recall queue" again silently bounced back to Evidence. Found by tracing a
  *second* transition through the effect, not just confirming the first one worked.
- **Campaign.tsx**: two bugs in one effect — stale approval-success state could show over a
  different campaign's data on an id change, and a slow response for a stale id could
  overwrite a newer one. **Signals.tsx** had the identical race on its filter checkbox.
- A misplaced docstring (dead statement after a `return`, silently dropped from FastAPI's
  generated docs) and a `zip()` without `strict=` in Model B's feature engineering.
- **I-063** (logged, not fixed): no idempotency check on campaign approval — a product
  decision, not a code-review call, so it's recorded rather than silently resolved.

49 new tests came out of this pass: `test_auth_seam.py` 22→38 (+16), `test_scoping.py` new
at 16, `test_db_helpers.py` new at 4, frontend 0→13. Full detail in `ISSUES.md` and the
review's commits.

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
| ~~NEW~~ | ~~**Agent endpoint lifecycle.**~~ **REVISED 2026-09-08: scale-to-zero is now ON.** The 2026-09-02 decision was "keep it running hot"; after the v5 write-action testing the user asked for billing to stop, so `scale_to_zero_enabled` was flipped to `True` via `serving-endpoints update-config` (single entity, v5, polled to `READY`/`NOT_UPDATING`). **This is not instant** — it stops billing once the endpoint actually scales down after its idle window, and the first demo question then pays a cold start. Delete + redeploy remains the only guaranteed-immediate zero (v5 stays registered in UC; redeploy is one job run). There is **no `stop` subcommand** — Model Serving offers only scale-to-zero or delete. **The rate still cannot be measured from this workspace** — `system.billing` needs `USE SCHEMA` we do not have, and the public pricing pages publish GPU rates only. | Cost |
| **NEW** | **Should the public chat panel answer?** `/api/chat` uses the caller's token, so it 401s on Render. Making it work needs a service identity there — an **amendment** to §8a's "no PAT or SP on Render", not an exception. That rule's stated reason is the write path and `/api/chat` has none, but it would expose workspace-billed LLM inference and complaint retrieval to anyone with the URL. | Demo polish only |
| **I-018** | **Index lifecycle.** How long to leave the AI Search endpoint up: ~$6.72/day, ~23 days to demo ⇒ ~$155 if left running throughout. No longer the *only* recurring cost — the agent serving endpoint now runs alongside it. | Cost |
| ~~I-015~~ | ~~**Streaming vs PII guardrail.**~~ **Resolved by choosing not to stream.** `/api/chat` is non-streaming, so the §4.5 output guardrail claim stays available. Cost: answers appear all at once after a few seconds. | — |
| ~~I-026~~ | ~~Chunking scope.~~ **Resolved by measurement** — the full-corpus hybrid test returned 10/10 distinct `complaint_id`, so the near-1:1 chunk table causes no near-duplicate retrieval problem and needs no read-time dedupe. | — |

---

## Risks, honestly

0. **The App is proved for one person, and the failure mode is the bad one (I-084).** This is
   the project's live risk as of 2026-09-08, replacing the ones below that were retired. Every
   verification of the Databricks App ran under the owner's identity. If Lakebase does not
   auto-provision a Postgres login role, a judge authenticates successfully, sees the console
   shell, gets their real identity back from `/api/me` — and then every data route 500s. **A
   failure that arrives after visible success reads as a broken project, not a missing grant.**
   That is I-050's lesson one layer up, and it is unresolved because it cannot be tested from
   this account. One second-identity sign-in retires it; no amount of further owner-side
   testing can.

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
3. ~~**Scope vs schedule.**~~ **Retired 2026-09-02 — every phase but the App migration is
   done.** Phases 4/6/7/8/10 all shipped: Model B with a real, leak-checked precision/recall
   (4), auth seam + GitHub login (6), agent with 4 tools + evaluation gates (7), console live
   on Render with themes and a signed-in operator surface (8), Postgres RLS proved live on
   depot scoping (10). What is left is the Databricks App migration (~20 Sept, an afternoon
   per the seam) — the last item on the whole plan, not one of several. Cut list unchanged
   and still agreed: Feature Store online serving, Genie Agent, Unity AI Gateway, governance
   kept at its visible slice rather than the full matrix.
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

## Where we are — 3 Sep

**End-to-end review and a visual redesign, both landed and verified live — no new capability,
all hardening.** Two separate passes, same session.

**1. Adversarial code review.** Read every backend router, the auth seam, `db.py`,
`scoping.py`, and every stateful frontend view — not "does it run" but "what happens on the
second transition through this effect, the second concurrent request, the reachable-but-untested
branch." Found and fixed 8 real bugs: the session-expiry gap (**I-062** — cookie `max_age` only
controls when the browser stops sending a session, not how long the server honours it), the
`chat.py` 502-instead-of-503 for signed-in Render users, App.tsx's redirect loop on a second
"Recall queue" click, matching stale-response races in Campaign.tsx and Signals.tsx, a dead
docstring, and an unguarded `zip()`. Logged rather than silently resolved: **I-063**, no
idempotency check on campaign approval — a product decision, not a code-review call. 49 new
tests came out of the pass (detail in the Testing section above and in `ISSUES.md`).

**2. Console redesign.** Same visual language, more craft — no new colours, no new information
density; the original CSS's own "severity legible, colour rationed" rule stayed the rule. Two-
layer shadows, left-accent spines on stat cards and message boxes instead of background tint
alone, a brand-mark chip, pill-hover nav tabs, colour-matched glow shadows on the primary/danger
buttons, an accent rail on hover for clickable table rows, themed scrollbars, a signed radial
glow behind the sign-in card. All motion is 120–250ms hover/press feedback, never idle
animation, and collapses under `prefers-reduced-motion`.

**Verified, not assumed.** Installed a headless Chromium locally and screenshotted every view —
queue, campaign detail, signals, evidence, sign-in — in both themes, against a real backend
running in `snapshot` mode with actual data, before committing anything. Then checked the live
deployment: confirmed the served bundle hashes (`index-CZ4VHG3q.js` / `index-CoeEXbtC.css`)
match the local build exactly, and screenshotted the live site's evidence page and sign-in gate
in both themes. Campaign detail and the operator queue can't be screenshotted live without a
real GitHub session — the deployment's own auth gate, working as designed, not a gap — so the
local snapshot-mode screenshot stands in for them, since it is the same built bundle.

Commits: `719d9b1` / `75f7688` / `50b7577` (the review), `043e747` (three frontend bugfixes),
`604d01e` (docs), `8f79d26` (the redesign). Pushed to `main`; Render auto-redeployed and was
confirmed serving the new build.

**Later the same day: verification hooks, an AI/BI dashboard, and a metric view — none of it
new capability, all of it making existing claims harder to get wrong.**

**1. Verification hooks** (`.claude/settings.json`, commit `7be634d`). A **Stop hook** now runs
the full off-platform suite (`.venv/bin/pytest`, `npm test` if `node_modules` exists) on every
turn-end attempt and blocks via the documented `continueConversation: true` +
`systemMessage` JSON on failure, carrying the real failure output. A **PostToolUse hook**
extends the existing ruff format/fix step with a real `ruff check` (remaining errors surface
via exit 2 + stderr) and adds `tsc --noEmit` on edited `.ts`/`.tsx` files. Both verified by
extracting the actual command and running it directly against deliberately broken input before
trusting the config — caught a real bug doing this: `ruff check` prints "All checks passed!"
even on success, so gating on non-empty output (the first draft) would have false-blocked on
every clean edit; fixed to gate on the actual exit code.

**2. AI/BI Dashboard** (`dashboards/fleetguard_overview.json`, commit `a056c77`). Four pages —
Overview, Emerging Signals, Evidence, Trust — built from schemas and aggregate queries checked
live against `bootcamp_students.fleetguard` before being embedded, not written from memory.
Reuses the existing `Serverless Starter Warehouse`; no new compute, no auto-refresh configured
(the main driver of unexpected AI/BI cost). Two real bugs caught pre-deploy: an `ORDER BY`
that self-shadowed an aggregate alias against its own source column name, and a reconciliation
false-positive — naively comparing full `bronze_complaints` (all product types) against
silver+quarantine reports a mismatch, because out-of-scope product types are filtered *before*
that split, not part of it; fixed to the documented V+T scope, now reconciles exactly for all
three entities. The **Trust** page is new: it proves `bronze = silver + quarantine` per entity
from the live quarantine tables rather than asserting it, shows quarantine reasons by entity,
and shows fleet-match confidence (EXACT vs MODEL_VARIANT) with the caveat that the
deterministic guarantee only holds for EXACT.

**3. `evidence_metrics` metric view** (`dashboards/metric_views/evidence_metrics.sql`, commits
`1bdfae6` / `0788d45`) — prompted by noticing the dashboard's Evidence page was the *third*
independent recomputation of the same lift/rate arithmetic (`gold_lead_time_summary` →
`export_evidence.py` → the dashboard's own SQL). A UC Metric View now defines `Detection Rate
%`, `Lift`, and `Median Lead Days` once, both per-arm and as unconditional single-value
measures for KPI tiles; the dashboard's Evidence page was rewired to read all six of its
widgets via `MEASURE(...)` instead of carrying its own copy. Two real findings while building
it, both logged in full in `ISSUES.md`: **I-064**, almost sourcing it from `gold_lead_time_v3`
— the newest-*sounding* table, but whose `detected_v3` reproduces 11.2%, the already-published
*abandoned* semantic-clustering result, not the shipped 16.0%/11.1%; caught by checking three
candidate columns against `STATUS.md`'s own published figures before writing any YAML. And
**I-065**, the experimental `aitools tools statement submit --file` CLI silently mangling the
YAML (almost certainly on literal `%` characters in measure names / `LIKE` patterns) with a
misleading parse error, even though the identical content validated clean locally via PyYAML —
switched to the stable Statement Execution REST API, which deployed clean.

Also discussed and deliberately deferred: a Genie Agent (would duplicate the existing chat
agent's job for the operator persona; better fit is linking one to this dashboard later if an
open-ended-analyst need actually shows up) and Genie's cost model (a real, if usually small for
light use, per-user LLM billing component since 2026-07-08, on top of warehouse compute).

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

**Closed 2026-09-08 — the agent performs a real write.** This answers the question "is this
repo actually doing agentic action?", where the honest answer had been *no*: all four tools
were reads, and `propose_service_campaign` returns a dict. The chain now runs
**user → agent → complaint retrieval → decision → `open_defect_signal` → Lakebase insert →
CDF → Delta → console**, verified end to end from a browser. The model never executes the
write and cannot — the app does, under the caller's identity, so `opened_by` is a genuine
human and RLS applies exactly as it does to a UI click. v5 serving; v4 dropped after
verification (I-052 again — it was still `DEPLOYMENT_READY` at 0% traffic).

**The first live run was silently wrong, and that is the more useful finding.** It reported
`fleet_vehicles = 0` for a real brake defect on **2,116** F-250s: the agent names models the
way NHTSA does (`F-250 SD`), the fleet registry uses vPIC's (`F-250`). That is **I-030 —
already measured, documented and correctly handled in `gold_fleet_exposure` — reintroduced
months later by new code joining the same two vocabularies.** Zero is the worst possible
value, because the Emerging tab orders by that column. Fixed with the gold layer's own match
tiers (`EXACT → MODEL_VARIANT → MAKE_ONLY → NONE`), reported rather than blended. **I-075.**

### Next, in priority order

**✅ I-086 — RESOLVED 2026-09-08, same evening it was found.** The App's Lakebase routes were
403ing for the owner's own browser session (`Invalid scope, required scopes: postgres`) and
surviving every fix. Root cause: **OBO scope lives in three planes, not two** — the workspace
allowlist (`["*"]`, fine), the app resource (`postgres, sql, model-serving`, fine), and a
**sticky per-user consent grant** that was captured before I-083's scope fix landed and never
widened when the app's scope list did. Restart, sign-out/sign-in and re-applying
`user_api_scopes` all failed because each targeted a plane that was already correct.
Fixed self-service — `DELETE /api/2.0/oauth-app-integrations/<oauth2_app_client_id>/user-consent/me`,
then reopen the app in a **fresh/incognito** session (revocation does not invalidate
already-issued tokens for up to an hour, so a warm session imitates the bug after a correct
fix). Verified: `user_consented_scopes` now carries all three, and the console works in a real
browser. **The earlier "needs account-admin" conclusion was wrong** — `/user-consent/me` is
deliberately self-service. Full write-up, including the two false claims it corrected, in
`ISSUES.md` I-086.

**This also retires one of I-084's three unknowns** — the browser OAuth-consent step, never
previously exercised, has now been completed successfully by a real human in a browser.

**I-084, still open and now unblocked:** the App has still only ever been verified end to
end under the **owner's identity**. `db.py` connects to Postgres **as the caller**, and it is
unknown whether Lakebase auto-provisions a login role on first connect: measured live,
`zach@zachwilson.tech` has one, but `eumardassis@gmail.com` and `gudetayared@gmail.com` — both
owners on this metastore — do **not**. "The judges are admins" does not settle it; admin
clears the app ACL, Postgres roles are a separate namespace. **We cannot pre-create roles** (no
`CREATEROLE`, Phase 10), so if auto-provisioning does not happen there is no fix from this
account. **One sign-in by one real second identity settles this**, along with the untested
browser OAuth-consent step. **Do not test with `zach@zachwilson.tech`** — he already has a
role, so a pass proves nothing. Pick someone without one. Fallback if it fails: Render's
`app-login` + snapshot needs no Databricks identity at all.

Also decide before the demo: **judges are not in `FLEETGUARD_APPROVERS`**, so they can read
everything and get 403 on approve. Admin does not bypass it — the gate is application logic.

---

## WHAT IS LEFT BEFORE THE DEMO

**As of 2026-09-08. Demo 25–30 Sept (~17 days).** Every phase is done or deliberately cut; the
list below is all that stands between here and the demo. Items are ordered by what would hurt
most if skipped, not by effort.

### A. Blocking — cannot be resolved from this account

**~~A0. I-086~~ — ✅ RESOLVED 2026-09-08.** Was: `403 Invalid scope, required scopes: postgres`
on every Lakebase route, for the owner's own browser. Cause was a **stale per-user OAuth consent
grant** — a third configuration plane, self-service to fix, not an account-admin wall. Revoked
via `/user-consent/me` + re-consented in a fresh browser session; verified working. **A1 is
unblocked.** See `ISSUES.md` I-086.

**A1. One second-identity sign-in on the Databricks App (I-084) — now the single highest-value
action remaining.** The App is verified end to end under the **owner's identity only**. One
sign-in by one other person retires the two unknowns that survive: whether Lakebase
auto-provisions a Postgres login role for an identity that has never used it, and whether the
app ACL is right. *(The third — whether the browser OAuth-consent step works at all — was
retired by I-086's fix, which forced exactly that flow and completed it successfully.)* **Pick a
tester WITHOUT an existing Lakebase role — not `zach@zachwilson.tech`, who has one, so a pass
would prove nothing.** No further owner-side testing can substitute. **Warn the tester they
will see a consent screen** listing `postgres`/`sql`/`model-serving` and must accept it — now a
known, expected step rather than an untested one, and the app will 403 exactly as it did in
I-086 if they decline.

**A1 state as of 2026-09-09 — in flight, but it will not close A1 on its own.** TA Raghu
(`raghavendra.yama@gmail.com`) has been granted **`CAN_USE`** on the app and asked to sign in and
browse. Three things a cold session must not misread:
- **He already has a Lakebase login role** (checked: `postgres list-roles` on
  `summer-bootcamp-2026-v2`, 32 roles, his among them). So a pass proves the ACL, the consent
  flow and the non-owner code paths — **not** the auto-provisioning question that actually
  threatens the demo. **A1 still needs a tester without a role.** Most of the ~296-person cohort
  qualifies; check any candidate with that same command before asking them.
- **The app must be started for him** — `CAN_USE` does not permit starting stopped compute, and
  the app is deliberately stopped.
- **He is in `FLEETGUARD_APPROVERS`** (added `dfcc13c`), so Approve would **succeed** for him,
  writing a service campaign plus ~200 work orders and firing CDF. The app ACL and the approver
  list are independent mechanisms — `CAN_USE` grants no protection here. He has been asked not
  to approve; if that write is wanted deliberately, budget the cleanup.

**A2. Decide whether judges can approve.** `FLEETGUARD_APPROVERS` holds **two** addresses as of
`dfcc13c` — the owner and `raghavendra.yama@gmail.com` — and nobody else, so a *judge* still gets
**403 on approve**. The approval gate is application logic and workspace admin does not bypass
it; nor does the app ACL, which is a separate mechanism (`CAN_USE` neither grants nor withholds
approval). The approval flow is arguably the strongest thing in the demo; leaving it locked is a
choice, not an oversight. One env-var change on whichever surface is used — and it needs a
redeploy to take effect, so decide before the pre-demo deploy rather than on the day.

### B. Build work still outstanding

**~~B1. `I-079`~~ — ✅ CODE FIXED 2026-09-09, rebuild still owed to B3.** The detector's exact
make/model join (I-030's third appearance) is now tiered `EXACT`/`MODEL_VARIANT`, matching the
gold layer and the agent write path rather than adding a fourth spelling of the rule. Verified
read-only against live data before shipping: it reproduces the measured 2,418 and 766 exactly
and changes **only those two of 48** rows. The tier travels with the count through the API and
renders as a `VARIANT` badge, so the fix does not trade a wrong `0` for a falsely precise
`2,418` — that number includes 315 `PROMASTER CITY` vans. 7 new tests
(`tests/test_signals_routes.py`; the router had none). **The stored table still holds the old
zeros** — B3 rebuilds it, and running it twice is what the deferral policy exists to prevent.

**B2. Seeded demo state.** Phase 11's last remaining piece.

**B3. The pre-demo data refresh — the day before, not the morning of.** Chain, in order:
ingest → bronze/silver → rebuild `gold_emerging_signal` (**now carries B1's tiered join and a
new `match_basis` column** — the loader adds it idempotently, so no manual migration) → load
Lakebase → **expect "N affecting your fleet" to become 6 of 50, not 4** →
`export_evidence.py` + `export_demo_snapshot.py` → commit → deploy. **Expect this to break the
pinned numbers** in the agent smoke test (25 vehicles / 22 depots; 48 signals / 2 fleet-relevant
/ RAM 2500 at 1,256). That breakage is intentional — it forces a look rather than a silent pass
— but budget time to update the pins afterwards. **The signals build's own assertions will not
fire**: checked 2026-09-09, they are range guards (`n > 0`, `n < 2000`), not pinned counts, so
they neither catch nor complain about this change. Only the agent smoke test pins exact values.

### C. Optional — decide rather than default into

**C1. E-01, the AI Gateway PII guardrail.** Worth doing because it corrects a claim that is
*wrong today* in the frozen proposal (§4.5 credits agent endpoints with guardrails they do not
support). Blocked on a `system.ai` privilege wall; the honest path is option (c) — build from
published docs, clearly caveated as unverified against this workspace.

**C2. E-07 and E-09 — record the decision, do not build.** Both are marked ADOPT in
`ENHANCEMENTS.md` with zero implementation and no logged reversal, which is I-051's pattern in
the backlog. **E-09 (LangGraph) is arguably already satisfied**: it was adopted for
suspend-and-resume on the write path, which the action-envelope pattern now does across a
process boundary, under a different identity. E-07 (labeling) is a mechanism demo with n=1 by
its own admission. Write both decisions down and close them.

**C3. Notification digest.** Last Phase 13 item, never started, needs a new email dependency.
Lowest value of anything remaining.

### D. Standing cost decisions

| item | state | note |
|---|---|---|
| AI Search `fleetguard-vs` | **running, ~$6.72/day** | ~17 days ⇒ **~$115** if left up to the demo (I-018). The only continuously-billing resource. **Do not rebuild the index inside the demo window** — it is most of a working day |
| Agent serving endpoint | v6, **scale-to-zero ON** | `agents.deploy()` resets this to `False` on *every* deploy — re-assert it after any redeploy |
| Databricks App | **STOPPED** — brought up 2026-09-08 to fix I-086, stopped again once verified | `databricks apps start fleetguard-console`, ~2 min. It will not answer a cold URL, and `CAN_USE` does not let a tester start it — bring it up before anyone else tries |
| `fleetguard-cdf-to-gold` | **UNPAUSED** | Event-driven, not scheduled; fires only on agent writes or signal loads, capped at 1 run/min |

### E. Demo-day mechanics, easy to forget

- **Start the App first** (~2 min) — and the agent endpoint takes a **cold start** on the first
  question, because scale-to-zero is on. Warm both before anyone is watching.
- `/` on Render is cached and can serve a stale `index.html` for minutes after a deploy (I-054)
  — probe an API route to confirm a deploy, never the console page.
- The Emerging tab's "**4 affecting your fleet**" is 2 batch + 2 agent-opened of 50. If B1 lands
  it becomes **6 of 50**. Do not confuse it with I-079's separate "2 → 4 of 48" (I-079).

---

### Historical detail — completed items, kept for the record

*Everything below is done, superseded, or deliberately shelved. Retained because this project's
argument depends on the record of what was tried, not only what shipped.*

0. ~~**The agent cannot see the fleet's make/model vocabulary**~~ and ~~**rule 6 reads as a
   permission gate**~~ — **both fixed, deployed and verified against the live endpoint
   2026-09-08. v6 is serving, single entity, scale-to-zero ON.**

   `lookup_fleet_models(make=None)` (I-076) is the sixth tool: it reads `gold_fleet_vehicle`
   — the same table Lakebase's `fleetguard_vehicle` is loaded from, so its spellings are
   exactly what the write path matches on — and returns the make roster whether or not a make
   was supplied, so a model asking about a make the fleet does not own sees the alternatives
   in the same result. `SYSTEM_PROMPT` rule 6 was split into three (I-077): rule 6 now says
   explicitly that it governs *description, not permission*; rule 7 says being asked to open a
   signal **is** the authorization and to state an assumption rather than stall; rule 8 says
   to check make/model with `lookup_fleet_models` and pass the *fleet's* spelling.

   **Verified live against the warehouse before packaging** — the tool's exact SQL and its
   parameter binding, not a literal-substituted approximation (I-056 was a binding that worked
   in one shape and not another): 15 makes · 47 make/model combos · 20,000 vehicles ·
   `FORD`/`F-250` = 2,116 with no `F-250 SD` · `make='ford'` case-insensitive ·
   `make='DODGE'` → `make_in_fleet=False`, empty models, full roster still returned. The
   RAM 2500 the agent was asked about is **1,256 vehicles** — the answer it stalled instead of
   giving. `gold_fleet_vehicle` added to `resources` in the logging cell; omitting it would
   fail only at demo time (I-050's shape).

   **Deployed and verified end to end.** Notebook synced to the workspace,
   `fleetguard-agent-build` run with `deploy=false` (`result_state: SUCCESS`, so the new
   smoke-test assertions passed — they raise), v6 registered `READY`, then
   `fleetguard-deploy-agent` with `model_version=6`. Live endpoint verified twice: asked what
   the fleet operates it returned Ford 8,619 / RAM 4,304 / F-250 2,116 / RAM 2500 1,256,
   every figure matching the warehouse; replaying the original failure it **acted instead of
   stalling**, scoped to `RAM`/`2500` in the fleet's spelling, quoted the 1,256-vehicle count,
   and still said "requested", not "saved".

   **I-052 fired again, and worse.** After `agents.deploy()`: v5 was still
   `DEPLOYMENT_READY` at 0% traffic (third occurrence), **and v6 came back with
   `scale_to_zero_enabled: False`** — silently reverting the cost decision made earlier the
   same day. Both fixed in one `update_config`, payload built programmatically from the live
   entity so `MLFLOW_EXPERIMENT_ID` survived. Endpoint is now one entity, v6, scale-to-zero
   **on**, `NOT_UPDATING`/`READY`. **`agents.deploy()` resets scale-to-zero every time — treat
   re-enabling it as part of the deploy, not an optional follow-up.**

   Two test-method findings, both worth keeping: the SDK's `serving_endpoints.query()`
   silently drops a `ResponsesAgent`'s entire `output` and returns HTTP 200 with an
   almost-empty object (**I-078** — query `/invocations` raw, as the console does). And the
   first regression check asserted `"dodge" not in answer`, which **failed on correct
   behaviour**: the agent named a 2008 Dodge Ram recall only to reject it as too old. Assert
   on the action envelope, which is what reaches the database — the prose check was I-058's
   naive-scorer mistake repeated.


0. **`render-u2m` login is still blocked; the pipeline it would exercise is independently
   confirmed live end to end (2026-09-04).** The account-admin OAuth registration landed
   2026-09-03 (E-14's blocker resolved), but the app was registered without the `all-apis`
   scope (`access_denied: Scopes 'all-apis' are not assigned to the client ...`), and the
   admin pushed back on granting it — reasonably: verified against Databricks' own API
   reference that `all-apis`/`sql`/`offline_access`/`openid`/`profile`/`email` is the
   *entire* assignable scope set for a custom app integration, so there is no narrower
   combination that covers both Lakebase and Model Serving (the two APIs this app needs
   beyond `sql`). Awaiting the admin's decision.

   **Rather than wait, the whole pipeline the login would unlock was verified separately,
   with zero new exposure:** ran the backend locally in `static-dev` mode using the owner's
   own `databricks auth token --profile abhi` (a token from Databricks' own first-party CLI
   OAuth client — no custom app, no scope question, nothing shared with anyone). Confirmed
   live: `/api/queue` and `/api/campaigns/17V629000` return real Lakebase data matching the
   pinned values above; `POST .../service-campaign` created a real service campaign
   (`SC-17V629000-6082d04b`, 25 work orders) in one transaction; all of it — service
   campaign, all 25 work orders, and the audit log row — confirmed present in
   `bootcamp_students.bootcamp_cdc.lb_fleetguard_*_history` via CDF. Read, write, and CDF
   propagation are all proven; only the browser-based OAuth login itself remains blocked.
   (Test rows from this and later verification rounds were cleaned from live Lakebase
   2026-09-04 — see item 0.5 below.)

   If the admin doesn't grant `all-apis`: revisit Databricks Apps instead of narrowing
   further — a `sql`-only re-scope would still permanently lose the write path and chat
   feature (neither has an assignable scope short of `all-apis`), landing worse than Apps
   for comparable rework.
0.5 **Commercial-fleet-value roadmap — 5 of 8 items built and verified live, merged to `main`
   and pushed (2026-09-04).** Came out of a UX walkthrough plus a "does this give a commercial
   fleet real value" analysis. Done, in build order:
   - **Work-order lifecycle + technician assignment.** `GET/PATCH /api/work-orders` and a
     "Work orders" tab, so work orders no longer sit at `OPEN` forever unread.
     `fleetguard_technician` (120 seeded, ~2/depot) backs a real assignment dropdown with a
     server-side depot-consistency check, not just UI filtering.
   - **"Launched" campaigns view** — first consumer of `GET /api/service-campaigns`, which
     existed since the approval gate itself but had no UI. Click-through into a filtered
     Work-orders view.
   - **Sort/filter on the Recall queue, Work orders, and Launched tables** — click-to-sort
     headers with an always-visible indicator (not hover-only, fixed after review), plus
     dropdown filters. Recall queue's consequence-before-volume default order is preserved
     until a user explicitly clicks a header.
   - **Per-work-order actual cost logging** (`fleetguard_work_order.actual_cost`, nullable,
     `CHECK >= 0`), summed and broken down by component/depot via `GET /api/cost-breakdown`.
     Replaced a same-day-rejected first design that multiplied one flat assumed "$/vehicle"
     figure — wrong even fully disclosed, since different repairs cost different amounts
     (I-069).
   - **Audit log made readable** — a filterable "Audit log" tab plus CSV export of
     `fleetguard_audit_log`, which had recorded every launch/status-change/assignment/
     cost-log since Phase 7 with zero consumers until now.

   All writes stay gated by `FLEETGUARD_APPROVERS` regardless of auth source; all reads follow
   the existing open-to-any-signed-in-identity asymmetry. **Merged to `main` as one squash
   commit (`ab554f2`) and pushed to `origin/main`** — the branch is no longer ahead of main.

   **Depot risk heatmap and recall trend chart: also done, also merged (2026-09-04/05).**
   `GET /api/depot-risk` (new "Depots" tab, all 60 depots, deliberately no single blended
   risk score — real component numbers instead, see I-069's lesson) and `GET /api/recall-trend`
   (new "Trends" tab, this app's first chart, hand-rolled SVG per the no-new-dependency
   precedent `markdown.tsx` set, 13 years of real NHTSA filing dates already in Lakebase).
   That's 6 of the original 8 roadmap items done.

   **Role-based views: attempted, then deliberately shelved — not merged, on purpose
   (2026-09-05).** Built real depot-scoping enforcement (`fleetguard_depot_assignment`,
   previously idle since Phase 10) rather than a cosmetic role picker, caught and fixed a
   real precedence bug before merging (I-070), and verified it live end to end. Then, asked
   directly whether it was worth finishing: **no.** Two reasons. First, it re-demonstrates a
   capability (Postgres RLS) that Phase 10 already proved and already counted as done —
   little new judging credit for the work involved. Second, and more concretely, live
   testing surfaced that the "Depots/Trends/Audit log stay fleet-wide" decision is not
   fully achievable without a real schema change: `fleetguard_vehicle_exposure` has no
   `depot_id` of its own, so `depots.py`'s vehicle-derived columns (`fleet_size`,
   `urgent_vehicles_exposed`, `total_vehicles_exposed`, `distinct_campaigns`) go through a
   join to the RLS-protected `fleetguard_vehicle` table — meaning Postgres's own RLS policy
   (not this app's code) silently zeroes those columns for every depot except the caller's
   own the moment *any* real assignment exists anywhere, regardless of what `depots.py`
   intended. Fixing it properly means denormalizing `depot_id` onto
   `fleetguard_vehicle_exposure` plus a static `fleet_size` column on `fleetguard_depot` —
   real scope, for a feature with **zero visible footprint in the default demo state**
   (nobody is enrolled by default; the depot-scope banner never appears unless someone
   deliberately sets up an assignment). Not a good trade this close to a fixed demo date.

   **The branch (`feature/role-based-views`) is kept, not merged, not deleted** — three
   commits, fully working and tested for what it does, available if this becomes worth
   finishing later. Only one thing off that branch landed on `main`: the local `static-dev`
   setup script (`scripts/run_local_static_dev.sh`, cherry-picked as `c5b3af0`) — useful
   regardless of the feature's fate, since it replaces a command that had been reconstructed
   from session memory every time rather than living anywhere checked in.

   **Remaining, not started:** notification digest (needs a new email-sending dependency —
   the biggest new-infra lift of what's left, and arguably not worth it for the same
   "low visible footprint" reason role-based views was shelved).

   **Test-data hygiene — clean as of 2026-09-04.** Live Lakebase was cleaned twice this
   session: 6 test service campaigns + ~150 work orders + audit rows removed mid-session, then
   one more leftover (`SC-17V629000-64c4958b`, from the round of verification right after)
   removed at session end. Only `SC-17V629000-b3b9dfbd` (pre-existing, 2026-09-01) remains —
   confirmed via a live `GET /api/service-campaigns` call, 1 row. CDF history in
   `bootcamp_cdc.lb_fleetguard_*_history` is append-only and was never and can never be
   scrubbed; cleanup only ever affects live/current Postgres state. Any *new* test campaigns
   created by a future verification round will need the same treatment again before a demo.
0.6 **E-01 (AI Gateway PII guardrail) — picked up, paused mid live-verification
   (2026-09-05).** After shelving role-based views, evaluated `docs/ENHANCEMENTS.md`'s
   backlog honestly and picked E-01 as the highest-value item left: it's marked "ADOPT —
   highest priority" there because it corrects an actual wrong claim already in the frozen
   proposal (§4.5 claims AI Gateway PII guardrails protect the agent; agent endpoints
   deployed via `agents.deploy()` don't support `guardrails` at all, only inference tables —
   confirmed live via `databricks serving-endpoints put-ai-gateway -h`'s own text, which
   states plainly that pay-per-token/external-model/provisioned-throughput endpoints are
   fully supported and agent endpoints are not).

   **Also confirmed live:** the agent currently calls `databricks-claude-opus-4-8` directly
   (`src/agent/14_fleetguard_agent.py`'s `LLM_ENDPOINT`) — a shared, pre-provisioned
   Foundation Model API endpoint. E-01's fix is a new wrapper endpoint (`fleetguard-llm`)
   around the *same* model, with the `ai_gateway` guardrail block attached to the wrapper
   instead — same model, same $/token rate, no new idle/reserved-compute cost (pay-per-token
   endpoints don't bill while unused). The one open cost question — whether the guardrail
   scan itself adds separate overhead — was never settled.

   **Blocked while trying to verify that live:** creating even a *disposable test* wrapper
   endpoint around a `system.ai.*` Foundation Model (tried both `databricks-claude-opus-4-8`
   and `databricks-gpt-5-5`) fails with `Model version '1' does not exist`, and the correct
   version string can't be discovered — `databricks model-versions list` against the
   `system` catalog returns empty, most likely a genuine privilege gap (`EXECUTE` on the
   model / `USE_CATALOG` on `system`) for a regular workspace identity, not a request-shape
   mistake (several shapes were tried and ruled out first). **Nothing was left behind** —
   every attempt failed before provisioning anything; `serving-endpoints list` confirms no
   `fleetguard-llm-test` exists.

   **Next steps, in order:** (a) try creating the *real* `fleetguard-llm` endpoint directly
   rather than a throwaway test — if the same version error recurs there, it's clearly a
   platform wall rather than a test-specific mistake; (b) if still blocked, ask whoever
   administers this shared workspace whether regular users have `USE_CATALOG`/`EXECUTE` on
   `system.ai` model versions; (c) failing both, drop the live cost-verification and build
   E-01 against Databricks' published AI Gateway docs instead of a hands-on measurement,
   clearly caveated as unverified against this workspace (matching this project's own
   "prefer stating a number as estimated" discipline).
1. **Databricks App (~20 Sept)** — now the **primary** hosting target, not a second surface
   alongside Render (decision 2026-09-08). It makes queue, assistant and Emerging live for a
   real user via OBO. The auth seam means it is an afternoon. Keep it `STOPPED` between
   sessions — `apps create` provisions billing compute on *create*.

   **Verified working locally in a browser, 2026-09-08**, before any of that:
   `scripts/run_local_static_dev.sh` against **live Lakebase**, screenshotted headless.
   All 8 tabs render; `/api/queue` 50 campaigns, `/api/depot-risk` 60 depots,
   `/api/service-campaigns` 1 (test data still clean — the day's agent probes called the
   serving endpoint directly, which cannot write, and correctly wrote nothing). The Emerging
   tab shows both agent-opened signals badged **AGENT**, ordered to the top by fleet size
   (F-250 2,116 · RAM 2500 1,256), with the detector-only columns blank rather than
   fabricated. Attribution checks out: `fleetguard_audit_log` carries `SIGNAL_OPENED` for both
   with the real human principal.

   ~~**Small gap found, not a bug:** the signals router selects 15 columns and `opened_by` is
   not one of them.~~ **Closed 2026-09-08.** The identity was written and correct but only
   visible one tab away in the Audit log — the write path's strongest claim, true and
   invisible at the same time. `opened_by` is now selected and rendered under `source='AGENT'`
   rows as "opened by …"; detector rows have no opener and render nothing. Verified in the
   browser against live Lakebase. Two tests, and the SQL assertion was **checked against the
   pre-fix code first** — a `Signal` field with a default validates perfectly against a query
   that never returned the column, so asserting the response alone would have passed against
   exactly the bug it closes.
2. ~~**Re-run the evaluation with the corrected scorers.**~~ **Done 2026-09-02 — agent v3
   passes both hard gates**, 10 cases:

   | scorer | first run | corrected |
   |---|---|---|
   | `never_invents_a_recall` | 0.667 | **1.000** |
   | `fleetguard_rules` (LLM judge) | 0.500 | **0.800** |
   | `never_claims_launched` · `grounded_numbers` · `states_match_tier` · `safety` · `relevance` · `answer_not_empty` | 1.000 | 1.000 |

   The two residual `fleetguard_rules` failures are judge artefacts, checked case by case: one
   applied the match-tier rule to a *complaint* count (the rule is about exposed **vehicles**),
   the other read "propose a campaign for 17V629000" — an id supplied in the question — as
   asserting a recall exists. Mild substance in the second: the agent verifies exposure for a
   campaign id but never that the campaign is in the recall table. Not worth a rule change
   before the demo; worth knowing.

   **The evaluation is ~25 seconds, not 90 minutes.** The MLflow run duration is 0.4 min for
   every run; the 90 minutes was `%pip install` + `%restart_python` + `load_model` building the
   agent's environment. Re-running is a 3-minute job.

   **Re-run against v6, 2026-09-08 — both hard gates still 1.000**, 12 cases (v4 was also
   evaluated on 2026-09-04; v5 never was):

   | scorer | v3 (10 cases) | v4 (12) | **v6 (12)** |
   |---|---|---|---|
   | `never_claims_launched` · `never_invents_a_recall` | 1.000 | 1.000 | **1.000** |
   | `fleetguard_rules` (LLM judge) | 0.800 | 0.727 | **0.636** |
   | `relevance_to_query` | 1.000 | 1.000 | **0.909** |
   | `grounded_numbers` · `states_match_tier` · `safety` · `answer_not_empty` | 1.000 | 1.000 | **1.000** |

   **The launch trap did not regress** — the concern going in was that I-077's "being asked is
   the authorization" would leak from signals to campaigns. It did not: rule 7 is scoped to
   signals, rule 4 still forbids launching, and the deterministic gate holds at 1.000.

   Only compare v4→v6 — v3 ran on 10 cases, before the multi-turn regression case was added,
   so its 0.800 has a different denominator. v4 failed 3 cases; v6 fails **the same 3 plus
   one**, and the new one fails for the *same reason* as an existing failure (an emerging-signal
   fleet count quoted without a tier). One case of an already-known complaint, not a new
   behaviour class.

   Of the 4 `fleetguard_rules` failures, two are judge artefacts of a kind already recorded:
   a **complaint** count judged against the vehicle match-tier rule, and *proposing* a campaign
   read as *launching* one — which the deterministic `never_claims_launched` scorer correctly
   passed on the same case. The `relevance_to_query` failure is the same shape: the agent was
   marked down for **declining to invent remedy text**, which is exactly what that case exists
   to require. Three scorers punishing correct behaviour is I-058's lesson recurring.

   **The other two failures were real, and found a bug (I-079).** They complained that fleet
   counts were quoted without a match tier; chasing why turned up that
   `gold_emerging_signal.fleet_vehicles` has no tier because the detector joins the fleet on
   **exact** make/model — I-030's third appearance, after `gold_fleet_exposure` (handled) and
   `agent_actions` (I-075). Two signals report 0 against 2,418 and 766 real vehicles, so
   "2 fleet-relevant" is really **4 of 48**. **No live signal is affected under either
   matching**, so nothing on screen is currently wrong; the fix is folded into the pre-demo
   refresh below, because it breaks the pinned 48/2/1,256 assertions on purpose.
3. ~~**Refresh discipline for the THREE snapshots.**~~ **Checked 2026-09-02, deliberately
   NOT refreshed — the decision, not the chore, was the point.** Corpus check first:
   `silver_complaint` latest complaint is 2026-08-27 (ingested 08-31), unchanged since the
   signals were built. NHTSA's `FLAT_CMPL.zip` shows `Last-Modified: 2026-09-02`, so newer
   source data exists upstream, but nothing has been re-ingested. Rebuilding
   `gold_emerging_signal` against the unchanged corpus reproduced **byte-for-byte identical**
   output (48 / 9 live / 2 fleet-relevant) — confirms reproducibility, changes nothing.

   The trap avoided: re-running `export_demo_snapshot.py` right now would stamp
   `captured_at: 2026-09-02` on **unchanged** data, and the console displays that timestamp to
   viewers as when it was "captured from live Lakebase". Bumping the date without new data
   would be the console asserting freshness it doesn't have — same shape as I-050 and I-058,
   at the UI layer this time.

   **Do the real refresh once, the day before the demo, not now**: ingest → bronze/silver →
   **fix the detector's exact make/model join first (I-079)** → rebuild `gold_emerging_signal`
   → load Lakebase → `export_evidence.py` + `export_demo_snapshot.py` → commit → Render deploy.
   The I-079 fix must land *before* the rebuild, or the refresh bakes the undercount in for
   another cycle; reuse the gold layer's tier expression rather than writing a third copy. Expect it to break the pinned numbers
   in the agent smoke test (25 vehicles / 22 depots) and the signals build assertions — that
   breakage is intentional, it forces a look rather than a silent pass, but budget time to
   update the pins afterward. Doing this today would mean doing it twice.

3.5 **Refresh discipline for the THREE snapshots** (superseded by the check above; kept for
   the mechanics), all point-in-time, all regenerated by hand, all of which a reviewer can
   date:
   - `gold_emerging_signal` → `fleetguard-emerging-signals`, then `fleetguard-load-signals`
     (dated `as_of 2026-08`)
   - `app/backend/fleetguard_api/evidence.json` → `scripts/export_evidence.py`
   - `app/backend/fleetguard_api/snapshot.json` → `scripts/export_demo_snapshot.py`
     (**this one is what the public console serves** — refreshing it needs a commit and a
     Render deploy, so it is not a same-day-of-demo task)
   Decide the refresh order and do it the day *before*, not the morning of.
5. ~~**Phase 4 Model B.**~~ **Done 2026-09-02 — precision 83.7%, recall 96.3%, ROC-AUC
   0.925, published on the evidence page.** Golden set is 765 pairs derived from NHTSA's own
   recall text (real, not synthetic — E-08). **Caught its own leakage before reporting
   (I-060):** first run scored an implausible precision=1.000, traced to a feature that was
   tautologically tied to the golden set's negative-label rule rather than learned; fixed and
   retrained. This was the largest remaining *capability* gap — closing it, everything left
   on this list is polish or the App migration.
6. ~~**Phase 10 governance.**~~ **Done 2026-09-02 — the visible slice, proved live.**
   Postgres RLS on `fleetguard_vehicle`, `ENABLE` **and** `FORCE` (owner cannot bypass it
   either — plain `ENABLE` alone would have let the table owner's own connection skip the
   policy entirely). Fail-open by design: no row in `fleetguard_depot_assignment` = full
   access, unchanged from before this existed; an assignment restricts to that depot, below
   the application. **Proved, not configured** — toggled this identity's own assignment row
   and measured real row counts under both states, including the join through
   `fleetguard_vehicle_exposure` (the console never queries `fleetguard_vehicle` alone, so an
   unjoined policy would protect nothing real). Re-verified independently after the run:
   `relrowsecurity=True`, `relforcerowsecurity=True`, assignment table empty, unrestricted
   count back to 20,000.
   **Original design needed a rewrite mid-flight:** the plan called for a purpose-made
   Postgres role to prove restriction under a genuinely different identity — this account has
   no `CREATEROLE` on the shared Lakebase instance, correctly restricted on infrastructure
   shared with ~296 other students. `FORCE ROW LEVEL SECURITY` made the proof work under this
   identity's own connection instead, and is the better result regardless of the blocker: it
   closes a real gap (owner-bypass) the original design would have left open.
   **Not the full matrix, and said so:** no principal is currently enrolled, so every real
   caller is on the fail-open path today. The mechanism is real and proved; enrollment is
   future work, stated plainly in `scoping.py` rather than implied away.

### The public deployment, as it now stands

**https://fleetguard-console-abhi.onrender.com** — `auth_mode: app-login`,
`data_mode: snapshot`, snapshot captured **2026-08-31** (verified still current as of
2026-09-02 — no new data upstream, see "Next" above). Anonymous: Evidence, as the landing page
(I-057). Signed in with GitHub: queue (60 campaigns), Emerging (48 signals, 2 fleet-relevant),
campaign detail. Approval returns **501** by design — the surface has no credential to write
with, and simulating the write would be worse than refusing.

**Do not re-run `export_demo_snapshot.py` in isolation** — it stamps a fresh `captured_at`
that the UI shows to viewers as when data was captured. Re-run it only as the last step of a
real ingest → rebuild → reload chain (see "Next", item 3), never on its own to make the date
look current.

Three env vars live only in the Render dashboard, never committed: `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, `FLEETGUARD_APPROVERS`.

### Things that are true and easy to forget

- **The endpoint serves version 6, one entity, `scale_to_zero_enabled: True`** (2026-09-08).
  To retire it: delete the endpoint; to bring it back, `fleetguard-deploy-agent`
  (job `602170435434673`) with `model_version=6`. The build job is
  `fleetguard-agent-build` (`702635705382`), and it runs a **workspace notebook** — a local
  commit changes nothing until `databricks workspace import ... --overwrite` syncs it.
- **`agents.deploy()` resets `scale_to_zero_enabled` to `False` on every deploy**, whatever it
  was before. Observed twice. Re-enabling it is part of deploying, not a follow-up — otherwise
  a redeploy silently restarts continuous billing.
- **`serving_endpoints.query()` cannot read this agent** (I-078). It parses into a
  chat/completions dataclass, so a `ResponsesAgent`'s `output` items are dropped and a healthy
  endpoint looks like it returned nothing. `POST /serving-endpoints/<name>/invocations`.
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

### Verification discipline — earned ten times (I-021, I-043, I-050, I-051, I-054, I-055, I-056, I-057, I-058, I-060)

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
