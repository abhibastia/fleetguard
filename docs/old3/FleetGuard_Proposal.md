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

NHTSA's API is explicitly not intended for bulk VIN lookups and applies automated rate control. The architecture therefore reads **flat files for the corpus** and uses **the API only for incremental campaign polling and single-VIN decode**, with decoded results cached in Lakebase. This is the correct design independent of the rate limit.

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

**Bronze.** Auto Loader with `schemaEvolutionMode = addNewColumns` and `_rescued_data` capturing malformed rows rather than discarding them. Change Data Feed enabled at creation, which is also a prerequisite for AI Search standard endpoints.

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

### 4.6 Analytics

**Lakebase Change Data Feed** streams row-level operational changes into Delta with `_change_type` preserved. There is no dual-write path, so the analytics layer cannot diverge from application state.

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
5. Writes land in Lakebase with an append-only `audit_log` entry, flowing onward through CDF.

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
| Golden set | 150 hand-labelled recall-to-vehicle match pairs, held out from training |
| Model evaluation | MLflow Evaluate on every version; precision and recall **published on the application's own page** |
| Threshold policy | Tuned for recall, not F1 — see below |
| Pipeline expectations | DLT expectations with quarantine routing; no silent drops |
| Data Quality Monitoring | Profiling, freshness, and drift across bronze, silver, gold |
| Agent output | LLM-as-judge on drafted campaign text, scored against the golden set |
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

### 8.3 Latency budget

Freshness is measured from NHTSA publication to visibility in the operator console.

| Stage | Average | Worst case |
|---|---|---|
| Recall API poll interval (conditional `If-Modified-Since`) | 7.5 s | 15 s |
| Bronze micro-batch trigger | 7.5 s | 15 s |
| Silver enrichment and exposure join | 4 s | 8 s |
| Lakebase upsert and console read | 2 s | 5 s |
| **Publication → visible** | **~21 s** | **~43 s** |

Polling runs at a 15-second interval using conditional requests, so unchanged responses return `304 Not Modified` and cost almost nothing. Exponential backoff applies on `429`. This respects NHTSA's rate guidance while holding worst-case end-to-end latency under 60 seconds.

The operational write path is faster still: agent writes reach the Delta analytics layer through Lakebase Change Data Feed in under 10 seconds, so agent-activity analytics are sub-minute by construction.

**Interpretation.** Sub-minute latency here describes the ingestion and propagation path, which is what the system controls. NHTSA itself refreshes the flat-file corpus daily.

### 8.4 Storage layout and retention

**Clustering.** Delta liquid clustering on `(make, model, component, received_date)` for `complaint_chunk` and silver tables, rather than Hive-style partitioning. Complaint volume is heavily skewed toward a small number of high-volume models, which would produce badly unbalanced partitions; liquid clustering handles that skew and adapts as query patterns settle.

**Bronze** is clustered on ingest date, matching the append pattern.

**CDF retention.** `delta.changeDataFeed.retentionDuration` is set to 30 days on Lakebase-backed analytics tables, comfortably exceeding the DLT refresh interval while bounding storage. `delta.logRetentionDuration` is aligned to the same window.

**CDF lag monitoring.** A Data Quality Monitor tracks the gap between the newest `_commit_timestamp` in the change feed and the newest row in `agent_activity_fact`, alerting above a five-minute threshold. Lag here means the analytics layer is drifting from the operational truth, which is precisely the failure the no-dual-write design exists to prevent.

### 8.5 Environments and CI/CD

Databricks Asset Bundles define pipelines, jobs, serving endpoints, dashboards, and the App itself as code, with dev and prod targets. GitHub Actions runs `databricks bundle deploy` on merge to main. Databricks Repos for notebook versioning.

### 8.6 Observability

MLflow 3 traces for agent behaviour. Data Quality Monitoring for pipeline health, freshness, and CDF lag. System Tables for cost attribution, including cost per defect signal surfaced — a unit economic the business persona understands.

## 9. Platform Component Map

**In scope, each load-bearing:** Unity Catalog · UC Volumes · Delta Lake · Change Data Feed · Auto Loader · Structured Streaming · Lakeflow Jobs · Lakeflow Declarative Pipelines · Photon · Serverless compute · AI Search · Feature Store · MLflow 3 (tracking, registry, tracing, evaluation) · Models in Unity Catalog · Model Serving · Mosaic AI Agent Framework · UC Functions as governed tools · Lakebase · Databricks SQL · AI/BI Dashboards · Genie Agents · Data Quality Monitoring · ABAC row filters and column masks · Data Classification · System Tables · Delta Sharing · Databricks Asset Bundles · Databricks Repos · **Databricks Apps** · OAuth M2M and on-behalf-of.

**Deliberately out of scope:** Clean Rooms (no second party), Lakebridge (legacy migration, not applicable), Lakeflow Designer (visual authoring is inconsistent with a code-first delivery model), Lakeflow Connect (no managed connector exists for NHTSA artefacts; Auto Loader is the correct primitive), Iceberg managed tables (no interoperability requirement).

---

## 10. Scale Characteristics

| Dimension | Status | Evidence |
|---|---|---|
| **Volume** | **Met — verified** | 2.4M service bulletin rows and 154,367 investigation rows confirmed on ingest with zero rescued rows, plus the complaint corpus and 20,000 vehicles |
| **Variety** | **Met** | Owner-written free text with no schema, plus recall, bulletin, and investigation prose |
| **Velocity** | **Met** | 15-second conditional poll into a 15-second micro-batch. Worst-case publication-to-visible ~43 s; agent-write to analytics under 10 s via CDF (§8.3). |

All three dimensions are met. Velocity is measured on the ingestion and propagation path the system controls; the NHTSA corpus itself refreshes daily, and the proposal does not claim otherwise.

---

## 11. Delivery Plan

| Phase | Scope |
|---|---|
| 1 | UC Volume, ingestion job, bronze/silver/gold pipeline |
| 2 | Fleet registry with live vPIC decoding |
| 3 | Chunking and AI Search Delta Sync Index |
| 4 | Model B, golden set, MLflow evaluation harness |
| 5 | Lakebase schema with CDF enabled at creation |
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
| NHTSA API rate limiting at 15 s polling | Medium | Conditional requests return `304` when unchanged; exponential backoff on `429`; flat files carry the corpus |
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
| Analytics pipeline | Lakebase CDF → Delta → DLT fact tables → AI/BI and Genie |
| Frontend | ABAC-driven operator console carrying the full workflow |
| Deployed application | **Databricks App**, deployed via Asset Bundle; external evidence page on Render as a secondary read-only surface |
| Two or more of three Vs | Volume verified, variety met, velocity met at sub-60 s (§8.3) |
| Business problem | Fleet safety exposure, reactive and proactive |
| Real consumer | Fleet maintenance and safety managers with an existing budget line |
| Quality control | Golden set, MLflow Evaluate, published metrics, DQ Monitoring, expectations |
| System design | Three-principal OAuth model, human gate, deterministic severe-recall path, no dual-write |
| MLflow | Two registered models, agent tracing, LLM-as-judge, drift-triggered retraining |
| Embeddings, chunking, vector search | 512-token chunks, AI Search Delta Sync Index, hybrid retrieval |
| Unstructured data | Complaint narratives, recall text, 2.4M service bulletins |

---

*Source availability and schema stability verified against live NHTSA endpoints. Product naming current as of August 2026.*
