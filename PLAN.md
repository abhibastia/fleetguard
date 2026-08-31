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
2. **`static.nhtsa.gov` conditional requests: YES, with a trap.** `HEAD` returns both
   `Last-Modified` and `ETag`. `If-Modified-Since` works (`304`, 0 bytes).
   `If-None-Match` with the exact advertised ETag returns `200` and the full body —
   the ETag is published and ignored. Build change detection on `If-Modified-Since` only.

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

### Phase 3 — Chunking + AI Search
- Build the Delta Sync Index over `complaint_chunk` (`embedding_source_column`,
  hybrid ANN+BM25).
- **Done when:** a hybrid query returns component-code exact matches AND semantically
  related narratives in the same result set.

### Phase 4 — Model B + golden set + MLflow harness
- Build the 150-pair golden set as a `mlflow.genai.datasets` UC dataset.
- Train the recall-match classifier, tune threshold for recall.
- Wire `mlflow.genai.evaluate()` with `Correctness`/`RetrievalGroundedness`/`Safety`
  for the agent/retrieval path, separately from classical precision/recall for Model B.
- **Done when:** precision/recall numbers exist and are real, not placeholders.

### Phase 5 — Lakebase schema + CDF  🟡 ROUND-TRIP PROVEN (2026-08-31)
*Naming `fleetguard_<entity>` → `lb_fleetguard_<entity>_history` (I-036). Destination
`bootcamp_students.bootcamp_cdc`, authorised.*

**Done-when MET on the first table.** `fleetguard_depot` created with `REPLICA IDENTITY
FULL`; 60 inserts, 1 update, 1 delete appeared in
`lb_fleetguard_depot_history` as 60 `insert` / 1 `update_preimage` / 1 `update_postimage` /
1 `delete`, with all five metadata columns and **no collision suffix** (I-038).

**All 11 tables created** (2026-08-31), each verified `relreplident = 'f'`, owned by
`abhisek.bastia17@gmail.com`. Script is idempotent and transactional
(`src/lakebase/08_create_remaining_tables.py`).

*Outstanding:* populate the tables from gold, and a timed write to measure real capture
latency rather than citing the documented ~15 s.

- Create the 11-table Postgres schema, `REPLICA IDENTITY FULL` on every table.
- Enable Lakebase CDF at schema level (UI or API — resolved by the pre-work above).
- **Done when:** a test row written to Lakebase appears in `lb_<table>_history` with
  correct `_pg_change_type`/`_pg_lsn`.

### Phase 6 — OAuth wiring
- App resource bindings + OBO scopes for the primary surface; external service
  principal + `generate_database_credential()` pool for Render.
- **Done when:** a signed-in test user sees ABAC-scoped rows/columns in the App; the
  external surface reads only `public_summary`.

### Phase 7 — Agent tools + write path
- Build the 4 read + 4 write UC Function tools, `EXECUTE` grants for
  `fleetguard_agent_sp`.
- Wire the human approval gate on `launch_service_campaign()`.
- Put Unity AI Gateway in front of the serving endpoint (PII guardrail, usage
  tracking).
- **Done when:** an end-to-end agent turn — read → propose → gated write → audit_log
  row — works live.

### Phase 8 — Databricks App + external surface
- `app.yaml`, resource bindings, OBO console (signal queue, approval, work orders).
- Render: read-only `public_summary` page, `/health`, `/api/stats`.
- **Done when:** both surfaces are deployed and the App is the primary demoable
  workflow.

### Phase 9 — Model A + lead-time backtest  🟡 BASELINE DONE (2026-08-31)
*(The differentiating capability — protect this phase's time budget.)*

*Run early, out of sequence, deliberately:* the backtest needs only silver, so measuring it
in week 1 converted the project's largest risk from a week-4 discovery into week-1
knowledge. `gold_lead_time_backtest`, `gold_lead_time_control`, `gold_lead_time_summary`.

**Measured (volume-anomaly half only, no embeddings):** 16.0% detection at median 197-day
lead on 777 post-2010 investigations, against **11.1% on a volume-matched placebo**
(z ≈ 2.62, p ≈ 0.009). Real but modest — a 1.44× lift. Two earlier versions gave flattering
artefacts (409 days; 160× separation) and were discarded; see `docs/ISSUES.md` I-027.

**Conclusion that reshapes the plan:** volume anomaly alone does **not** carry the
differentiator. The semantic half is load-bearing, not an enhancement — which makes Phase 3
mandatory rather than optional.

- ✅ Volume-anomaly scoring + backtest harness with a control arm.
- ⬜ HDBSCAN over embeddings (needs Phase 3), then re-run the harness to measure the lift.
- **Done when:** a real (possibly negative) lead-time number exists and is published
  as-is. *A publishable floor already exists* — remaining work is to improve on it.

### Phase 10 — Governance
- Data Classification, ABAC row filters/column masks, DQ Monitors (including the
  Lakebase CDF lag monitor from §8.4), System Tables.
- **Done when:** DQ Monitoring dashboard shows live freshness/drift signals, not just
  config.

### Phase 11 — Deployment hardening
- `table_update` trigger wired to `lb_<table>_history` (per §8.3's YAML), Render
  always-on + pinger, seeded demo state.
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
