# FleetGuard — Capstone Project Proposal

**Vehicle defect early warning and recall response for commercial fleet operators**

Prepared as a senior data architecture proposal. Diagram: `fleetguard_e2e.png`.

---

## 1. Executive summary

Commercial fleet operators find out about vehicle safety defects the same way private owners do: when NHTSA issues a recall. By then the failures have already happened on their vehicles, often hundreds of them, because a fleet running 400 identical vans carries 400× an individual owner's exposure to any defect pattern.

FleetGuard reads NHTSA's public defect corpus — over 1.5 million owner-written complaint narratives going back to 1995, plus recall campaigns, defect investigations, and manufacturer service bulletins — and does two things a fleet operator cannot do today:

1. **Reactive.** When a recall posts, it resolves campaign scope against the operator's VIN roster in seconds instead of an afternoon, ranks by depot and severity, and launches the service campaign.
2. **Proactive.** It clusters complaint narratives semantically and flags emerging defect patterns *before* NHTSA acts, which nobody currently does for fleets.

The second capability is verifiable rather than asserted. Because NHTSA publishes investigation open dates alongside complaints and recalls, the system's detection lead time can be measured against held-out historic recalls. **That measurement is an output of this project, not an input.** No lead-time figure is claimed in this proposal until the backtest produces one.

---

## 2. Who this is for

**Primary user — Fleet Maintenance & Safety Manager.** Owns vehicle uptime and safety compliance across a depot network. Today they receive recall notices by email, cross-reference VIN ranges in a spreadsheet, and chase depots by phone. They are the person who approves a service campaign.

**Secondary users:**

| Role | What they use | What FleetGuard changes |
|---|---|---|
| Depot manager | Work order queue | Sees only their depot's vehicles, unmasked |
| Reliability analyst | Cluster explorer, trends | Sees fleet-wide patterns, VINs masked |
| VP Operations | Executive dashboard, Genie Agent | Asks "which depots have open Park It exposure" in plain English |
| Data engineer | Pipeline health, DQ monitors | Operates the system |

**Why there is a real consumer.** Last-mile delivery operators, rental companies, utilities, and municipal fleets already pay for fleet management platforms (Samsara, Fleetio, Verizon Connect). None of them does defect-pattern detection against the NHTSA corpus. The budget line exists; the capability does not.

**Why the timing matters.** A recall campaign starts a clock. From the moment it posts, the operator is knowingly running vehicles with a documented safety defect. On a Park It recall — NHTSA's designation for defects severe enough that owners should stop driving immediately — that clock is measured in hours.

---

## 3. What I corrected from the working documents

The uploaded architecture drafts are strong on platform terminology and I have kept most of their structure. Seven things needed fixing, and the first is a requirement failure rather than a preference.

**1. Lakebase was misidentified.** The rubric matrix maps *"Lakebase → all tables in Unity Catalog (managed + external)."* That is not what Lakebase is. Lakebase is a managed Postgres-compatible **OLTP** engine with its own projects, branches, and endpoints. Delta tables in Unity Catalog are not Lakebase, and submitting a project where "Lakebase" means "Delta tables" fails the Lakebase requirement outright, and takes the Change Data Feed requirement down with it. **Corrected:** operational state lives in Lakebase Postgres; the analytical corpus lives in Delta; CDF flows *from Lakebase into* Delta.

**2. AWS Lambda is unnecessary.** The drafts ingest via Lambda into S3. That adds a second cloud account, IAM configuration, and a failure mode outside Databricks, for no benefit. **Corrected:** a Lakeflow Job task downloads the flat files and polls the APIs directly into a **UC Volume**. This also aligns with the storage document's own recommendation, which I agree with — Volumes give governed, lineage-tracked file storage with no IAM to troubleshoot mid-demo.

**3. The DLT expectation would break the pipeline.** The drafts use `@dlt.expect_or_fail("valid_vin", "vin IS NOT NULL AND length(vin) = 17")` on complaints. NHTSA complaints frequently have missing or partial VINs — owners often don't supply one. `expect_or_fail` halts the pipeline on the first such row, and a VIN-less complaint is still perfectly good clustering signal because it carries make, model, year, and component. **Corrected:** `expect_or_drop` routing to a quarantine table, with VIN presence treated as an enrichment flag rather than a validity gate.

**4. Invalid Python.** `def silver.enriched_complaints():` is not a legal function name. Minor, but a judge reading the code will see it.

**5. Streamlit fails the AI grader.** Streamlit renders an empty shell and streams content over a websocket after JS execution. A grader scraping the page sees nothing. **Corrected:** FastAPI + Jinja server-rendered HTML.

**6. NWS weather data is Rainmaker residue.** Weather has no causal relationship to vehicle defect patterns. It reads as a component bolted on to fill a checklist, which is exactly what an experienced judge notices. **Cut.**

**7. Unvalidated ROI figures.** "3–6 months before NHTSA" and "$2.3M in avoided failures" appear as executive-summary facts. Neither has been measured. Asserting the number the project exists to discover inverts the scientific logic and is the easiest thing in the deck to attack. **Corrected:** both are stated as measurement targets with methodology attached.

*Terminology check:* I verified the renames the drafts use. **AI Search** (formerly Vector Search, renamed June 2026, SDK `databricks-ai-search`), **Genie Agents** (formerly Genie Spaces), and **Data Quality Monitoring** are all correct and current. Use them.

---

## 4. Data sources and integration

### Real, external, free, no API key

| Source | Endpoint | Content | Refresh | Integrated via |
|---|---|---|---|---|
| ODI complaints | `static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip` | 1.5M+ owner-written narratives since 1995, tab-delimited | Daily | Lakeflow Job → UC Volume → Auto Loader |
| ODI recalls | `.../rcl/FLAT_RCL_POST_2010.zip` | Campaign scope, remedy, consequence, Park It flag | Daily | Same |
| ODI investigations | `.../inv/FLAT_INV.zip` | Investigation open dates — **the backtest ground truth** | Daily | Same |
| Service bulletins | `.../tsbs/TSBS_RECEIVED_2025-2026.zip` (+ 6 historical chunks back to 1995) | Manufacturer TSBs, often precede recalls | Daily | Same |
| Recalls API | `api.nhtsa.gov/recallsByVehicle` | Live new-campaign detection | On demand | 60s poll, Structured Streaming |
| vPIC | `vpic.nhtsa.dot.gov/api` | VIN → make, model, year, plant, body class | On demand | UC Function, cached in Lakebase |

**TSB source restructured mid-2024, handled by design.** NHTSA split the single TSB flat file into 5-year chunks (`TSBS_RECEIVED_1995-1999.zip` … `TSBS_RECEIVED_2025-2026.zip`), each independently versioned. A companion `MFR_COMMS_RECEIVED_*.zip` series also exists at the same path but is a slimmer CSV (TSB ID, make/model/year, summary only) — not the tab-delimited flat file this pipeline needs. The ingestion job pulls the historical chunks once and re-polls only the current open chunk (`2025-2026`) daily.

**Rate limit, handled by design.** NHTSA states the API is not intended for bulk VIN lookups and applies automated rate control. The architecture therefore uses **flat files for the corpus and the API only for incremental polling and single-VIN decode**, with results cached. Worth stating explicitly in the walkthrough: it shows the constraint was read rather than discovered in production.

### Synthetic

**Fleet registry** — 20,000 vehicles across 60 depots. Real fleet rosters are not public. Every VIN is structurally valid and decoded against the **live vPIC API**, so the join between synthetic roster and real recall data exercises the real code path.

### Simulated

Nothing.

This real / synthetic / simulated split is published as a table on the application's landing page. Last year's winner did exactly this, and it reads as engineering maturity rather than as an admission.

---

## 5. End-to-end architecture

### Layer 1 — Ingestion

A Lakeflow Job downloads the four flat files daily and polls the recalls API every 60 seconds, landing raw files in a **UC Volume** at `/Volumes/fleetguard/raw/`. Checkpoint and schema locations are kept at **separate paths** — colocating them corrupts schema state, a detail the working documents flagged correctly and which is worth keeping.

### Layer 2 — Lakeflow Spark Declarative Pipeline

**Bronze.** Auto Loader with `schemaEvolutionMode = addNewColumns` and `_rescued_data` capturing malformed rows rather than dropping them. Change Data Feed enabled from creation, which is also a prerequisite for AI Search standard endpoints.

**Silver.** Deduplication on ODI number; normalisation of manufacturer, make, and model strings (NHTSA's own changelog documents these shifting over time); `ai_extract` pulling structured defect and component fields out of narrative prose; PII detection on narrative text — the flat file periodically contains names, addresses, and dealer contacts — **tagged rather than deleted**, so it can be masked per-role downstream. Expectations route violations to quarantine.

**Chunking.** Narratives split into 512-token chunks into `complaint_chunk`, retaining component, make, model, and date metadata for filtered retrieval.

**Gold.** `emerging_cluster`, `recall_campaign_scope`, `odi_investigation`, `fleet_exposure`.

Structured Streaming on a 30-second trigger, watermarked, with idempotent upserts. Photon on serverless compute.

### Layer 3 — AI Search and ML

**AI Search Delta Sync Index** over `complaint_chunk`, using `embedding_source_column` so AI Search computes and maintains embeddings from the chunk text. This removes the duplicated embedding step in the working drafts, which computed vectors in the pipeline *and* built an index. Hybrid ANN + BM25 retrieval, which matters here because component codes and part numbers are exact-match tokens that pure semantic search handles poorly. Storage-optimized endpoint for cost at this vector count.

**Feature Store** — rolling complaint rate per make/model/component, computed offline for training and served online when the agent scores a live match. This is a genuine online/offline parity problem, not a checkbox.

**MLflow Model A — emerging-defect detector.** HDBSCAN over chunk embeddings within a rolling window, plus a volume-anomaly score comparing each cluster against its own history.

**MLflow Model B — match confidence.** Calibrated gradient-boosted classifier scoring recall-scope-to-vehicle matches. Threshold tuned for **recall rather than F1**, for a stated reason (§7).

Both registered in Unity Catalog and served via Model Serving.

### Layer 4 — Agent and Lakebase

**Lakebase** (managed Postgres) holds live operational state:

```
vehicle(vin, depot_id, make, model, year, mileage, status)
depot(depot_id, name, region, manager)
defect_signal(signal_id, component, cluster_id, confidence, status, opened_at)
recall_campaign(campaign_id, nhtsa_number, scope, park_it, issued_at)
vehicle_exposure(vin, campaign_id | signal_id, match_confidence, matched_at)
service_campaign(campaign_id, created_by, approved_by, approved_at, status)
work_order(wo_id, vin, depot_id, assigned_to, due_date, status)
agent_action(action_id, tool, input, output, actor, created_at)
approval(approval_id, action_id, decision, decided_by, decided_at)
audit_log(...)  -- append-only
```

**Agent Bricks / Mosaic AI Agent Framework**, tools exposed as governed UC Functions:

*Read:* `search_similar_complaints()` (AI Search), `get_fleet_exposure()` (Lakebase), `score_signal()` (Model Serving), `fetch_recall_detail()` (live NHTSA API).

*Write:* `open_defect_signal()`, `launch_service_campaign()`, `assign_work_order()`, `record_decision()`.

Every write lands in `audit_log`. **MLflow 3** traces the agent loop, logs token cost and tool calls, and runs LLM-as-judge evaluation against the golden set.

### Layer 5 — Analytics and serving

**Lakebase Change Data Feed** streams row-level operational changes into Delta with `_change_type` preserved. No dual-write, so the analytics layer can never disagree with the application. A DLT pipeline builds `agent_activity_fact` and `signal_lifecycle_fact`, driving: signals opened per week, median time-to-decision, **human approve vs override rate**, match precision drift, and campaign completion by depot.

The override rate is the metric worth watching. It measures whether the humans actually trust the model, and it triggers retraining.

**AI/BI Dashboards** for executive and operations views. **Genie Agent** over the gold schema for natural-language questions from the VP persona.

---

## 6. Frontend on Render — two surfaces, one codebase

The UI document argues for a single dynamic view where per-user OAuth flows through to the SQL Warehouse and Unity Catalog row filters and column masks decide what each user sees. That is architecturally correct and I have kept it — but it cannot be the *only* surface, because **the AI grader cannot log in**. A single authenticated view means the scraper sees a login wall and scores zero.

**Public surface (no auth).** Server-rendered FastAPI + Jinja. Aggregate and masked data only, served under a service principal. The real/synthetic table, the requirement map, the backtest result, live pipeline metrics with a visible timestamp, `/health` and `/api/stats` as public JSON. This is what the scraper reads.

**Operator console (authenticated).** Per-user OAuth on-behalf-of to the SQL Warehouse, so Unity Catalog ABAC genuinely decides content: a depot manager sees their depot's vehicles with VINs unmasked; an analyst sees fleet-wide patterns with VINs masked. One template, identity decides the rows and columns. This is where write actions and the approval gate live.

### Two connection details that will bite during judging

**Lakebase OAuth tokens expire after 60 minutes.** Databricks Apps rotate credentials automatically; external apps do not get that. Render needs a **service principal with an OAuth secret** and a connection pool calling `generate_database_credential()` on each new connection. A static token in an environment variable works throughout development and then dies an hour into the judging window.

**Two cold starts stack.** Render's free tier spins down after inactivity, and the first Lakebase connection after idle is slow because autoscaling starts compute from zero. Mitigation: paid always-on Render instance for the judging window, plus an external pinger hitting an endpoint that touches Lakebase, keeping both warm.

**The landing page must never query a SQL Warehouse synchronously.** Warehouse start-up would blow a scraper timeout. Public reads come from a Lakebase summary table refreshed by a Databricks job.

---

## 7. Quality control

- **Golden set** — 150 hand-labelled recall-to-vehicle match pairs, held out from training.
- **MLflow Evaluate** on every model version, with precision and recall **published on the application's own page**, not buried in a notebook.
- **Threshold policy, stated.** Tuned for recall rather than F1: a false negative leaves a vehicle with a documented safety defect on the road, a liability event. A false positive sends a mechanic to check a van that turns out fine. Those errors are not symmetric and the system does not pretend they are.
- **DLT expectations** with quarantine, never silent drops.
- **Data Quality Monitoring** for profiling, freshness, and drift on bronze, silver, and gold.
- **LLM-as-judge** on agent-drafted campaign text, scored against the golden set.
- **Unity Catalog** lineage, grants, and audit on every agent write.

---

## 8. System design decisions worth defending

**Where the model does not run.** Park It recalls bypass the model entirely. Scope match is a deterministic VIN-range check, the vehicle is flagged, no language model participates. Reserving the model for ranking and drafting and keeping it out of the highest-severity path is a deliberate choice.

**Human approval gate.** `open_defect_signal()` and `assign_work_order()` run autonomously — reversible and cheap. `launch_service_campaign()` always waits on a person, because pulling vehicles out of service costs real money, and the resulting approve/override rate is itself a signal worth measuring.

**PII masked, not deleted.** Narrative PII is tagged by Data Classification and masked by ABAC per role. It must stay readable to the embedding pipeline while being opaque to the dashboard — a real design tension with a defensible resolution.

**Two tiers, one queue.** Proactive signals and reactive recall campaigns land in the same work queue ranked by fleet exposure. The user never context-switches.

---

## 9. Databricks component map

**Used, each earning its place:** Unity Catalog · UC Volumes · Delta Lake · Change Data Feed · Auto Loader · Structured Streaming · Lakeflow Jobs · Lakeflow Declarative Pipelines (DLT) · Photon · Serverless compute · AI Search · Feature Store · MLflow 3 (tracking, registry, tracing, evaluation) · Models in Unity Catalog · Model Serving · Mosaic AI Agent Framework / Agent Bricks · UC Functions as tools · Lakebase · Databricks SQL · AI/BI Dashboards · Genie Agents · Data Quality Monitoring · ABAC row filters and column masks · Data Classification · System Tables · Delta Sharing · Databricks Asset Bundles · Databricks Repos.

**Deliberately excluded:** Clean Rooms (no second party), Lakebridge (legacy warehouse migration, irrelevant), Lakeflow Designer (drag-and-drop undercuts the engineering credibility of the rest), Lakeflow Connect (no managed connector exists for NHTSA flat files, so Auto Loader is correct), Iceberg managed tables (no interoperability requirement here).

Say the exclusions out loud in the walkthrough. A judge reading thirty components where five are obviously bolted on scores lower than twenty-eight that all survive the question "why is this here."

---

## 10. Three Vs

| V | Status | Evidence |
|---|---|---|
| Volume | **Met** | 1.5M+ narratives → roughly 5M chunk vectors, joined against 20,000 vehicles |
| Variety | **Met** | Owner-written free text with no schema, plus recall and bulletin prose |
| Velocity | **Partial** | 60s poll into a 30s micro-batch — pipeline latency, not source latency. NHTSA publishes daily. |

Two of three met outright. I would rather state the velocity limitation than claim sub-minute freshness on a source that refreshes once a day.

---

## 11. Build sequence

1. UC Volume, ingestion job, bronze/silver/gold pipeline — establishes volume immediately
2. Fleet registry with live vPIC decoding
3. Chunking + AI Search Delta Sync Index
4. Model B, golden set, MLflow evaluation
5. Lakebase schema, CDF enabled from day one
6. Agent tools, write path, audit log, approval gate
7. Render public surface + operator console
8. **Model A and the lead-time backtest**
9. Deploy, uptime monitoring, seeded demo state
10. Governance layer: Data Classification, ABAC, DQ monitors, System Tables
11. *Stretch:* CPSC / FSIS second connector

Steps 1–7 constitute a complete, submittable project. **Step 8 is what wins it.** If the schedule slips, cut step 11 and polish — not the backtest.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Lakebase token expiry mid-judging | Service principal + pooled `generate_database_credential()` rotation |
| Stacked Render / Lakebase cold starts | Paid always-on instance, external pinger, Lakebase-backed landing page |
| Grader scrapes an empty JS shell | Server-rendered HTML, public JSON endpoints, no auth on the evidence surface |
| NHTSA API rate limiting | Flat files carry the corpus; API only for polls and cached single-VIN decode |
| Synthetic fleet reads as fake | Real/synthetic/simulated table; every VIN decoded against live vPIC |
| Demo depends on a live recall occurring | Seeded replay scenario; live pipeline runs on schedule with visible timestamp |
| Crawler clicking buttons wipes agent writes | Session-scoped writes, scheduled reset |
| Backtest shows no useful lead time | Publish it. A measured negative result reads as competence; a tuned-until-pretty number reads as the opposite. |

---

## 13. Requirement compliance

| Requirement | Implementation |
|---|---|
| Spark data pipeline | Lakeflow Declarative Pipelines, Auto Loader + Structured Streaming, bronze→silver→gold |
| Third-party API | NHTSA recalls, complaints, vPIC — free, keyless |
| **Lakebase data model** | **Managed Postgres OLTP, 10-table operational schema (§5)** |
| Action-taking AI agent | 4 read tools, 4 write tools as UC Functions, audited, human-gated |
| Analytics pipeline | **Lakebase CDF** → Delta → DLT fact tables → AI/BI + Genie |
| Frontend | Server-rendered public surface + ABAC-driven operator console |
| Deployed application | Render, always-on, Databricks backend |
| 2+ of 3 Vs | Volume and variety met; velocity partial and stated |
| Business problem | Fleet safety exposure, reactive and proactive |
| Real consumer | Fleet maintenance and safety managers with existing budget |
| Quality control | Golden set, MLflow Evaluate, published precision/recall, DQ Monitoring, expectations |
| System design | Human gate, deterministic severe-recall path, no dual-write, PII masked not deleted |
| MLflow | Two registered models, agent tracing, LLM-judge, drift-triggered retraining |
| Embeddings / chunking / vector search | 512-token chunks, AI Search Delta Sync Index, hybrid retrieval |
| Unstructured data | 1.5M+ owner-written free-text narratives |

---

*Endpoints and product names verified current as of August 2026. Row counts to be confirmed on first ingest.*
