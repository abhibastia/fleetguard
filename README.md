# FleetGuard

Vehicle defect early-warning and recall-response platform for commercial fleets, built on
Databricks (Lakeflow, Lakebase, Unity Catalog, Model Serving, AI Search) against NHTSA's
public defect corpus.

A fleet operator running thousands of vehicles today learns about safety defects the same
way a private owner does — when a recall posts. FleetGuard gives them two capabilities
instead of one:

- **Reactive — recall response.** A campaign posts; FleetGuard resolves its scope against
  the fleet's VIN roster, ranks exposure by depot and severity, and issues work orders under
  human approval — from recall to dispatched work order in seconds, not the days a manual
  cross-reference takes.
- **Proactive — early warning.** Detect a complaint pattern before NHTSA opens a formal
  investigation into it. **Measured, not projected: when it fires, a median 197-day lead on
  the investigation opening — but it only fires on 16.0% of investigations, against 11.1% on
  a volume-matched placebo control (1.44× lift, z ≈ 2.62, p ≈ 0.009).** A narrow, real edge on
  a minority of cases, not a general early-warning net. The system predicts that an
  investigation will open — not that a recall will be issued, and not which VINs are
  affected; see `docs/ARCHITECTURE.md` §1 for the precise, deliberately narrow claim.

![FleetGuard end-to-end architecture](docs/fleetguard_e2e_current.png)

*As-built, tracks `docs/ARCHITECTURE.md`. See also the [identity & authorisation diagram](docs/fleetguard_identity_current.png) and the [frozen 2026-08-31 proposal diagrams](docs/FleetGuard_Proposal.md) for what changed and why.*

## Live demo

**Primary — Databricks App:** `fleetguard-console` (real per-user OBO — Unity Catalog and
Postgres RLS enforce under the caller's own Databricks identity, not a simulation). Kept
stopped between sessions to avoid idle cost; ask if you want it started. **Verified end to
end under the owner's identity only** — a second real identity has not yet signed in, so
treat that path as unconfirmed for anyone else until it has (`docs/STATUS.md`, I-084).

**Fallback — Render:** https://fleetguard-console-abhi.onrender.com — kept live, not the
plan of record. Runs against live Lakebase via `render-u2m` (Databricks OAuth; the browser
login round-trip is itself unconfirmed) or against a committed snapshot via GitHub sign-in.
The **Evidence** tab needs no sign-in on either surface — it's the published backtest result
above, sourced live from the same measurement.

## What it's built on

- **Ingestion → medallion pipeline** (Lakeflow Declarative Pipelines): NHTSA's complaint,
  recall, investigation, and TSB flat files → bronze → silver → gold, with a quarantine
  split so `bronze = silver + quarantine` reconciles exactly at every layer.
- **Semantic retrieval**: Databricks AI Search over 1.7M+ complaint narrative chunks,
  hybrid (BM25 + embedding) search.
- **A registered, deployed agent** (Mosaic AI Agent Framework, Model Serving): six tools —
  five read (complaint search, fleet exposure, fleet vocabulary lookup, emerging-signal
  lookup, campaign proposal) and one real write (`open_defect_signal`, executed by the app
  under the caller's own identity, never by the model) — traced with MLflow, evaluated
  against a held-out golden set built from NHTSA's own recall text.
- **Lakebase Postgres** as the operational store for fleet state (vehicles, depots, service
  campaigns, work orders, audit log) — Change Data Feed replicates every write into Unity
  Catalog within seconds, and Postgres Row-Level Security enforces depot-scoped reads below
  the application, not just in it.
- **A FastAPI + React console**, one service for API and UI, running unchanged across
  Render and Databricks Apps behind a single auth seam (`app/backend/fleetguard_api/auth/`).

## Run it locally

```bash
scripts/run_local_static_dev.sh          # starts the backend at :8811 against live Lakebase
```

See `app/backend/README.md` for what this sets up and why every one of its environment
variables is there. Frontend development lives in `app/frontend/` (`npm run dev`); rebuild
the console the backend serves with `scripts/build_console.sh`.

## Repository layout

| Path | What's there |
|---|---|
| `src/` | Ingestion, medallion pipelines, fleet registry, search, agent, backtest, Lakebase migrations |
| `app/` | The FastAPI backend + React console that make up the live product |
| `docs/` | Living architecture spec, build status, issue log, and the frozen original proposal |
| `scripts/` | Runnable setup/build scripts — local dev server, console build, evidence/snapshot export |
| `tests/` | Unit tests (run everywhere) and integration tests (opt-in, hit the live workspace) |
| `dashboards/` | AI/BI dashboard definitions |

## Documentation map

Read `docs/STATUS.md` first if you're picking this up cold — it's the one page answering
"where are we," with next steps in priority order.

| Document | Job |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The living spec — what the system *is*, kept true with the code |
| [`docs/STATUS.md`](docs/STATUS.md) | Where the build has got to, updated every session |
| [`docs/ISSUES.md`](docs/ISSUES.md) | Every problem hit during the build, root cause, resolution — append-only |
| [`docs/ENHANCEMENTS.md`](docs/ENHANCEMENTS.md) | Evaluated backlog: adopted, deferred, or rejected, with reasons |
| [`docs/FleetGuard_Proposal.md`](docs/FleetGuard_Proposal.md) | What was proposed, before the build — **frozen**, not updated as facts changed |
| [`PLAN.md`](PLAN.md) | Phase sequencing and definitions of done |
