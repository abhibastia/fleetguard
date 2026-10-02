# FleetGuard console API

The FastAPI backend under `app/backend/fleetguard_api/` serves both the JSON API (this page)
and the built React console, from one process — see `main.py` and `docs/ARCHITECTURE.md` §8/§9
for why. Every route below is mounted under the `/api` prefix except `/healthz`, which is a
liveness check meant to work before any identity is established.

**This is a hand-written index for orientation, not the authoritative schema.** FastAPI
generates a live, always-accurate OpenAPI spec from the same code — run the backend locally
(`scripts/run_local_static_dev.sh`) and open `/docs` (Swagger UI) or `/redoc` for exact request
and response models, including every field these tables abbreviate.

**Auth.** Every route except `/healthz`, `/api/evidence` and `/api/corpus` requires the caller's own
Databricks identity, arriving via `CurrentPrincipal` (`deps.py`) — Databricks Apps OBO in
production, a static dev token locally. Nothing here is queried as a service principal; see
`docs/ARCHITECTURE.md` §7/§8 for the identity model and why it matters for the write paths
below.

## Ops

| Method | Path               | Purpose |
| ------ | ------------------ | ---------- |
| GET    | `/healthz`         | Unauthenticated **liveness** check — reports auth mode, data mode (live vs. snapshot), and whether the built console is present. Always 200 while the process is up. |
| GET    | `/api/readyz`      | **Readiness** — a different question: *would the demo work right now?* Five checks — Lakebase, the agent serving endpoint, the AI Search index (**including `indexed_row_count` against the source table**, because an index can be `ready` and short), the two committed snapshots, and release provenance (served agent version + console git SHA). Each reports its own status, detail and latency. **200 only if all pass; 503 otherwise**, with the same body either way. |
| GET    | `/api/me`          | The caller's own identity and how it was obtained. Proves the auth seam end to end. |
| GET    | `/api/auth/status` | What auth mode this deployment is running, for the console's own diagnostics. |

**Why both.** `/healthz` returned `ok` for weeks with the index deleted, the agent stopped and
Lakebase unreachable — it only ever checked the process, so it could not tell anyone whether
the system worked (I-115). `/readyz` answers that, and is **authenticated** because its Lakebase
check has to run under the caller's token: this app holds no privileges of its own (§8a), so
proving the *app's* access would test something the product does not do. That also means
`/readyz` cannot serve as a container probe, which is fine — `/healthz` is, and it is unchanged.

**It is free to call.** The agent and index are checked with `serving-endpoints get` and
`get-index`, control-plane reads. Querying the agent instead would wake a scale-to-zero
container and bill until it idled down — the one thing a readiness probe must not do. The one
exception is the index row count, which needs a SQL warehouse; it degrades to a note rather
than failing readiness, because a warehouse scaled to zero is not a broken index.

**It is also the release preflight.** *"Is the live system the thing in the zip?"* was named by
review as the biggest practical risk, and answering it meant looking in five places. The
`release` check reports two of them — served agent version and the git SHA the console was
built at — so the question is one authenticated URL rather than a runbook (I-117).

## Queue & campaign detail — the operator's primary surface

| Method | Path                           | Purpose |
| ------ | ------------------------------ | ---------- |
| GET    | `/api/queue`                   | Recall campaigns ranked by fleet exposure — the landing view. |
| GET    | `/api/campaigns/{campaign_id}` | One campaign's detail: exposed vehicles, match tier (`EXACT`/`MODEL_VARIANT`), depot breakdown. |

## Approval — the write path behind the human gate

| Method | Path                                            | Purpose |
| ------ | ----------------------------------------------- | ---------- |
| POST   | `/api/campaigns/{campaign_id}/service-campaign` | Launch a service campaign: writes the campaign, one work order per exposed vehicle, and an audit row, all in one transaction. Gated by `FLEETGUARD_APPROVERS`; the agent has no path here (§7.1, full dispatch walkthrough §7.3). |
| GET    | `/api/service-campaigns`                        | Recently launched service campaigns with a per-status work-order breakdown. |

## Work orders — closing the loop past "approve"

| Method | Path                       | Purpose |
| ------ | -------------------------- | ---------- |
| GET    | `/api/work-orders`         | List work orders, filterable by service campaign, depot, or status. |
| PATCH  | `/api/work-orders/{wo_id}` | Update status, technician assignment, or actual cost. |
| GET    | `/api/cost-breakdown`      | Aggregate actual cost by depot/status. |

## Assistant — the agent chat panel

| Method | Path             | Purpose |
| ------ | ---------------- | ---------- |
| POST   | `/api/chat`      | Proxies a question to the deployed `ResponsesAgent` under the caller's own token, and executes any write the agent requested (`open_defect_signal`, `watch_campaign`) — see `agent_actions.py` and `docs/ARCHITECTURE.md` §7.1 for why the model itself never writes (tool reference: §7.2). |
| GET    | `/api/watchlist` | Campaigns flagged via the agent's `watch_campaign` action — a plain read over what the write path committed. |

## Signals — the proactive half

| Method | Path           | Purpose |
| ------ | -------------- | ---------- |
| GET    | `/api/signals` | Emerging defect signals: the batch z-score detector's rows plus any agent-opened (`open_defect_signal`) ones, fleet-relevant first. |

## Recall API — the live feed's own state

| Route                        | What |
| ---------------------------- | ---------- |
| `GET /api/recall-api-status` | Coverage and health of the `recallsByVehicle` sweep (one row per fleet make/model/year), plus campaigns the live API has that the daily flat file does not, with fleet exposure attached. `summary.success_rate_pct` is **null, not 0**, when nothing has been polled — "not run" and "all failed" must not render the same. Backed by `fleetguard_recall_api_poll` / `fleetguard_recall_api_alert`, loaded by `fleetguard-load-recall-api-status`. |

## Reference / roster

| Method | Path                | Purpose |
| ------ | ------------------- | ---------- |
| GET    | `/api/technicians`  | The technician roster, optionally filtered by depot — what work-order assignment validates against. |
| GET    | `/api/depot-risk`   | Fleet-wide risk by depot — the view no single-campaign or single-work-order screen gives you. |
| GET    | `/api/recall-trend` | Historical trend: recall campaigns hitting the fleet per year since 2014. |

## Audit & evidence

| Method | Path                        | Purpose |
| ------ | --------------------------- | ---------- |
| GET    | `/api/audit-log`            | Every campaign launch, work-order change, and cost log, human-readable. |
| GET    | `/api/audit-log/export.csv` | The same, as a CSV download. |
| GET    | `/api/evidence`             | The published backtest result. **Deliberately unauthenticated** — it serves a measured result about NHTSA data, not fleet or VIN data, so it works without a Databricks identity. |
| GET    | `/api/corpus`               | Corpus scale for the landing page — bronze row counts, the synthetic fleet's cardinality, and the RAG chunk count. **Also deliberately unauthenticated**, on the same test. |

**Both public routes serve a committed snapshot, not a live query, and that is a constraint
rather than a preference:** the public surface has no Databricks credential at request time
(§8a), so there is nothing to run `COUNT(*)` under. `scripts/export_evidence.py` and
`scripts/export_corpus.py` regenerate them with provenance (`generated_at`, the source schema,
the exact statement). A missing snapshot is a loud **503**, never zeros — a page rendering
"0 complaints" reads as *this system has no data* rather than *the numbers failed to load*
(I-050).

**Why these two and no others.** Every figure served here is public NHTSA corpus scale or a
*cardinality* of the synthetic fleet registry — no VIN, no depot, no campaign, nothing an
identity could scope. Counting the fleet is public; **reading it is not**, and `/api/queue` and
`/api/depot-risk` read the same registry and stay gated. `tests/test_evidence_route.py` and
`tests/test_corpus_route.py` pin the exemption in both directions, so a third public route has
to be a decision someone makes rather than one that drifts in.

## External APIs this project consumes

Distinct from the console API above — these are third-party sources FleetGuard's pipelines and
fleet registry pull from, not endpoints this project serves. See `CLAUDE.md`'s "Verified facts"
section for how each was confirmed live.

| API                                                   | Used for |
| ----------------------------------------------------- | ---------- |
| `static.nhtsa.gov/odi/ffdd/{cmpl,rcl,inv,tsbs}/*.zip` | Complaint, recall, investigation, and TSB flat files — the corpus everything else is built on. Conditional fetch via `If-Modified-Since` only (the host advertises but ignores `If-None-Match`). |
| `api.nhtsa.gov/recalls/recallsByVehicle`              | Live recall polling for the fleet's ~200 (make, model, year) combos — the reactive half's real-time trigger, feeding `bronze_recall_api` and `gold_recall_alert`. Not the fleet registry: that is vPIC's job, below. No conditional-request support — every poll returns the full body. Retry/gate behaviour in `ARCHITECTURE.md` §3.1. |
| `vpic.nhtsa.dot.gov/api` (`DecodeVINValuesBatch`)     | Authoritative make/model/year/body-class decode for every fleet VIN — never the dirty make/model strings on complaint records. |
