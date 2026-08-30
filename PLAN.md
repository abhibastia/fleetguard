# FleetGuard — Build Action Plan

Turns the delivery plan in `docs/FleetGuard_Proposal.md` (§11) into concrete, sequenced,
verifiable work. Grounded in what was already confirmed live before build start (see
"Starting point" below) — not re-derived from scratch.

## Starting point — already done

- `free-edition` workspace: `fleetguard` catalog → `raw` schema → `nhtsa_flat_files`
  volume exists, with two real NHTSA files already landed and parsed clean through
  `read_files` (0 rescued rows).
- All 6 data sources confirmed live: 4 flat files (correct URLs), Recalls API, vPIC API.
- Architecture, identity model, and quality-control design are finalized and graded
  (100/100, see `docs/feedback-final-proposal-fleetguard.pdf`).

## Two things to resolve before committing to automation choices

1. **Does Lakebase CDF have Asset Bundle support yet?** If not, Phase 5's setup is a
   manual/scripted step outside `bundle deploy`, and CI/CD (Phase 6) needs to account
   for that gap explicitly rather than assume it away.
2. **Does `static.nhtsa.gov` return `Last-Modified` on a `HEAD` request?** One `curl -I`
   settles whether cheap daily change-detection on the flat files is viable before
   Phase 1's ingestion job is built around an assumption.

## Phases

### Phase 1 — Ingestion + bronze/silver/gold
- Lakeflow Job: download 4 flat files daily, land in `/Volumes/fleetguard/raw/`,
  separate checkpoint/schema paths.
- Auto Loader → bronze (schema evolution, `_rescued_data`, Delta CDF on).
- Silver: dedup on ODI number, `ai_extract` for defect/component fields, PII
  tag-not-delete, `expect_or_drop` → quarantine.
- Chunking → `complaint_chunk` (512-token).
- **Done when:** all 4 files flow bronze→silver→gold on a schedule with 0 unexplained
  quarantine rows on a full run.

### Phase 2 — Fleet registry
- Generate synthetic 20,000-vehicle/60-depot roster; decode every VIN against live vPIC.
- **Done when:** roster table populated, 100% of VINs resolve against vPIC with no
  fabricated make/model/year.

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

### Phase 5 — Lakebase schema + CDF
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

### Phase 9 — Model A + lead-time backtest
*(The differentiating capability — protect this phase's time budget.)*
- HDBSCAN over embeddings + volume-anomaly scoring.
- Backtest: detection date vs. ODI investigation open date on held-out historical
  recalls.
- **Done when:** a real (possibly negative) lead-time number exists and is published
  as-is.

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

### Phase 12 — Optional: second connector (CPSC/FSIS)
- Only if 1–11 are solid with time to spare.

## Sequencing

Phases 1→3→4 are a critical path (each needs the last). Phase 2 and Phase 5 can run
in parallel with 1–4. Phases 6–8 depend on 5 being done. Phase 9 depends on 3 and 4.
Phase 10 can start as soon as any pipeline exists and grow incrementally. Phase 11 is
last by definition.
