# FleetGuard

## Vehicle Defect Early Warning & Recall Response Platform

**Capstone Project Proposal**
Prepared as a production architecture specification.

**Diagrams:** `fleetguard_e2e.png` (system architecture) · `fleetguard_identity.png` (identity & authorisation)

---

## 1. Executive Summary

Commercial fleet operators learn about vehicle safety defects the same way private owners do — when NHTSA issues a recall. By that point the failures have already occurred across their fleet, because an operator running 400 identical vans carries 400× an individual owner's exposure to any given defect pattern.

FleetGuard operates on NHTSA's complete public defect corpus and delivers two capabilities:

**Reactive — recall response.** When a campaign posts, FleetGuard resolves its scope against the operator's VIN roster in seconds, ranks exposure by depot and severity, and launches the service campaign under human approval.

**Proactive — emerging defect detection.** FleetGuard clusters complaint narratives semantically and surfaces defect patterns before NHTSA opens an investigation. No fleet management platform on the market does this.

The proactive capability is measurable rather than asserted. NHTSA publishes investigation open dates and recall issue dates alongside the complaint corpus, so detection lead time is validated against held-out historical recalls. **The lead-time figure is an output of this system, not an input to its business case.** No such number is claimed in this document.

**Platform:** Databricks end to end. The operator console ships as a **Databricks App**; a lightweight external evidence surface on Render exposes public metrics without authentication. OAuth throughout — no long-lived database credentials anywhere in the system.

---

## 2. Business Context

### The problem

| | Today | With FleetGuard |
|---|---|---|
| Recall arrives | Email notice, manual VIN cross-reference in a spreadsheet, an afternoon of work | Scope resolved against roster in seconds, ranked by depot and severity |
| Park It recall | Same manual process, but the clock is measured in hours | Deterministic VIN-range match, immediate flag, campaign drafted |
| Emerging defect | Invisible until NHTSA acts | Surfaced from complaint clustering with a measured confidence score |
| Audit trail | Email threads and spreadsheet versions | Every decision recorded with actor, timestamp, and rationale |

A recall campaign starts a liability clock. From the moment it posts, the operator is knowingly running vehicles with a documented safety defect.

### Who this is for

**Primary — Fleet Maintenance & Safety Manager.** Owns uptime and safety compliance across a depot network. Approves service campaigns. This is the person who logs in daily.

| Secondary role | Surface | Value |
|---|---|---|
| Depot manager | Work order queue | Own depot's vehicles, VINs unmasked |
| Reliability analyst | Cluster explorer | Fleet-wide patterns, VINs masked |
| VP Operations | Executive dashboard, Genie Agent | "Which depots have open Park It exposure?" |
| Platform engineer | Pipeline health, DQ monitors | Operates the system |

### Market validation

Last-mile delivery operators, rental fleets, utilities, and municipal fleets already license fleet management platforms — Samsara, Fleetio, Verizon Connect. None performs defect-pattern detection against the NHTSA corpus. The budget line exists; the capability does not.

---

## 3. Data Sources

### Verified live sources — public, free, no API key

| Source | Artefact | Content | Status |
|---|---|---|---|
| ODI Complaints | `FLAT_CMPL.zip` | Owner-written defect narratives, 1995 → present; expected order 1.5–2.5M rows, confirmed at first ingest | **Live, verified** |
| ODI Recalls | `FLAT_RCL_POST_2010.zip` | Campaign scope, remedy, consequence, Park It flag | **Live, verified** |
| ODI Investigations | `FLAT_INV.zip` | **154,367 rows** — investigation open dates | **Verified, 0 rescued rows** |
| Technical Service Bulletins | `TSBS_RECEIVED_2025-2026.zip` + historical chunks | **2.4M rows** — manufacturer bulletins, frequently precede recalls | **Verified, 0 rescued rows** |
| Recalls API | `api.nhtsa.gov/recallsByVehicle` | Live campaign detection | **Live** |
| vPIC | `vpic.nhtsa.dot.gov/api` | VIN → make, model, year, plant, body class | **Live** |

Both large flat files parse through `read_files` with zero rescued rows, confirming schema stability ahead of the build.

**The investigations file is the system's ground truth.** It is what converts "we detect defects early" from a marketing claim into a measured lead-time distribution.

### Ingestion policy

NHTSA's API is explicitly not intended for bulk VIN lookups and applies automated rate control. The architecture therefore reads **flat files for the corpus** and uses **the API only for 60-second campaign polling and single-VIN decode**, with decoded results cached in Lakebase. This is the correct design independent of the rate limit.

### Data provenance

| Classification | Assets |
|---|---|
| **Real** | All NHTSA complaints, recalls, investigations, service bulletins, VIN decoding — plus every embedding, model, and agent write the system produces |
| **Synthetic** | Fleet roster only — 20,000 vehicles across 60 depots, every VIN structurally valid and decoded against the live vPIC API |
| **Simulated** | None |

This table is published on the application's landing page.

---

## 4. Architecture

### 4.1 Ingestion

A Lakeflow Job downloads the four flat-file artefacts daily and polls the recalls API every 60 seconds, landing raw files in a **Unity Catalog Volume** at `/Volumes/fleetguard/raw/`. The job runs under a dedicated service principal holding `WRITE VOLUME` and pipeline ownership — and no application privileges.

Checkpoint and schema locations are held at separate paths. Colocating them corrupts schema state.

### 4.2 Lakeflow Declarative Pipeline

**Bronze.** Auto Loader with `schemaEvolutionMode = addNewColumns` and `_rescued_data` capturing malformed rows rather than discarding them. Delta Change Data Feed enabled at creation, which is also a prerequisite for AI Search standard endpoints.

**Silver.** Deduplication on ODI number. Normalisation of manufacturer, make, and model strings — NHTSA's own changelog documents these shifting across the corpus lifetime. `ai_extract` derives structured defect and component fields from narrative prose. PII in narrative text is **detected and tagged, not deleted**, so it can be masked per-role downstream while remaining available to the embedding pipeline.

Expectations route violations to a quarantine table. VIN presence is an enrichment flag, not a validity gate — a large share of complaints carry no VIN, and those records remain valid clustering signal through make, model, year, and component.

**Chunking.** Narratives split into 512-token chunks in `complaint_chunk`, retaining component, make, model, and date metadata for filtered retrieval.

**Gold.** `emerging_cluster`, `recall_campaign_scope`, `odi_investigation`, `fleet_exposure`, `tsb_signal`.

Structured Streaming on a 30-second trigger, watermarked, with idempotent upserts. Photon on serverless compute.

### 4.3 Retrieval and models

**AI Search Delta Sync Index** over `complaint_chunk` using `embedding_source_column`, so AI Search computes and maintains embeddings directly from chunk text. Hybrid ANN + BM25 retrieval, which matters here because component codes and part numbers are exact-match tokens that pure semantic search handles poorly. Storage-optimized endpoint given the vector count.

**Feature Store.** Rolling complaint rate per make/model/component, computed offline for training and served online when the agent scores a live match — a genuine online/offline parity requirement.

**Model A — emerging defect detector.** HDBSCAN over chunk embeddings within a rolling window, combined with a volume-anomaly score comparing each cluster against its own history. TSB signals act as a corroborating feature.

**Model B — match confidence.** Calibrated gradient-boosted classifier scoring recall-scope-to-vehicle matches. Threshold tuned for recall rather than F1 (§6).

Both registered in Unity Catalog, served via Model Serving.

### 4.4 Operational data — Lakebase

Lakebase (managed Postgres) holds all live application state:

```sql
vehicle(vin, depot_id, make, model, year, mileage, status)
depot(depot_id, name, region, manager_principal)
defect_signal(signal_id, component, cluster_id, confidence, status, opened_at)
recall_campaign(campaign_id, nhtsa_number, scope, park_it, issued_at)
vehicle_exposure(vin, campaign_id, signal_id, match_confidence, matched_at)
service_campaign(campaign_id, created_by, approved_by, approved_at, status)
work_order(wo_id, vin, depot_id, assigned_to, due_date, status)
agent_action(action_id, tool, input, output, actor_principal, created_at)
approval(approval_id, action_id, decision, decided_by, decided_at)
audit_log(...)                          -- append-only
public_summary(...)                     -- pre-aggregated, masked, unauthenticated reads
```

`public_summary` exists so the unauthenticated surface never depends on a SQL Warehouse cold start.

### 4.5 Agent

Mosaic AI Agent Framework, with all tools exposed as Unity Catalog Functions and therefore governed as UC securables.

| Tool | Type | Backing |
|---|---|---|
| `search_similar_complaints()` | Read | AI Search index |
| `get_fleet_exposure()` | Read | Lakebase |
| `score_signal()` | Read | Model Serving |
| `fetch_recall_detail()` | Read | NHTSA API |
| `open_defect_signal()` | **Write** | Lakebase |
| `launch_service_campaign()` | **Write, gated** | Lakebase |
| `assign_work_order()` | **Write** | Lakebase |
| `record_decision()` | **Write** | Lakebase |

The agent executes on Databricks compute, never on Render. MLflow 3 traces every tool call, token cost, and latency.

**Unity AI Gateway** sits in front of the agent's serving endpoint (`put_ai_gateway`): a PII-block guardrail as a second layer alongside the Data-Classification tagging already applied to narrative text (§4.2), a per-user rate limit, and an inference table that is the actual mechanism behind the "cost per defect signal" unit economic in §8.6 — that number does not come from a separate cost-tracking system, it comes from joining this inference table's token usage against `agent_activity_fact`.

### 4.6 Analytics

**Lakebase Change Data Feed** (Lakebase CDF, Public Preview) streams row-level operational changes into Unity Catalog as `lb_<table>_history` tables, with `_pg_change_type`, `_pg_lsn`, `_pg_xid`, and `_timestamp` preserved per change. There is no dual-write path, so the analytics layer cannot diverge from application state.

*Build note:* Lakebase CDF is configured at the schema level — start it from the Lakebase UI or via the Postgres REST API / Databricks SDKs (`CAN MANAGE` on the project, plus `USE CATALOG`/`USE SCHEMA`/`CREATE TABLE` on the destination). Every source table needs `REPLICA IDENTITY FULL` and Postgres 16/17/18. We have not confirmed a dedicated Asset Bundle resource type for it, so §8.5's `bundle deploy` may not cover this step end to end — worth checking during the build rather than assuming either way.

A DLT pipeline builds `agent_activity_fact` and `signal_lifecycle_fact`, driving: signals opened per period, median time-to-decision, **human approve versus override rate**, match precision drift, and campaign completion by depot.

Override rate is the system's trust metric. It measures whether operators actually accept the model's judgement, and it triggers retraining.

**AI/BI Dashboards** for executive and operations views. **Genie Agent** over the gold schema for natural-language queries.

---

## 5. Identity & Authorisation

The Databricks App is the primary operator surface, and platform-managed identity is a large part of why. Three token paths, four principals, no shared credentials, no long-lived database passwords. See `fleetguard_identity.png`.

### 5.1 Path A — Databricks App, user on-behalf-of

The primary operator surface. This path carries the core workflow.

1. The user opens the App URL and is authenticated by workspace SSO. There is no separate login, no OAuth client to register, and no redirect URI to maintain.
2. The App receives the user's identity through the `X-Forwarded-Access-Token` header. User authorisation scopes are declared in `app.yaml`; consent is granted once at first launch.
3. **Resource bindings** attach the SQL Warehouse, Model Serving endpoint, Lakebase database, and UC Volume to the App. Credentials for these resources are **rotated by the platform**. The App contains no credential-refresh code.
4. Queries execute as the signed-in user, so Unity Catalog evaluates ABAC at the data layer: row filters scope `depot_id` to the user's assignment, column masks redact VIN and residual owner text.
5. The console presents the signal queue with exposure ranking, campaign approval, and work order assignment.

A depot manager sees their own vehicles with VINs in the clear. An analyst sees fleet-wide patterns with VINs masked. One template — identity determines both rows and columns, and the frontend cannot bypass it.

### 5.2 Path B — External evidence surface, service principal

A small unauthenticated page for public metrics and provenance. No human present, read-only, pre-aggregated and masked.

1. Render holds `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` as secret environment variables. Never committed.
2. `POST /oidc/v1/token` with `grant_type=client_credentials`, `scope=all-apis` → workspace OAuth token, **60-minute lifetime**.
3. `POST /api/2.0/postgres/credentials` with that bearer token → Lakebase database credential, **60-minute lifetime**, workspace-scoped.
4. The connection pool calls `generate_database_credential()` **on every new connection**. Rotation is intrinsic to connection acquisition, not a scheduled job.

External surfaces must implement this rotation explicitly; the App does not, because the platform handles it. The public principal holds `SELECT` on `public_summary` only — no base tables, no writes.

### 5.3 Path C — Agent execution

1. The App invokes the Model Serving endpoint through its resource binding, forwarding user identity and approval context.
2. The agent runs inside Databricks under `fleetguard_agent_sp`. It never executes in the frontend.
3. Tools are UC Functions with per-principal `EXECUTE` grants. A tool the agent has not been granted cannot be invoked, regardless of what the model attempts.
4. `launch_service_campaign()` blocks on human approval; the approver's identity is recorded.
5. Writes land in Lakebase with an append-only `audit_log` entry, flowing onward through Lakebase CDF.

### 5.4 Credential inventory

| Credential | Lifetime | Rotation |
|---|---|---|
| Databricks App resource credentials | Platform-managed | Automatic; no rotation code in the application |
| App OBO user token | Per request | Forwarded header, never persisted |
| External service principal secret | Up to 730 days | Calendar-driven, documented in runbook |
| External Lakebase credential | 60 minutes | Re-minted per connection; expiry enforced at login |

### 5.5 Least privilege

| Principal | Grants |
|---|---|
| `fleetguard_pipeline_sp` | `WRITE VOLUME`, pipeline ownership. No application access. |
| App service principal | Resource bindings only; every query further scoped by user OBO. |
| `fleetguard_agent_sp` | `EXECUTE` on UC tools, `INSERT`/`UPDATE` on operational tables. |
| `fleetguard_public_sp` | `SELECT` on `public_summary` only. External surface, no writes. |
| Signed-in user | ABAC determines visibility, in the App and externally alike. |

No principal holds more than one path's privileges. A compromised external instance can read masked aggregates and nothing else.

---

## 6. Quality Control

| Control | Implementation |
|---|---|
| Golden set | 150 hand-labelled recall-to-vehicle match pairs, stored as a Unity Catalog-backed `mlflow.genai.datasets` evaluation dataset — versioned and governed, not a flat file — held out from training |
| Extraction accuracy | `ai_extract` defect/component fields spot-checked against a hand-labelled sample of complaint narratives, the same ground-truth pattern used for Model B — nothing upstream of clustering is trusted un-checked |
| Model evaluation (Models A and B) | Classical metrics logged to MLflow on every version — precision, recall, and a calibration curve for Model B against the held-out golden set; cluster stability and lead-time distribution for Model A. Precision and recall **published on the application's own page** |
| Agent and retrieval evaluation | `mlflow.genai.evaluate()` against the same UC-backed dataset, scored with named built-in scorers (`Correctness`, `RetrievalGroundedness`, `Safety`) on the retrieval and generation path. Generative scorers are applied only where a generative output exists; the classifier is not evaluated with them |
| Threshold policy | Tuned for recall, not F1 — see below |
| Pipeline expectations | DLT expectations with quarantine routing; no silent drops |
| Data Quality Monitoring | Profiling, freshness, and drift across bronze, silver, gold |
| Agent output | `Guidelines` scorer grades drafted campaign text against the golden set; human approve/override decisions on `launch_service_campaign()` are logged as MLflow feedback via `create_labeling_session()`, so the override rate that triggers retraining (§4.6) is an auditable Assessment trail, not an implied Lakebase counter |
| Lineage and audit | Unity Catalog lineage; every agent write recorded with actor and timestamp |
| Backtest harness | Detection date versus ODI investigation open date on held-out recalls |

**On the threshold.** A false negative leaves a vehicle carrying a documented safety defect in service — a liability event. A false positive sends a technician to inspect a vehicle that proves sound. These errors are not symmetric, and the system does not treat them as though they are.

---

## 7. Design Decisions

**Deterministic path for severe recalls.** Park It campaigns bypass the model entirely. Scope matching is a deterministic VIN-range check; no language model participates in the highest-severity decision path. The model is reserved for ranking and drafting.

**Approval gate placement.** `open_defect_signal()` and `assign_work_order()` execute autonomously — both are reversible and low-cost. `launch_service_campaign()` always requires human approval, because removing vehicles from service carries real cost, and the resulting override rate is itself a measurement worth having.

**PII masked, not removed.** Narrative PII is tagged by Data Classification and masked by ABAC per role. It must remain readable to the embedding pipeline while staying opaque to the dashboard — resolved at the data layer rather than in application code.

**Two tiers, one queue.** Proactive signals and reactive recall campaigns surface in a single work queue ranked by fleet exposure. Operators never context-switch between two products.

**Presentation-only frontend.** Render holds no data, no model, and no agent logic. Every stateful operation executes on Databricks under a governed principal.

---

## 8. Deployment & Operations

### 8.1 Databricks App — primary surface

The operator console ships as a Databricks App and carries the core workflow end to end: signal queue, exposure ranking, campaign approval, and work order assignment.

**Packaging.** A FastAPI application with Jinja templates, declared in `app.yaml` with its command, environment, and user authorisation scopes. Source lives in the project repository and deploys through the Databricks Asset Bundle alongside the pipelines, jobs, and serving endpoints, so a single `databricks bundle deploy` produces a consistent environment. No separate build system.

**Identity.** Workspace SSO with on-behalf-of user authorisation (§5.1). The App receives the user's token by header, and every query to the SQL Warehouse and Lakebase executes as that user. Unity Catalog row filters and column masks continue to apply exactly as specified — enforcement lives at the data layer, so App-hosted and externally hosted surfaces are governed identically.

**Resource bindings.** SQL Warehouse, Model Serving endpoint, Lakebase database, and UC Volume are bound as App resources. Lakebase credentials are rotated by the platform, which removes the 60-minute refresh logic the external surface requires.

**Agent invocation.** The App calls the Model Serving endpoint through its binding, forwarding user identity and approval context. UC Function tools carry per-principal `EXECUTE` grants, so tool access is governed independently of the calling surface.

| Concern | In the App | On the external surface |
|---|---|---|
| User identity | SSO + OBO header | Service principal only, no user |
| Lakebase credentials | Platform-rotated | `generate_database_credential()` per connection |
| ABAC enforcement | Yes, as the signed-in user | Not applicable; reads pre-masked summaries |
| Write actions | Yes, gated by approval | None |
| Cold start | Warm workspace compute | Always-on instance plus pinger |

### 8.2 External evidence surface — secondary

A single unauthenticated page on Render carrying the provenance table, requirement map, backtest results, live pipeline metrics with visible timestamps, and `/health` plus `/api/stats` as public JSON. It reads `public_summary` in Lakebase and never touches a SQL Warehouse synchronously, so no cold start can stall it.

This surface is deliberately read-only and carries no part of the operator workflow. It exists so that platform metrics and data provenance are inspectable without a workspace account.

### 8.3 Latency profile

FleetGuard has two data paths with materially different latency characteristics, and the proposal claims sub-minute performance only on the one that genuinely has it.

**Path 1 — external corpus ingestion. Daily cadence, not sub-minute.**

NHTSA refreshes the ODI flat files once per day. The `recallsByVehicle` API returns neither `ETag` nor `Last-Modified` and ignores `If-Modified-Since` and `If-None-Match`; every request returns a full `200` with the complete body. There is no conditional-request mechanism on this endpoint, so aggressive polling means repeatedly pulling a full payload from a public government API — the same bulk-access pattern §3 commits to avoiding.

Polling therefore runs at **60-second intervals**, which is already generous against a source that changes daily, with exponential backoff on `429`. Worst-case publication-to-visible on this path is roughly 85 seconds, and the proposal does not claim otherwise.

*Build note:* whether `static.nhtsa.gov` returns `Last-Modified` on a `HEAD` request against the flat files is worth verifying empirically before relying on it for cheap change detection. It is a plain file server, so it likely does, but that assumption should be tested rather than assumed.

**Path 2 — operational event stream. Sub-minute, end to end.**

The system's high-velocity data is the operational stream it generates: agent tool invocations, defect signal state transitions, approval decisions, work order assignments, and exposure recomputations across a 20,000-vehicle roster. These flow from Lakebase through Lakebase CDF into Delta and onward to `agent_activity_fact` and `signal_lifecycle_fact`.

| Stage | Average (estimated) | Worst case (estimated) |
|---|---|---|
| Lakebase write commit | < 1 s | 2 s |
| Lakebase CDF capture into `lb_*_history` | 15 s | 20 s |
| Trigger settle (`wait_after_last_change`) + job start | 5 s | 12 s |
| DLT micro-batch into fact tables | 8 s | 15 s |
| Dashboard and console read | 2 s | 5 s |
| **Write → visible in analytics** | **~30 s** | **~54 s** |

These figures are derived from the trigger configuration below rather than assumed independently of it — the capture row reflects Lakebase CDF's documented sync interval, and the settle row reflects `wait_after_last_change_seconds`. Databricks does not publish a latency SLA, but does characterise Lakebase CDF as landing application writes in Unity Catalog within a minute, which is consistent with the worst case above. They remain engineering estimates to be measured against the live pipeline during the build, the same way the lead-time backtest is.

**What actually fires the DLT run.** The `agent_activity_fact`/`signal_lifecycle_fact` pipeline is not driven by a fixed timer — it is a Lakeflow Job with a native **`table_update` trigger** pointed directly at the `lb_<table>_history` mirror tables, so a run starts only when Lakebase CDF actually writes a change:

```yaml
trigger:
  table_update:
    condition: ALL_UPDATED
    table_names:
      - "fleetguard.raw.lb_agent_action_history"
      - "fleetguard.raw.lb_defect_signal_history"
    min_time_between_triggers_seconds: 15
    wait_after_last_change_seconds: 5
```

`wait_after_last_change_seconds` batches rapid successive writes (e.g. an agent working through several signals in one turn) into a single DLT run instead of one run per row; `min_time_between_triggers_seconds` caps run frequency under load. Both are set tight deliberately: a longer settle window would batch more efficiently but would push worst-case visibility past the sub-minute threshold the velocity claim depends on. This replaces an assumed continuous-trigger DLT pipeline with a documented, event-driven primitive.

This is the path the course's own analytics requirement describes — change data feed populating a Delta table supporting analytics about application usage, agent activity, or data changes. It is sub-minute by construction, and it is **demonstrable live**: approve a campaign in the console, refresh the analytics view, and the row is present.

**Summary.** Velocity is met on the operational path. The external corpus is daily and is described as such.

### 8.4 Storage layout and retention

**Clustering.** Delta liquid clustering on `(make, model, component, received_date)` for `complaint_chunk` and silver tables, rather than Hive-style partitioning. Complaint volume is heavily skewed toward a small number of high-volume models, which would produce badly unbalanced partitions; liquid clustering handles that skew and adapts as query patterns settle.

**Bronze** is clustered on ingest date, matching the append pattern.

**Lakebase CDF retention.** The `lb_<table>_history` tables are sync-managed Unity Catalog managed tables that already carry full SCD Type 2 history in their rows, so layering `delta.changeDataFeed.retentionDuration` on top of them is redundant and may not be settable on sync-managed output. Retention target is 30 days of history beyond DLT consumption, applied through the destination tables' own retention and `VACUUM` settings.

*Build note:* confirm which table properties are settable on sync-managed destinations before committing to a mechanism. If they are locked, the fallback is a downstream Delta copy under our own retention policy, with the history tables treated as a transient mirror.

**Lakebase CDF lag monitoring.** A Data Quality Monitor tracks the gap between the newest `_timestamp` in the history tables and the newest row in `agent_activity_fact`, alerting above a five-minute threshold. Lag here means the analytics layer is drifting from the operational truth, which is precisely the failure the no-dual-write design exists to prevent.

### 8.5 Environments and CI/CD

Databricks Asset Bundles define pipelines, jobs, serving endpoints, dashboards, and the App itself as code, with dev and prod targets. GitHub Actions runs `databricks bundle deploy` on merge to main. Databricks Repos for notebook versioning.

### 8.6 Observability

MLflow 3 traces for agent behaviour. Data Quality Monitoring for pipeline health, freshness, and Lakebase CDF lag. System Tables cover platform-wide cost; the agent-specific "cost per defect signal" figure comes from the Unity AI Gateway inference table on the agent's serving endpoint (§4.5), joined against `agent_activity_fact` — a unit economic the business persona understands, backed by a real query rather than a stated aspiration.

## 9. Platform Component Map

**In scope, each load-bearing:** Unity Catalog · UC Volumes · Delta Lake · Delta Change Data Feed · Lakebase Change Data Feed · Auto Loader · Structured Streaming · Lakeflow Jobs (including `table_update` triggers) · Lakeflow Declarative Pipelines · Photon · Serverless compute · AI Search · Feature Store · MLflow 3 (tracking, registry, tracing, evaluation) · Models in Unity Catalog · Model Serving · Unity AI Gateway · Mosaic AI Agent Framework · UC Functions as governed tools · Lakebase · Databricks SQL · AI/BI Dashboards · Genie Agents · Data Quality Monitoring · ABAC row filters and column masks · Data Classification · System Tables · Delta Sharing · Databricks Asset Bundles · Databricks Repos · **Databricks Apps** · OAuth M2M and on-behalf-of.

**Deliberately out of scope:** Clean Rooms (no second party), Lakebridge (legacy migration, not applicable), Lakeflow Designer (visual authoring is inconsistent with a code-first delivery model), Lakeflow Connect (no managed connector exists for NHTSA artefacts; Auto Loader is the correct primitive), Iceberg managed tables (no interoperability requirement).

---

## 10. Scale Characteristics

| Dimension | Status | Evidence |
|---|---|---|
| **Volume** | **Met — verified** | 2.4M service bulletin rows and 154,367 investigation rows confirmed on ingest with zero rescued rows, plus the complaint corpus and 20,000 vehicles |
| **Variety** | **Met** | Owner-written free text with no schema, plus recall, bulletin, and investigation prose |
| **Velocity** | **Met — operational path** | Agent write → Lakebase CDF → Delta fact tables in ~30 s average, ~54 s worst case (§8.3). Demonstrable live. |

Volume and variety are met on the external corpus. Velocity is met on the operational event stream, which is the path the analytics requirement itself describes. The NHTSA corpus refreshes daily and no sub-minute claim is made for it.

---

## 11. Delivery Plan

| Phase | Scope |
|---|---|
| 1 | UC Volume, ingestion job, bronze/silver/gold pipeline |
| 2 | Fleet registry with live vPIC decoding |
| 3 | Chunking and AI Search Delta Sync Index |
| 4 | Model B, golden set, MLflow evaluation harness |
| 5 | Lakebase schema with Lakebase CDF enabled at creation |
| 6 | OAuth: App resource bindings and OBO scopes; external service principal and pool rotation |
| 7 | Agent tools, write path, audit log, approval gate |
| 8 | Databricks App: `app.yaml`, resource bindings, OBO console; external evidence page |
| 9 | **Model A and the lead-time backtest** |
| 10 | Governance: Data Classification, ABAC, DQ monitors, System Tables |
| 11 | Deployment hardening, uptime monitoring, seeded demo state |
| 12 | *Optional:* CPSC and USDA FSIS connectors on the same ingestion core |

Phases 1–8 constitute a complete, deployable system. **Phase 9 is the differentiating capability.** Should schedule pressure arise, phase 12 and cosmetic refinement are the correct sacrifices.

---

## 12. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Lakebase credential expiry on the external surface | Medium | Per-connection OAuth rotation, load-tested before launch. The App uses platform-managed rotation. |
| External surface cold start | Medium | Always-on instance, pinger, Lakebase-backed reads. The App is unaffected — core workflow does not depend on it. |
| NHTSA API rate limiting | Low | 60 s polling against a daily-refreshed source; backoff on `429`; flat files carry the corpus. The endpoint supports no conditional-request mechanism, so polling stays conservative. |
| Fleet roster read as unrepresentative | Medium | Provenance table published; every VIN decoded against live vPIC |
| Demo dependent on a live recall occurring | Medium | Seeded replay scenario; live pipeline runs on schedule with visible timestamp |
| Backtest yields no useful lead time | Medium | Publish the measured result. A negative finding with sound methodology is a legitimate outcome; a tuned figure is not. |
| Service principal secret exposure | Low | Least-privilege grants; public principal reaches only masked summary views |

---

## 13. Requirement Compliance

| Requirement | Implementation |
|---|---|
| Spark data pipeline | Lakeflow Declarative Pipelines, Auto Loader and Structured Streaming, bronze → silver → gold |
| Third-party API | NHTSA recalls, complaints, vPIC — public, keyless |
| Lakebase data model | Managed Postgres OLTP, eleven-table operational schema (§4.4) |
| Action-taking AI agent | Four read tools, four write tools as UC Functions, audited, human-gated |
| Analytics pipeline | Lakebase Change Data Feed → Delta → DLT fact tables → AI/BI and Genie |
| Frontend | ABAC-driven operator console carrying the full workflow |
| Deployed application | **Databricks App**, deployed via Asset Bundle; external evidence page on Render as a secondary read-only surface |
| Two or more of three Vs | Volume verified, variety met, velocity met on the operational Lakebase CDF path (§8.3) |
| Business problem | Fleet safety exposure, reactive and proactive |
| Real consumer | Fleet maintenance and safety managers with an existing budget line |
| Quality control | Golden set, MLflow Evaluate, published metrics, DQ Monitoring, expectations |
| System design | Three-principal OAuth model, human gate, deterministic severe-recall path, no dual-write |
| MLflow | Two registered models, agent tracing, LLM-as-judge, drift-triggered retraining |
| Embeddings, chunking, vector search | 512-token chunks, AI Search Delta Sync Index, hybrid retrieval |
| Unstructured data | Complaint narratives, recall text, 2.4M service bulletins |

---

*Source availability and schema stability verified against live NHTSA endpoints. Product naming current as of August 2026.*
