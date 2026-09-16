# FleetGuard console API

The FastAPI backend under `app/backend/fleetguard_api/` serves both the JSON API (this page)
and the built React console, from one process — see `main.py` and `docs/ARCHITECTURE.md` §8/§9
for why. Every route below is mounted under the `/api` prefix except `/healthz`, which is a
liveness check meant to work before any identity is established.

**This is a hand-written index for orientation, not the authoritative schema.** FastAPI
generates a live, always-accurate OpenAPI spec from the same code — run the backend locally
(`scripts/run_local_static_dev.sh`) and open `/docs` (Swagger UI) or `/redoc` for exact request
and response models, including every field these tables abbreviate.

**Auth.** Every route except `/healthz` and `/api/evidence` requires the caller's own
Databricks identity, arriving via `CurrentPrincipal` (`deps.py`) — Databricks Apps OBO in
production, a static dev token locally. Nothing here is queried as a service principal; see
`docs/ARCHITECTURE.md` §7/§8 for the identity model and why it matters for the write paths
below.

## Ops

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Unauthenticated liveness check — reports auth mode, data mode (live vs. snapshot), and whether the built console is present. |
| GET | `/api/me` | The caller's own identity and how it was obtained. Proves the auth seam end to end. |
| GET | `/api/auth/status` | What auth mode this deployment is running, for the console's own diagnostics. |

## Queue & campaign detail — the operator's primary surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/queue` | Recall campaigns ranked by fleet exposure — the landing view. |
| GET | `/api/campaigns/{campaign_id}` | One campaign's detail: exposed vehicles, match tier (`EXACT`/`MODEL_VARIANT`), depot breakdown. |

## Approval — the write path behind the human gate

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/campaigns/{campaign_id}/service-campaign` | Launch a service campaign: writes the campaign, one work order per exposed vehicle, and an audit row, all in one transaction. Gated by `FLEETGUARD_APPROVERS`; the agent has no path here (§7.1, full dispatch walkthrough §7.3). |
| GET | `/api/service-campaigns` | Recently launched service campaigns with a per-status work-order breakdown. |

## Work orders — closing the loop past "approve"

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/work-orders` | List work orders, filterable by service campaign, depot, or status. |
| PATCH | `/api/work-orders/{wo_id}` | Update status, technician assignment, or actual cost. |
| GET | `/api/cost-breakdown` | Aggregate actual cost by depot/status. |

## Assistant — the agent chat panel

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | Proxies a question to the deployed `ResponsesAgent` under the caller's own token, and executes any write the agent requested (`open_defect_signal`, `watch_campaign`) — see `agent_actions.py` and `docs/ARCHITECTURE.md` §7.1 for why the model itself never writes (tool reference: §7.2). |
| GET | `/api/watchlist` | Campaigns flagged via the agent's `watch_campaign` action — a plain read over what the write path committed. |

## Signals — the proactive half

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/signals` | Emerging defect signals: the batch z-score detector's rows plus any agent-opened (`open_defect_signal`) ones, fleet-relevant first. |

## Reference / roster

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/technicians` | The technician roster, optionally filtered by depot — what work-order assignment validates against. |
| GET | `/api/depot-risk` | Fleet-wide risk by depot — the view no single-campaign or single-work-order screen gives you. |
| GET | `/api/recall-trend` | Historical trend: recall campaigns hitting the fleet per year since 2014. |

## Audit & evidence

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/audit-log` | Every campaign launch, work-order change, and cost log, human-readable. |
| GET | `/api/audit-log/export.csv` | The same, as a CSV download. |
| GET | `/api/evidence` | The published backtest result. **Deliberately unauthenticated** — it serves a measured result about NHTSA data, not fleet or VIN data, so it works without a Databricks identity. |

## External APIs this project consumes

Distinct from the console API above — these are third-party sources FleetGuard's pipelines and
fleet registry pull from, not endpoints this project serves. See `CLAUDE.md`'s "Verified facts"
section for how each was confirmed live.

| API | Used for |
|---|---|
| `static.nhtsa.gov/odi/ffdd/{cmpl,rcl,inv,tsbs}/*.zip` | Complaint, recall, investigation, and TSB flat files — the corpus everything else is built on. Conditional fetch via `If-Modified-Since` only (the host advertises but ignores `If-None-Match`). |
| `api.nhtsa.gov/recalls/recallsByVehicle` | Per make/model/year recall lookup for the fleet registry. No conditional-request support — every poll returns the full body. |
| `vpic.nhtsa.dot.gov/api` (`DecodeVINValuesBatch`) | Authoritative make/model/year/body-class decode for every fleet VIN — never the dirty make/model strings on complaint records. |
