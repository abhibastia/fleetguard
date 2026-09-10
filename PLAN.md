# FleetGuard — Build Action Plan

Turns the delivery plan in `docs/FleetGuard_Proposal.md` (§11) into concrete, sequenced,
verifiable work. Grounded in what was already confirmed live before build start (see
"Starting point" below) — not re-derived from scratch.

> **For current status, read `docs/STATUS.md`** — one page covering phase progress,
> what exists in the workspace, measured results, cost, and open decisions. This file is
> the *plan*; STATUS is the *position*.

## Starting point — as of 2026-08-31

- **Workspace: `abhi` profile** (`dbc-7b106152-caf3.cloud.databricks.com`), a *shared*
  bootcamp metastore. Project schema is **`bootcamp_students.fleetguard`**, owned by
  `abhisek.bastia17@gmail.com`. Catalog creation is unavailable, so medallion layers are
  table-name prefixes (`bronze_`/`silver_`/`gold_`) in that one schema.
  **Never write outside it.**
- The `free-edition` volume and its test files do **not** carry over. Nothing is
  provisioned in `abhi` beyond the schema.
- Compute: one serverless SQL warehouse, `Serverless Starter Warehouse`
  (`b15d3d6f837ba428`, 2X-Small).
- All 6 data sources confirmed live; full corpus downloaded and measured locally
  (counts in `CLAUDE.md` — use those, don't re-estimate).
- Architecture, identity model, and quality-control design finalized and graded
  (100/100, see `docs/feedback-final-proposal-fleetguard.pdf`), then corrected against
  measured data on 2026-08-31.

## Pre-work — both resolved

1. **Lakebase CDF bundle support: NO.** Bundle support for Lakebase is Beta and covers
   projects/branches/endpoints/roles/databases/synced_tables/catalogs — CDF is not a
   bundle resource. Phase 5 enablement is a manual runbook step, and Phase 6 CI/CD must
   say so rather than assume `bundle deploy` covers it.
   *Still true, and now one of **four** exceptions the bundle does not cover — see Phase 11
   and `docs/ARCHITECTURE.md` §9.1. The bundle itself was built 2026-09-10.*
2. **`static.nhtsa.gov` conditional requests: YES, with a trap.** `HEAD` returns both
   `Last-Modified` and `ETag`. `If-Modified-Since` works (`304`, 0 bytes).
   `If-None-Match` with the exact advertised ETag returns `200` and the full body —
   the ETag is published and ignored. Build change detection on `If-Modified-Since` only.

## MVP target — 7 September 2026

One vertical slice end to end: recall → ranked exposure → human approval → work orders in
Lakebase → CDF → browser. Phases 6, 7 and 8 are cut to the minimum that achieves it; full
scope for each remains below and resumes after the 7th. Definition, in/out lists and cut
order live in `docs/ENHANCEMENTS.md`.

**MVP blocker to resolve first:** the `gold_fleet_exposure` load scope (989,042 rows) —
without it there is no work queue.

## Phases

### Phase 1 — Ingestion + bronze/silver/gold  🟡 ~85% (2026-08-31)
*Done:* ingest job (4 files landing, `If-Modified-Since` verified working — `FLAT_CMPL`
returns 304 and skips 370 MB), bronze (4 tables, 8.44M rows, 0 rescued, cardinality-checked),
silver (9 tables, `bronze = silver + quarantine` reconciles exactly on every table).
*Outstanding:* chunking → `complaint_chunk`; 2 of 5 gold tables blocked on Phases 3 and 9
(`gold_emerging_cluster` needs Model A, and the remaining scope tables follow it).
*Deliberate deviation from "on a schedule":* the ingest job is **manual by choice** so it
does not consume shared-workspace compute before the demo window. Schedule it in Phase 11.

- Lakeflow Job: download 4 flat files daily, land in
  `/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/`, separate
  checkpoint/schema paths. Use `If-Modified-Since` for change detection —
  **not** `ETag`, which the host advertises and ignores.
- Auto Loader → `bronze_*` (schema evolution, `_rescued_data`, Delta CDF on).
  All objects live in `bootcamp_students.fleetguard`; medallion layers are
  table-name prefixes, not schemas.
- Silver: dedup on ODI number, `PROD_TYPE` branch keeping `V`+`T` only, harm-field
  typing, PII tag-not-delete, `expect_or_drop` → quarantine. Component comes from
  `COMPDESC` — **no `ai_extract` in silver** (it runs per surfaced cluster in Phase 9).
- Chunking → `complaint_chunk` (512-token).
- **Done when:** all 4 files flow bronze→silver→gold on a schedule with 0 unexplained
  quarantine rows on a full run.

### Phase 2 — Fleet registry  ✅ DONE (2026-08-31)
- `gold_fleet_vehicle` (20,000) + `gold_fleet_depot` (60) + `gold_fleet_exposure`.
- VIN prefixes sampled from real complaint VINs, check digit recomputed, make/model/year
  taken from live vPIC — never asserted locally.
- **Done-when met:** 400 randomly sampled generated VINs verified independently against
  live vPIC — 400/400 exact match on make, model and year, 0 failures.
- Segment mix 45% pickup / 40% van / 15% Class 7-8, spanning GVWR Class 1D→8, 47 models.

### Phase 3 — Chunking + AI Search  ✅ DONE (2026-09-01)
*Index `complaint_chunk_idx` on endpoint `fleetguard-vs`, **1,746,601 chunks, `ready:
true`** — matching `silver_complaint_chunk_indexed` exactly. ~7 h to build.*

**Done-when MET, verified at full corpus** (`ops_hybrid_query_test`). The earlier check ran
against a 42%-built index and was re-run before being quoted:

| query | ANN vs HYBRID |
|---|---|
| component code + symptom | **differs** — hybrid surfaces `FOUNDATION COMPONENTS:HOSES, LINES/PIPING` |
| pure paraphrase | **differs** — hybrid finds `ENGINE`/`VEHICLE SPEED CONTROL`; ANN drifts to `AIR BAGS` |
| `TAKATA airbag inflator` | identical — both saturate on `AIR BAGS` |

Harm-filtered retrieval (§4.3) **10/10 PASS**. Near-duplicate audit **10/10 distinct
`complaint_id`** — the sibling-chunk concern (I-023) does not materialise at full corpus,
so the agent's search tool does not need read-time dedupe.

*Cost note:* `fleetguard-vs` is the project's only recurring charge, ~$6.72/day. Billing
stops 24 h after the last index is deleted.

- Build the Delta Sync Index over `complaint_chunk` (`embedding_source_column`,
  hybrid ANN+BM25).
- **Done when:** a hybrid query returns component-code exact matches AND semantically
  related narratives in the same result set.

### Phase 4 — Model B + golden set + MLflow harness ✅ DONE (2026-09-02)
*Built per `docs/ENHANCEMENTS.md` E-08 (synthetic evals for the **agent only** — Model B's
golden set is real, not generated). E-05/E-06/E-07 as originally scoped for the agent are
separately covered by `16_evaluate_agent.py`; this phase's own MLflow harness is classical
precision/recall, not `mlflow.genai.evaluate()`, since Model B is a classifier, not a
generative model.*

**Golden set — 765 pairs, not a `mlflow.genai.datasets` UC dataset as originally planned.**
Labels are derived from NHTSA's own `defect_description` recall-scope text (real regulatory
language: *"Ford is recalling certain 2022 Super Duty F-250, F-350…"*), not from a live
labeling session — the plain `mlflow.genai.datasets` path fits generative eval rows, not a
binary match/no-match label with provenance attached, and text-derivation gave 765 usable
pairs (621 positive / 144 negative / 69 excluded as ambiguous) versus a 150-pair target.
Spot-checked by hand, including the case that motivated the whole approach: `RAM PROMASTER
CITY` vs recall model `PROMASTER` correctly labelled negative — different platforms, and
the recall text names only "ProMaster vans". See `src/fleet/05_build_model_b_golden_set.py`.

**Classifier — gradient-boosted, isotonic-calibrated, threshold tuned for recall (0.90
target).** Features are string-similarity metrics over the *structured* `model`/`recall_model`
fields only — deliberately never the `defect_description` text the labels were derived from.
`src/fleet/06_train_model_b.py`.

**I-060 — leakage found and fixed before reporting.** The first run scored an implausible
precision=1.000 at threshold=1.000. Traced to the golden set's own negative-label rule
requiring `recall_model NOT LIKE '%model%'` — making that boolean tautologically 0% for every
negative by construction, not by anything learned. Removed from the feature set; retrained.
**Real, reported numbers: precision 83.7%, recall 96.3%, ROC-AUC 0.925** — the number this
project stands behind.

**Done when: met.** Precision/recall are real, logged to MLflow (not placeholders), and
published on the evidence page per the proposal's own requirement (§6) —
`GET /api/evidence` → `model_b`, sourced by `scripts/export_evidence.py`, never hand-typed.

### Phase 5 — Lakebase schema + CDF  ✅ DONE (2026-09-01)
*Reference tables loaded and CDF measured end to end. Only the bulk exposure load remains,
and that is a **scope decision**, not unfinished plumbing — see "Outstanding" below.*

**Loaded from gold**, each reconciling exactly against source: `fleetguard_depot` 60,
`fleetguard_vehicle` 20,000 (0.5 s, ~40k rows/s), `fleetguard_recall_campaign` 592.

**CDF replicated all of it**, and depot's history reconciles arithmetically to the I-038
test: 61 `insert` / 60 `update_preimage` / 60 `update_postimage` / 1 `delete` = the original
60 inserts, plus 59 upsert-conflicts, plus the one previously-deleted row re-inserted. That
single check confirms `ON CONFLICT DO UPDATE` **and** `REPLICA IDENTITY FULL`.

**All 11 history tables exist with exact names and no `_1` collision suffixes** — CDF
replicates DDL, so they appeared when the `CREATE TABLE`s committed rather than on first
write (I-044 corrects the opposite claim). The naming risk (I-036) is retired for every
table, not just the one round-tripped.

**Capture latency MEASURED** (`ops_cdf_latency`): **7.1 – 15.6 s**, mean 12.5 s, 3/3 true
measurements. Report as a range consistent with a ~15 s flush, never one averaged number;
size demos against the 15.6 s worst case. Supports §8.3's sub-minute claim. I-046 records
why the first attempt (21.55 s) was an upper bound, not a measurement.

### Phase 5 (original definition) — Lakebase schema + CDF
*Naming `fleetguard_<entity>` → `lb_fleetguard_<entity>_history` (I-036). Destination
`bootcamp_students.bootcamp_cdc`, authorised.*

**Done-when MET on the first table.** `fleetguard_depot` created with `REPLICA IDENTITY
FULL`; 60 inserts, 1 update, 1 delete appeared in
`lb_fleetguard_depot_history` as 60 `insert` / 1 `update_preimage` / 1 `update_postimage` /
1 `delete`, with all five metadata columns and **no collision suffix** (I-038).

**All 11 tables created** (2026-08-31), each verified `relreplident = 'f'`, owned by
`abhisek.bastia17@gmail.com`. Script is idempotent and transactional
(`src/lakebase/08_create_remaining_tables.py`).

*Outstanding:* the bulk `gold_fleet_exposure` load (989,042 rows) into
`fleetguard_vehicle_exposure`. Postgres `COPY` is not the constraint; the open question is
what ~1M change events do to a CDF pipeline **shared with ~296 other students**. Decide
scope — full, `EXACT`-only (263,686), or a demo slice — before running. Re-measure
throughput at scale: the `PSYCOPG_IMPL=python` fix (I-045) uses the slower pure-Python
driver, so the 40k rows/s reference figure may not hold.

- Create the 11-table Postgres schema, `REPLICA IDENTITY FULL` on every table.
- Enable Lakebase CDF at schema level (UI or API — resolved by the pre-work above).
- **Done when:** a test row written to Lakebase appears in `lb_<table>_history` with
  correct `_pg_change_type`/`_pg_lsn`.

### Phase 6 — OAuth wiring
*See `docs/ENHANCEMENTS.md` E-01 — the AI Gateway must sit on our own pay-per-token LLM
endpoint, not the agent endpoint, which supports inference tables only. Verify live first.*
- App resource bindings + OBO scopes for the primary surface.
- **Done when:** a signed-in test user sees ABAC-scoped rows/columns in the App.

*Amended 2026-09-10: the second half of this phase — an external service principal and a
`generate_database_credential()` pool for a public Render surface — was dropped with Render.
Both surviving surfaces mint the Lakebase credential from the **caller's** token, so there is
no app-owned pool to build. See `deploy/render` for what it looked like.*

### Phase 7 — Agent tools + write path
*Build per `docs/ENHANCEMENTS.md` E-02 (`ResponsesAgent`, models-from-code,
`agents.deploy()`), E-03 (domain trace spans — `fleetguard_agent_action.trace_id` already
exists for this), E-04 (inference tables), E-09 (LangGraph, chosen for interrupt/resume at
the human approval gate).*
- Build the 4 read + 4 write UC Function tools, `EXECUTE` grants for
  `fleetguard_agent_sp`.
- Wire the human approval gate on `launch_service_campaign()`.
- Put Unity AI Gateway in front of the serving endpoint (PII guardrail, usage
  tracking).
- **Done when:** an end-to-end agent turn — read → propose → gated write → audit_log
  row — works live.

### Phase 8 — Databricks App + external surface
*Hosting decided (E-12): **Render** through MVP, **Databricks Apps** deployed ~20 Sept and
kept `STOPPED` between sessions, live demo on the App. Free-edition Apps rejected — separate
workspace/account, resource bindings are workspace-local, and §5.1's ABAC guarantee needs the
user to be an `abhi` identity. **The auth seam (E-13) is MVP scope**: one swappable token
provider, no handler reading headers directly.*

*Superseded 2026-09-10 — the Render half is gone. The two surfaces are now **Databricks Apps**
and a **local server**; the auth seam narrowed to `databricks-apps` and `static-dev`. The seam
itself was the right call and is what made this a config-sized change rather than a rewrite.*
- `app.yaml`, resource bindings, OBO console (signal queue, approval, work orders).
- **Done when:** the App is deployed and is the primary demoable workflow.

### Phase 9 — Model A + lead-time backtest  🟡 BASELINE DONE (2026-08-31)
*(The differentiating capability — protect this phase's time budget.)*

*Run early, out of sequence, deliberately:* the backtest needs only silver, so measuring it
in week 1 converted the project's largest risk from a week-4 discovery into week-1
knowledge. `gold_lead_time_backtest`, `gold_lead_time_control`, `gold_lead_time_summary`.

**Measured (volume-anomaly half only, no embeddings):** 16.0% detection at median 197-day
lead on 777 post-2010 investigations, against **11.1% on a volume-matched placebo**
(z ≈ 2.62, p ≈ 0.009). Real but modest — a 1.44× lift. Two earlier versions gave flattering
artefacts (409 days; 160× separation) and were discarded; see `docs/ISSUES.md` I-027.

~~**Conclusion that reshapes the plan:** volume anomaly alone does not carry the
differentiator. The semantic half is load-bearing, not an enhancement — which makes Phase 3
mandatory rather than optional.~~

**SUPERSEDED 2026-09-01 by the experiment it motivated (I-049).** The semantic half was
built and measured, and it does **not** improve detection — it made it worse, with zero
lead-time gain on shared detections. The volume-anomaly result is therefore not a floor
awaiting improvement; it is **the result**. Phase 3 remains justified, but by *retrieval*
for the agent's search tool (§4.3), not by clustering for early detection.

- ✅ Volume-anomaly scoring + backtest harness with a control arm.
- ✅ Semantic arm built and measured (2026-09-01). HDBSCAN was **abandoned** — it labelled
  85% of embeddings noise and no parameterisation fixed it (I-048); replaced with k-means
  subdivision inside each existing series.
- ❌ **The semantic hypothesis is FALSIFIED (I-049).** Detection *fell* 13.3% → 11.2%, lift
  unchanged at 1.24× → 1.26×, and on the 70 investigations both groupings detect,
  subdivision produced **0.0 days** of extra lead time. That zero is decisive: had the
  mechanism worked and merely been outweighed by fragmentation, shared detections would
  still fire earlier. They do not. The mechanism did not operate.
- **DONE — done-when MET.** A real lead-time number exists and is published as-is; it
  happens to be negative, which the definition of done explicitly anticipated.

**Published result — the differentiator:** **16.0% detection at median 197-day lead vs
11.1% on a volume-matched placebo (1.44×, z ≈ 2.62, p ≈ 0.009)**, from the volume-anomaly
detector on the full silver corpus. Modest, real, falsifiable, and defended by a control
arm. *Do not quote the 13.3%/11.2% pair as the headline* — those are the like-for-like
comparators computed on the restricted 37-month embedded set, and exist only to make the
v2-vs-v3 comparison valid.

**Phase 3 is not wasted by this.** Hybrid retrieval is verified and load-bearing for the
agent's search tool (§4.3) — retrieval and clustering are different uses of the same
embeddings. What is retired is only the claim that semantic clustering improves early
detection.

### Phase 10 — Governance ✅ VISIBLE SLICE DONE (2026-09-02); full matrix cut for schedule
*Cut to a visible slice on 2026-08-31 (see Phase 12's note): Postgres RLS on depot scoping,
proved once, not the full Data Classification / ABAC / DQ Monitors / System Tables matrix.*

**Postgres RLS live on `fleetguard_vehicle`** — `ENABLE` **and** `FORCE ROW LEVEL SECURITY`,
additive/fail-open via `fleetguard_depot_assignment` (no assignment row = unrestricted,
unchanged from before). `src/lakebase/15_enable_depot_rls.py`.

**Proved under real restricted and unassigned states, not trusted on config alone** — the
notebook toggles this identity's own assignment row and measures actual row counts,
including the join through `fleetguard_vehicle_exposure` the console actually reads (RLS on
`fleetguard_vehicle` alone protects nothing if that join isn't also filtered — checked, and
it is). Independently re-verified after the run: `relrowsecurity=True`,
`relforcerowsecurity=True`, assignment table empty, unrestricted count back to 20,000.

**Design detour, and why:** the original design created a purpose-made Postgres role to
prove restriction under a genuinely different identity. This account has no `CREATEROLE` on
the shared Lakebase instance — correctly restricted, on infrastructure shared with ~296
other students. `FORCE ROW LEVEL SECURITY` made the proof possible under this identity's own
connection instead, and is a strictly better result: without `FORCE`, Postgres exempts table
owners from RLS by default, which would have made "the frontend cannot bypass it" false for
any owner-connected caller regardless of policy content.

**Done when: met, for the reduced scope.** No principal is currently enrolled in
`fleetguard_depot_assignment`, so every real caller is on the fail-open path today — the
mechanism exists and is proved; nobody has been assigned through it yet. That is stated
plainly in `scoping.py`'s own docstring, not left implicit.

### Phase 11 — Deployment hardening
- `table_update` trigger wired to `lb_<table>_history` (per §8.3's YAML), seeded demo state.
  *(The "Render always-on + pinger" item was dropped when Render stopped being the demo
  surface, and removed here with Render itself on 2026-09-10.)*
- ✅ **Declarative Automation Bundle, 2026-09-10.** `databricks.yml` + `resources/` now
  describe the App, the bronze/silver pipeline, the dashboard and 17 of the 24 jobs, all bound
  to the existing objects. This closes the §8.5/§9 promise that had been in the proposal since
  the start and was never built — and it found what the gap had cost: **8 of 16 job notebooks
  were running code behind `main`** (I-096), two of them with bugs that had already been fixed
  and shipped elsewhere. Four exceptions remain and are documented rather than papered over:
  Lakebase CDF, AI Search endpoint/index, the agent serving endpoint, the metric view.
- **Done when:** the full pipeline survives an idle-then-cold-start cycle without
  manual intervention.

### Phase 12 — Optional: second connector (CPSC/FSIS)  ❌ CUT (2026-08-31)
*Dropped deliberately for schedule, not abandoned by neglect. With a 25–30 Sept demo and
eight phases remaining, this is the correct first sacrifice — the proposal itself names it
as such (§11). Also cut: Feature Store online serving, Genie Agent, Unity AI Gateway;
governance reduced to a visible slice rather than the full matrix.*

## Sequencing

Phases 1→3→4 are a critical path (each needs the last). Phase 2 and Phase 5 can run
in parallel with 1–4. Phases 6–8 depend on 5 being done. Phase 9 depends on 3 and 4.
Phase 10 can start as soon as any pipeline exists and grow incrementally. Phase 11 is
last by definition.
