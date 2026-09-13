# FleetGuard

Vehicle defect early-warning and recall-response platform for commercial fleets, built on
Databricks (Lakeflow, Lakebase, Unity Catalog, Model Serving, AI Search) against NHTSA's
public defect corpus.

**Built for a fleet safety team** running thousands of vehicles — the people who resolve a
recall against the roster and decide what gets dispatched — and for the safety leadership
above them who need to know the program is actually working, not just busy. Today, a fleet
learns about safety defects the same way a private owner does — when a recall posts.
FleetGuard gives the team two capabilities instead of one:

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

**Primary — Databricks App:** [`fleetguard-console`](https://fleetguard-console-1352785079224954.aws.databricksapps.com)
— real per-user OBO: every query runs as *your* Databricks identity, not a service account
and not a simulation, so Unity Catalog and Postgres apply their own rules to you rather than
the app deciding what you may see.

**It is kept stopped between sessions** so it isn't billing idle compute, and it will not
answer a cold URL. **Start it yourself — about two minutes:**

```bash
databricks apps start fleetguard-console
```

You'll then see an **OAuth consent screen** listing `postgres`, `sql` and `model-serving`.
Accept it; declining returns `403 Invalid scope` on every data page, which looks like a broken
app rather than an unauthorised one. Please **stop it again** when you're done.

**The Assistant's first answer takes up to a minute.** The agent runs on a serving endpoint kept
scaled to zero, so the first question wakes it — measured 47 s. Please wait rather than assuming
it hung. If it says *"the assistant is offline"* instead, the endpoint has gone fully stopped and
cannot wake itself; every other tab is unaffected, and it can be restored on request in about
three minutes.

**[`docs/DEMO.md`](docs/DEMO.md) is the guided tour** — pre-flight with measured timings, the
nine beats worth seeing, every number with its source, and an explicit list of what this project
does *not* claim.

**Honest status of this path:** verified end to end, but under the owner's identity, and for the
API routes under a programmatic token rather than a browser. No second person has signed in yet.
The failure that would matter — authenticating successfully and then having every data route fail
for want of a Lakebase role — **cannot happen to the reviewers**, who were checked and all hold
one. It is unproven rather than known-broken (`docs/STATUS.md`, I-084).

**The other way to run it** is locally, against the same live Lakebase — see *Run it locally*
below. Those are the two supported surfaces. A third, a public Render deployment with its own
OAuth login, was removed on 2026-09-10 and is preserved on the `deploy/render` branch; the
`render-u2m` browser login it carried was never confirmed working, having stalled on an
account-admin scope grant.

The **Evidence** tab needs no identity on either surface — it's the published backtest result
above, sourced from the same measurement.

## What it's built on

- **Ingestion → medallion pipeline** (Lakeflow Declarative Pipelines): NHTSA's complaint,
  recall, investigation, and TSB flat files → bronze → silver → gold, with a quarantine
  split so `bronze = silver + quarantine` reconciles exactly at every layer.
- **External APIs consumed**: `static.nhtsa.gov` flat files (`If-Modified-Since` only — the
  host ignores `If-None-Match`), `api.nhtsa.gov/recalls/recallsByVehicle` (no conditional-request
  support), and vPIC (`DecodeVINValuesBatch`) for authoritative make/model/year decode. Full
  list, including this console's own REST API, in [`docs/API.md`](docs/API.md).
- **Semantic retrieval**: Databricks AI Search over 1.7M+ complaint narrative chunks,
  hybrid (BM25 + embedding) search.
- **A registered, deployed agent** (Agent Framework, Model Serving): seven tools —
  five read (complaint search, fleet exposure, fleet vocabulary lookup, emerging-signal
  lookup, campaign proposal) and two real writes (`open_defect_signal`, `watch_campaign` —
  both executed by the app under the caller's own identity, never by the model) — traced
  with MLflow, evaluated
  against a held-out golden set built from NHTSA's own recall text.
- **Lakebase Postgres** as the operational store for fleet state (vehicles, depots, service
  campaigns, work orders, audit log). Change Data Feed replicates every write into Unity
  Catalog — **measured 7.1–15.6 s for the capture itself**; the derived gold facts follow a
  `table_update` job, taking **2.5–4.5 minutes end to end** (two live cycles). The two numbers
  are kept apart deliberately: quoting the capture latency for the whole chain would overstate
  it by an order of magnitude. Postgres Row-Level Security enforces depot-scoped reads below
  the application, not just in it — proved live, with two honest limits: no principal is
  enrolled by default, so it is fail-open until someone is, and a reviewer holding
  `bypassrls` will not see it apply to their own session (`docs/DEMO.md` §5).
- **A FastAPI + React console**, one service for API and UI, running unchanged on Databricks
  Apps and on a local server behind a single auth seam (`app/backend/fleetguard_api/auth/`).

## Run it locally

```bash
scripts/run_local_static_dev.sh          # starts the backend at :8811 against live Lakebase
```

**The token it mints lives one hour.** After that every Lakebase-backed route returns 500 with
`Invalid Token` — which looks exactly like a code regression if you've been editing all
afternoon, and has been mistaken for one twice. The fix is to restart the script, not to debug
your changes; `static-dev` holds a *static* token by design.

See `app/backend/README.md` for what this sets up and why every one of its environment
variables is there. Frontend development lives in `app/frontend/` (`npm run dev`); rebuild
the console the backend serves with `scripts/build_console.sh`.

## Deploying

Everything deployable is a **Declarative Automation Bundle** — `databricks.yml` plus
`resources/` describe the App, the bronze/silver pipeline, the AI/BI dashboard and 17 jobs.
They are *bound* to the existing workspace objects, so deploying updates them in place rather
than creating copies.

```bash
databricks bundle summary -t prod --profile abhi   # nothing should read "to be created"
./scripts/deploy.sh abhi prod                      # guards a dirty tree, deploys, checks provenance
```

Shipping the App is four commands, and the last one is the one that matters:

```bash
./scripts/build_console.sh                                        # only if app/frontend/ changed
databricks bundle deploy -t prod --profile abhi
databricks apps start fleetguard-console --profile abhi           # if stopped, ~2 min
databricks bundle run fleetguard_console -t prod --profile abhi   # ← ships the code
```

`bundle deploy` alone prints `Deployment complete!` and creates **no app deployment** — the
running app keeps serving what it last deployed. See `docs/ISSUES.md` I-097.

Deploying stays a deliberate manual act: it restarts the App under whoever is using it, so CI
runs tests and lint only and holds no workspace credentials. Four things the bundle does *not*
cover — Lakebase CDF (UI-only), the AI Search endpoint and index, the agent serving endpoint,
and the `evidence_metrics` metric view — are documented in `docs/ARCHITECTURE.md`.

Editing a workspace object by hand (`jobs reset`, `apps update`, the UI) is silently undone by
the next deploy.

## Repository layout

| Path | What's there |
|---|---|
| `src/` | Ingestion, medallion pipelines, fleet registry, search, agent, backtest, Lakebase migrations |
| `app/` | The FastAPI backend + React console that make up the live product |
| `docs/` | Living architecture spec, build status, issue log, and the frozen original proposal |
| `scripts/` | Runnable setup/build scripts — local dev server, console build, evidence/snapshot export, demo-state seeding |
| `tests/` | Unit tests (run everywhere) and integration tests (opt-in, hit the live workspace) |
| `dashboards/` | AI/BI dashboard definitions |
| `resources/` | Bundle resource files — the App, the pipeline, the dashboard, 17 jobs, as code |

## Documentation map

Read `docs/STATUS.md` first if you're picking this up cold — it's the one page answering
"where are we," with next steps in priority order.

| Document | Job |
|---|---|
| [`docs/DEMO.md`](docs/DEMO.md) | **Start here to look around** — pre-flight, the nine beats, numbers with sources, what not to claim |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The living spec — what the system *is*, kept true with the code |
| [`docs/API.md`](docs/API.md) | Every console REST endpoint and every external API this project consumes, one page |
| [`docs/STATUS.md`](docs/STATUS.md) | Where the build has got to, updated every session |
| [`docs/ISSUES.md`](docs/ISSUES.md) | Every problem hit during the build, root cause, resolution — append-only |
| [`docs/ENHANCEMENTS.md`](docs/ENHANCEMENTS.md) | Evaluated backlog: adopted, deferred, or rejected, with reasons |
| [`docs/FleetGuard_Proposal.md`](docs/FleetGuard_Proposal.md) | What was proposed, before the build — **frozen**, not updated as facts changed |
| [`PLAN.md`](PLAN.md) | Phase sequencing and definitions of done |
