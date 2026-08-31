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

**Proactive — emerging defect detection.** FleetGuard clusters complaint narratives semantically and surfaces defect patterns before NHTSA opens an investigation. No fleet management platform we surveyed publishes this capability.

The proactive capability is measurable rather than asserted. NHTSA publishes investigation open dates and recall issue dates alongside the complaint corpus, so detection lead time is validated against held-out historical recalls. Because each complaint also carries crash, fire, injury, and fatality fields, that lead time is expressed in harm terms: how many injuries and deaths were reported on a defect during the window in which the pattern was already visible and no regulator had yet acted. **Both figures are outputs of this system, not inputs to its business case.** Neither is claimed in this document.

**Platform:** Databricks end to end. The operator console ships as a **Databricks App**; a lightweight external evidence surface on Render exposes public metrics without authentication. OAuth throughout — no long-lived database credentials anywhere in the system.

---

## 2. Business Context

### The problem

| | Today | With FleetGuard |
|---|---|---|
| Recall arrives | Email notice, manual VIN cross-reference in a spreadsheet, an afternoon of work | Scope resolved against roster in seconds, ranked by depot and severity |
| Park It recall | Same manual process, but the clock is measured in hours | Deterministic scope match on make/model/year and manufacture window, immediate flag, campaign drafted |
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

US last-mile delivery operators, rental fleets, utilities, and municipal fleets — light vehicles through Class 8 — already license fleet management platforms such as Samsara, Fleetio, and Verizon Connect. None performs defect-pattern detection against the NHTSA corpus. The budget line exists; the capability does not.

---

## 3. Data Sources

### Verified live sources — public, free, no API key

| Source | Artefact | Content | Status |
|---|---|---|---|
| ODI Complaints | `FLAT_CMPL.zip` | Owner-written defect narratives — **2,240,289 rows**, `LDATE` 1995-01-01 → 2026-08-27 | **Measured 2026-08-31** |
| ODI Recalls | `FLAT_RCL_POST_2010.zip` | **244,925 rows / 15,211 distinct campaigns** — scope, remedy, consequence, Park It flags | **Measured 2026-08-31** |
| ODI Investigations | `FLAT_INV.zip` | 154,367 rows, but **5,344 distinct investigations** (rows are make/model/year granular) — open dates | **Measured 2026-08-31** |
| Technical Service Bulletins | `TSBS_RECEIVED_*.zip`, seven 5-year chunks | **5,801,279 rows** across all chunks — manufacturer bulletins, frequently precede recalls | **Measured 2026-08-31** |
| Recalls API | `api.nhtsa.gov/recalls/recallsByVehicle` | Live campaign detection; returns `parkIt` / `parkOutSide` booleans | **Live** |
| vPIC | `vpic.nhtsa.dot.gov/api` | VIN → make, model, year, plant, body class, GVWR class | **Live** |

Row counts are measured from the actual files, not estimated, and independently re-confirmed through `read_files` in the workspace on first ingest: 2,240,289 complaint rows and 5,344 distinct investigation action numbers, both matching the offline parse exactly.

**A parsing trap worth stating, because it defeats the obvious quality check.** These files are tab-delimited with no quoting convention, yet the narratives are free text containing double quotes. Spark's CSV reader treats `"` as a quote character by default, which swallows delimiters and shifts fields — silently corrupting 143 complaint rows. `read_files` therefore runs with quote handling disabled (`quote => '\0'`, `sep => '\t'`, `header => false`, `encoding => 'ISO-8859-1'`). Critically, **`_rescued_data` was zero both with and without the fix**: a zero rescued-row count is necessary but not sufficient evidence of a clean parse on this corpus. Ingest validation therefore asserts against known column cardinalities — `PROD_TYPE` distribution and distinct investigation count — rather than trusting the rescue column alone.

**The investigations file is the system's ground truth.** It is what converts "we detect defects early" from a marketing claim into a measured lead-time distribution.

**The backtest population is the distinct-investigation count, not the row count.** Of 5,344 distinct investigations, **777 opened in 2010 or later**, and **497 of those carry at least 30 complaints in the year preceding their open date** — the working backtest set. 154,367 is a row count and would overstate the evidence base by two orders of magnitude if quoted as the population.

**Three intervals are involved here, and only one of them is FleetGuard's claim.** They are kept separate deliberately, because conflating them would be an easy and flattering mistake.

| Interval | Status | Measured |
|---|---|---|
| Complaint accumulation → ODI investigation opens | **This is the claim.** The window in which the pattern is visible and no regulator has acted | **Not yet measured.** The signal exists — 674 of 777 post-2010 investigations (86.7%) have prior complaints, median **341 in the 365 days before the open date**. What FleetGuard converts of that into detection lead time is a Phase 9 output |
| ODI investigation opens → recall issued | Regulatory latency. Context, not a FleetGuard result | 886 campaigns join on `CAMPNO`; 97.2% investigation-first, median **118 days** (p25 51, p75 216) |
| Recall issued → operator response | The reactive half of the product | Seconds, by construction (§7) |

The middle figure is measured and stable, and it is useful — it shows the regulatory pipeline is slow enough that early detection has somewhere to go. But it is **not** evidence that FleetGuard detects anything early. Only the first row is that, and it is deliberately reported as an unmeasured target rather than a result.

**The complaint file carries harm outcomes per record**, which is what makes that distribution meaningful rather than merely early. Confirmed against NHTSA's published file layout:

| Field | Type | Meaning |
|---|---|---|
| `CRASH` | CHAR(1) | Vehicle involved in a crash |
| `FIRE` | CHAR(1) | Vehicle involved in a fire |
| `INJURED` | NUMBER(2) | Persons injured |
| `DEATHS` | NUMBER(2) | Fatalities |
| `MEDICAL_ATTN` | CHAR(1) | Medical attention required |
| `POLICE_RPT_YN` | CHAR(1) | Police report filed |
| `VEHICLES_TOWED_YN` | CHAR(1) | Vehicle towed |
| `FAILDATE` | CHAR(8) | Date of incident, distinct from date received |

`FAILDATE` matters independently: it dates the incident rather than the paperwork, so lead time can be measured from when harm occurred rather than from when someone filed.

**These are consumer allegations, not adjudicated casualty figures.** §6 sets out how the system treats them accordingly.

### Ingestion policy

NHTSA's API is explicitly not intended for bulk VIN lookups and applies automated rate control. The architecture therefore reads **flat files for the corpus** and uses **the API only for 60-second campaign polling and single-VIN decode**, with decoded results cached in Lakebase. This is the correct design independent of the rate limit.

### Data provenance

| Classification | Assets |
|---|---|
| **Real** | All NHTSA complaints, recalls, investigations, service bulletins, VIN decoding — plus every embedding, model, and agent write the system produces |
| **Synthetic** | Fleet roster only — 20,000 vehicles across 60 depots, every VIN structurally valid and decoded against the live vPIC API |
| **Simulated** | None |

This table is published on the application's landing page.

### Scope and boundaries

Stated explicitly, because both boundaries are deliberate and one has build consequences.

**Geographic — United States only.** NHTSA regulates the US market, so the complaint, recall, investigation, and bulletin corpus covers US-market vehicles exclusively. A vehicle sold in Europe is absent from the database even where the same nameplate sells in the US, and market-specific build differences mean a US recall frequently does not apply to the equivalent overseas vehicle. FleetGuard therefore serves US fleet operators.

Equivalent regulators publish comparable data — Transport Canada's recall and defect complaint database is the closest analogue, alongside the EU Safety Gate system, the UK DVSA database, and Japan's MLIT registry. The ingestion core is feed-agnostic by design, so international coverage is a roadmap item rather than a structural limitation. It is the same argument the CPSC and USDA FSIS stretch connectors make, applied across jurisdictions instead of product categories.

**Product type — vehicles and tires; child restraints and equipment excluded.** ODI's unit of analysis spans four categories via the `PROD_TYPE` discriminator: `V` (vehicle), `T` (tires), `E` (equipment), `C` (child restraint). The flat file schema is a union across all four.

*This has direct pipeline consequences.* A substantial share of columns are product-type-specific and null for any given row — `TIRE_SIZE`, `DOT`, `LOC_OF_TIRE`, `TIRE_FAIL_TYPE`, and `REPAIRED_YN` apply only to tires; `SEAT_TYPE` and `RESTRAINT_TYPE` only to child restraints. Silver filters on `PROD_TYPE` before column-level expectations are evaluated, keeping only `V` and `T` rows, so that quality rules are applied only to rows that were ever meant to carry those fields. Without this, expectations fire on structurally valid records and the quarantine table fills with false positives.

Tires are retained deliberately. Tire defects on heavy vehicles are a serious safety matter, operators manage tires as a distinct asset class with their own replacement cycles, and the data arrives in the file already ingested at no additional cost. Child restraints are excluded as irrelevant to a commercial fleet. Equipment (`PROD_TYPE = E` — aftermarket and non-vehicle-specific components) is excluded for the same reason: it does not resolve against a VIN/depot roster the way a vehicle or tire complaint does, so it carries no actionable fleet-exposure signal.

**Vehicle class — light through heavy.** NHTSA regulates vehicle defects across medium and heavy trucks and buses as well as light vehicles; FMCSA regulates carrier operations, which is a separate concern. A Class 8 tractor fleet is therefore in scope.

*Measured:* heavy-truck decode is not a weak point. Class 8 VINs across Freightliner, Peterbilt, Kenworth, Mack, Volvo and International decoded to make, model, year, `Truck-Tractor` body class and `Class 8: 33,001 lb and above` GVWR — coverage at least as complete as light vehicles. One caveat for the roster generator: vPIC returns full attributes even when the check digit fails (`ErrorCode 1`), so decode success must not be gated on `ErrorCode == 0`.

**Temporal — recalls from 2010.** The recall corpus uses `FLAT_RCL_POST_2010`, so recall coverage begins in 2010 while complaints extend back to 1995. This is a designed boundary, not an omission: fleet operators do not run vehicles old enough for pre-2010 campaigns to matter operationally. It does bound the backtest population to post-2010 recalls, which remains ample for a lead-time distribution, and the asymmetry is deliberate — the longer complaint history improves cluster baselines even where no corresponding recall is in scope.

---

## 4. Architecture

### 4.1 Ingestion

A Lakeflow Job downloads the four flat-file artefacts daily and polls the recalls API every 60 seconds, landing raw files in a **Unity Catalog Volume** at `/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/`. The job runs under a dedicated service principal holding `WRITE VOLUME` and pipeline ownership — and no application privileges.

*Namespace note:* the workspace is a shared metastore in which catalog creation is not available to this project, so all FleetGuard objects live in the single schema `bootcamp_students.fleetguard`, which this project owns. Medallion layers are therefore expressed as table-name prefixes (`bronze_`, `silver_`, `gold_`) rather than as sibling schemas. This is a naming convention, not an architectural change — lineage, expectations and grants behave identically.

Checkpoint and schema locations are held at separate paths. Colocating them corrupts schema state.

### 4.2 Lakeflow Declarative Pipeline

**Bronze.** Auto Loader with `schemaEvolutionMode = addNewColumns` and `_rescued_data` capturing malformed rows rather than discarding them. Delta Change Data Feed enabled at creation, which is also a prerequisite for AI Search standard endpoints.

**Silver.** Deduplication on ODI number. Normalisation of manufacturer, make, and model strings — NHTSA's own changelog documents these shifting across the corpus lifetime. PII in narrative text is **detected and tagged, not deleted**, so it can be masked per-role downstream while remaining available to the embedding pipeline.

**Where `ai_extract` runs, and where it deliberately does not.** Component is *not* extracted from prose: `COMPDESC` already ships as a populated structured field on every complaint, and paying a language model to re-derive it across 2.24M rows would be pure cost for no information gain. `ai_extract` is applied **per surfaced cluster, not per complaint** — deriving failure mode, operating conditions, and severity language for the few hundred clusters the system actually raises. This keeps LLM spend proportional to what an operator sees rather than to corpus size, and moves the cost out of the ingest path where it would gate every downstream phase.

Rows are filtered and branched on `PROD_TYPE` before column-level expectations run, so that tire-only and restraint-only columns are never evaluated against vehicle rows. Measured: `PROD_TYPE` is `V` 96.78% / `T` 1.85% / `C` 0.68% / `E` 0.68%, and the tire-only and restraint-only columns are perfectly scoped — zero population outside their own product type. Keeping `V` and `T` retains 98.63% of rows. Expectations route genuine violations to a quarantine table.

**VIN is an enrichment flag, not a validity gate — and not an identifier.** 85.1% of complaints carry a VIN, but the field is `CHAR(11)`: 1,899,700 of 1,907,037 populated values are exactly 11 characters, a partial VIN. Complaint VINs therefore cannot identify an individual vehicle and are never used for fleet matching; records remain valid clustering signal through make, model, year, and component regardless of VIN presence.

**Harm typing.** The outcome fields are cast and normalised in silver rather than passed through as raw text: `CRASH`, `FIRE`, `MEDICAL_ATTN`, `POLICE_RPT_YN`, and `VEHICLES_TOWED_YN` to boolean; `INJURED` and `DEATHS` to integer with nulls distinguished from zeros, since an unanswered field and a reported zero are different claims. `FAILDATE` is parsed and reconciled against `LDATE`, with implausible orderings quarantined rather than silently accepted.

**Chunking.** Narratives split into 512-token chunks in `complaint_chunk`, retaining component, make, model, date, and harm metadata for filtered retrieval — so the agent can restrict a semantic search to complaints that involved a fire or an injury.

**Gold.** `emerging_cluster` (carrying cluster harm totals, corroboration rate, and severity score alongside volume metrics), `recall_campaign_scope`, `odi_investigation`, `fleet_exposure`, `tsb_signal`.

Watermarked Structured Streaming with idempotent upserts, Photon on serverless compute. The corpus pipeline triggers on file arrival rather than on a short timer — the source refreshes daily (§8.3), so a sub-minute micro-batch cadence here would burn serverless compute for no freshness gain. The 30-second cadence belongs to the operational path only.

### 4.3 Retrieval and models

**AI Search Delta Sync Index** over `complaint_chunk` using `embedding_source_column`, so AI Search computes and maintains embeddings directly from chunk text. Hybrid ANN + BM25 retrieval, which matters here because component codes and part numbers are exact-match tokens that pure semantic search handles poorly. Storage-optimized endpoint given the vector count.

**Feature Store.** Rolling complaint rate per make/model/component, plus a rolling harm rate over the same grain — injuries and fatalities per thousand complaints, crash and fire incidence, and corroboration rate (share of harm claims accompanied by a police report or medical attention). Computed offline for training and served online when the agent scores a live match — a genuine online/offline parity requirement.

**Model A — emerging defect detector, harm-weighted.** HDBSCAN over chunk embeddings within a rolling window produces candidate clusters. Each cluster is then scored on two axes:

- **Volume anomaly** — complaint arrival rate against the cluster's own history, as before.
- **Harm severity** — a smoothed weighting over `DEATHS`, `INJURED`, `FIRE`, and `CRASH`, with `MEDICAL_ATTN` and `POLICE_RPT_YN` as corroboration signals.

The two combine into a single ranking score. Volume anomaly is retained rather than replaced: harm is sparse, so a cluster with one catastrophic report and no pattern should not outrank a fast-growing cluster of fire reports.

Smoothing is not optional here. The overwhelming majority of complaints report zero injuries, so an unsmoothed harm weight is dominated by a handful of records and the model degenerates into a fatality lookup. Harm acts as a severity multiplier on a volume-driven cluster score, with shrinkage toward the component-level base rate at low counts.

TSB signals act as a corroborating feature: a manufacturer bulletin on the same component raises confidence that a harm cluster reflects a real defect rather than reporting noise.

**Model B — residual match confidence.** The deterministic pass (§7) resolves campaign scope against the roster on make, model, model-year and manufacture window. Model B exists for what that pass cannot settle: manufacturer and model-string variants (`F-150` / `F150` / `F 150`, plus NHTSA's documented make-name drift), vehicles with an unknown manufacture date, and campaigns whose scope text narrows beyond make/model/year. A calibrated gradient-boosted classifier scores those residual cases, with the threshold tuned for recall rather than F1 (§6). It ranks ambiguity; it does not perform the match.

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

**Unity AI Gateway** sits in front of the agent's serving endpoint (`PUT /api/2.0/serving-endpoints/{name}/ai-gateway`): a PII guardrail set to `Block` as a second layer alongside the Data-Classification tagging already applied to narrative text (§4.2), a per-user rate limit, and an inference table that is the actual mechanism behind the "cost per defect signal" unit economic in §8.6 — that number does not come from a separate cost-tracking system, it comes from joining this inference table's token usage against `agent_activity_fact`.

### 4.6 Analytics

**Lakebase Change Data Feed** (Lakebase CDF, Public Preview) captures every insert, update and delete from the Postgres write-ahead log and lands it in Unity Catalog as `lb_<table>_history` managed Delta tables, batched and flushed roughly every 15 seconds. Each row carries `_pg_change_type` (`insert` / `delete` / `update_preimage` / `update_postimage`), `_pg_lsn`, `_pg_xid`, `_timestamp`, and `_sort_by`. There is no dual-write path, so the analytics layer cannot diverge from application state.

*Build note — confirmed, and it has a consequence.* Lakebase CDF is configured at the schema level from the Lakebase UI or via the Postgres REST API / Databricks SDKs (`CAN MANAGE` on the project, plus `USE CATALOG`/`USE SCHEMA`/`CREATE TABLE` on the destination). Every source table needs `REPLICA IDENTITY FULL` and Postgres 16, 17 or 18. Bundle support for Lakebase is in Beta and covers `postgres_projects`, `postgres_branches`, `postgres_endpoints`, `postgres_roles`, `postgres_databases`, `postgres_synced_tables` and `postgres_catalogs` — **CDF is not among them.** Enabling it is therefore a documented manual step in the runbook, and §8.5's "a single `bundle deploy` produces a consistent environment" carries this one explicit exception. Destination tables auto-suffix on name collision (`lb_users_history_1`), which matters if a table is ever re-synced.

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
| Model evaluation (Models A and B) | Classical metrics logged to MLflow on every version — precision, recall, and a calibration curve for Model B against the held-out golden set; cluster stability, lead-time distribution, and harm-window totals for Model A. Precision and recall **published on the application's own page** |
| Harm data calibration | Outcome fields are consumer allegations, not adjudicated casualty counts. Harm is applied as a smoothed severity multiplier with shrinkage toward the component base rate, never as a raw sum. Corroboration rate against `MEDICAL_ATTN` and `POLICE_RPT_YN` is tracked per cluster and surfaced in the console, so an operator can see how well-supported a severity score is |
| Agent and retrieval evaluation | `mlflow.genai.evaluate()` against the same UC-backed dataset, scored with named built-in scorers (`Correctness`, `RetrievalGroundedness`, `Safety`) on the retrieval and generation path. Generative scorers are applied only where a generative output exists; the classifier is not evaluated with them |
| Threshold policy | Tuned for recall, not F1 — see below |
| Pipeline expectations | DLT expectations with quarantine routing; no silent drops |
| Data Quality Monitoring | Profiling, freshness, and drift across bronze, silver, gold |
| Agent output | `Guidelines` scorer grades drafted campaign text against the golden set; human approve/override decisions on `launch_service_campaign()` are logged as MLflow feedback via `create_labeling_session()`, so the override rate that triggers retraining (§4.6) is an auditable Assessment trail, not an implied Lakebase counter |
| Lineage and audit | Unity Catalog lineage; every agent write recorded with actor and timestamp |
| Backtest harness | **Detection date versus ODI investigation open date** — the first interval in §3, and the only one that is a FleetGuard result. Run over the 497 post-2010 investigations carrying ≥30 prior-year complaints, held-out split within that set, with injuries and fatalities in the intervening window totalled from `INJURED`, `DEATHS`, and `FAILDATE`. Reported with the population size attached. The investigation→recall interval (median 118 days) is regulatory latency and is never reported as a system result |

**On the threshold.** A false negative leaves a vehicle carrying a documented safety defect in service. A false positive sends a technician to inspect a vehicle that proves sound. These errors are not symmetric, and the system does not treat them as though they are.

Harm weighting sharpens this rather than restating it. The threshold is set lower for clusters carrying fire, injury, or fatality reports than for clusters of comparable volume without them, because the cost of a miss scales with what the defect has already done to people. That asymmetry is a configured policy with a stated rationale, not an artefact of tuning.

---

## 7. Design Decisions

**Deterministic path for severe recalls.** Park It campaigns bypass the model entirely. No language model participates in the highest-severity decision path; the model is reserved for ranking and drafting.

*What the deterministic match actually is.* The recall corpus scopes campaigns by make, model, model-year and manufacture-date window (`BGMAN`/`ENDMAN`) — it publishes no VIN ranges, and NHTSA exposes no VIN-to-recall lookup. Complaint VINs are `CHAR(11)` partials and identify nothing. The roster, by contrast, carries full 17-character VINs decoded through vPIC, so scope resolution is exact set membership on `(make, model, model_year)` intersected with the manufacture window — deterministic, sub-second, and free of any model. Residual ambiguity from string variants and missing manufacture dates falls to Model B (§4.3), which ranks it rather than deciding it. Naming this precisely matters: a "VIN-range check" would be a claim the data cannot support, and the deterministic guarantee does not depend on it.

**Approval gate placement.** `open_defect_signal()` and `assign_work_order()` execute autonomously — both are reversible and low-cost. `launch_service_campaign()` always requires human approval, because removing vehicles from service carries real cost, and the resulting override rate is itself a measurement worth having.

**PII masked, not removed.** Narrative PII is tagged by Data Classification and masked by ABAC per role. It must remain readable to the embedding pipeline while staying opaque to the dashboard — resolved at the data layer rather than in application code.

**Two tiers, one queue, ranked by harm.** Proactive signals and reactive recall campaigns surface in a single work queue. The default ranking is harm-weighted severity against fleet exposure, so the top of the queue answers "which defect has already hurt people and sits in my fleet" rather than "which defect touches the most vehicles." Ranking by raw vehicle count remains available as a sort, because a high-exposure low-harm campaign is still a scheduling problem worth seeing. Operators never context-switch between two products.

**Presentation-only frontend.** Render holds no data, no model, and no agent logic. Every stateful operation executes on Databricks under a governed principal.

---

## 8. Deployment & Operations

### 8.1 Databricks App — primary surface

The operator console ships as a Databricks App and carries the core workflow end to end: signal queue, exposure ranking, campaign approval, and work order assignment.

**Packaging.** A FastAPI backend serving a JSON API, with a React single-page frontend built separately (Vite) and served as static assets from the same FastAPI process — one deployable, one origin, no CORS between frontend and backend. Declared in `app.yaml` with its `uvicorn` command, environment, and user authorisation scopes. Source lives in the project repository and deploys through the Declarative Automation Bundle alongside the pipelines, jobs, and serving endpoints, so a single `databricks bundle deploy` produces a consistent environment.

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

**The flat-file host does support conditional requests — but only one of the two mechanisms it advertises.** Measured against `static.nhtsa.gov` on 2026-08-31: `HEAD` returns both `Last-Modified` and `ETag`. A `GET` carrying `If-Modified-Since` at the advertised timestamp returns **`304`, zero bytes**. A `GET` carrying `If-None-Match` with the exact advertised ETag returns **`200` and the full 370 MB body** — the ETag is published and then ignored. Daily change detection therefore uses `If-Modified-Since` only. Building it on `ETag` would look correct, pass review, and silently re-download the entire corpus on every poll.

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

These figures are derived from the trigger configuration below rather than assumed independently of it — the capture row reflects Lakebase CDF's documented ~15-second batch-and-flush interval, and the settle row reflects `wait_after_last_change_seconds`. Databricks publishes no latency SLA for this path, so every row above is an engineering estimate to be measured against the live pipeline during the build, the same way the lead-time backtest is. The sub-minute claim rests on that measurement, not on a vendor guarantee.

**What actually fires the DLT run.** The `agent_activity_fact`/`signal_lifecycle_fact` pipeline is not driven by a fixed timer — it is a Lakeflow Job with a native **`table_update` trigger** pointed directly at the `lb_<table>_history` mirror tables, so a run starts only when Lakebase CDF actually writes a change:

```yaml
trigger:
  table_update:
    condition: ALL_UPDATED
    table_names:
      - "bootcamp_students.fleetguard.lb_agent_action_history"
      - "bootcamp_students.fleetguard.lb_defect_signal_history"
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

Declarative Automation Bundles (DABs, formerly Databricks Asset Bundles) define pipelines, jobs, serving endpoints, dashboards, and the App itself as code, with dev and prod targets. GitHub Actions runs `databricks bundle deploy` on merge to main. Databricks Repos for notebook versioning. One documented exception: Lakebase CDF enablement is not a bundle resource and is a manual runbook step (§4.6).

### 8.6 Observability

MLflow 3 traces for agent behaviour. Data Quality Monitoring for pipeline health, freshness, and Lakebase CDF lag. System Tables cover platform-wide cost; the agent-specific "cost per defect signal" figure comes from the Unity AI Gateway inference table on the agent's serving endpoint (§4.5), joined against `agent_activity_fact` — a unit economic the business persona understands, backed by a real query rather than a stated aspiration.

### 8.7 Phased rollout — Render first, Databricks Apps second

Databricks Apps access may not be available from day one (workspace-level constraint, not a design choice), so the operator console is built once and deployed in two phases rather than gated on that access from the start.

**Phase 1 — Render.** The same FastAPI + React console (§8.1) runs as a standalone Render service. Path B's existing service-principal M2M flow (§5.2) does not apply here, because it carries no human — the console's core value (depot-scoped rows, VIN masking by role) depends on per-user identity, so this phase needs a real login, not a stand-in. It uses Databricks' user-to-machine (U2M) OAuth authorization-code + PKCE flow, distinct from the M2M `client_credentials` flow already used elsewhere in this proposal:

1. Register a **custom OAuth app integration** in the **account console** ("App connections" → Add connection; equivalently `databricks account custom-app-integration create`) as a **confidential client** — the FastAPI backend holds the resulting client secret server-side. This is independent of the Databricks Apps mechanism and doesn't require it.
2. Login: redirect the user to `https://<workspace>/oidc/v1/authorize` with `client_id`, `redirect_uri` (must exactly match what's registered), `response_type=code`, `scope=all-apis offline_access`, `code_challenge` / `code_challenge_method=S256` (PKCE is mandatory), and `state`.
3. Exchange the code at `https://<workspace>/oidc/v1/token` with `grant_type=authorization_code`, `code`, `code_verifier`, and `redirect_uri`.
4. The access token is valid for **one hour**; the `offline_access` scope returns a `refresh_token` for silent renewal, held server-side per session and never exposed to the browser.
5. Every SQL Warehouse, Lakebase, and Model Serving call executes with that user's token, so Unity Catalog ABAC evaluates exactly as it will under Databricks Apps — this phase is a genuine test of the row/column visibility logic, not a stand-in for it.

*Confirm before relying on operationally:* whether HTTPS is enforced on non-localhost redirect URIs, and whether scopes narrower than `all-apis` (e.g. scoped only to Postgres or Model Serving) are accepted by this endpoint, are both undocumented as of this check. Treat `all-apis` as the working default and confirm the rest empirically once the app integration is registered against the `free-edition` workspace.

**Phase 2 — migrate to Databricks Apps.** One environment variable (`DEPLOY_TARGET`) selects between the two modes. A single `AuthProvider` is *not* sufficient on its own — the auth mechanism leaks into three further places, and all three are abstracted from the start:

| Seam | Render phase | Databricks Apps phase |
|---|---|---|
| `AuthProvider.get_current_user()` | Validates session, refreshes the U2M token when it nears its hour | Reads `X-Forwarded-Access-Token` |
| `CredentialFactory` for Lakebase / SQL Warehouse | `generate_database_credential()` per connection; warehouse constructed from env | Platform-rotated resource bindings |
| Route registration | `/login` and `/callback` registered; session middleware active | Neither registered; no session layer |
| Frontend auth state | SPA handles a login redirect and an expiry path | SPA assumes an authenticated session always exists |

Handlers only ever call `get_current_user()` and the credential factory, so no route or business-logic change is needed at cutover. Retire the custom OAuth app integration and its redirect URI, drop the login/callback routes and refresh-token storage, and switch the factory to bindings. SQL, Pydantic models, and the React components are untouched by the move.

*Scope asymmetry to plan for:* the Render phase must request `all-apis` (the documented option for a custom OAuth app integration), whereas the Apps phase can request only what it needs — `sql`, `postgres`, `model-serving`, `vector-search` are all first-class `app.yaml` scopes. The Apps deployment is therefore strictly less privileged than the Render one, which is the right direction of travel and worth stating rather than glossing.

## 9. Platform Component Map

**In scope, each load-bearing:** Unity Catalog · UC Volumes · Delta Lake · Delta Change Data Feed · Lakebase Change Data Feed · Auto Loader · Structured Streaming · Lakeflow Jobs (including `table_update` triggers) · Lakeflow Declarative Pipelines · Photon · Serverless compute · AI Search · Feature Store · MLflow 3 (tracking, registry, tracing, evaluation) · Models in Unity Catalog · Model Serving · Unity AI Gateway · Mosaic AI Agent Framework · UC Functions as governed tools · Lakebase · Databricks SQL · AI/BI Dashboards · Genie Agents · Data Quality Monitoring · ABAC row filters and column masks · Data Classification · System Tables · Declarative Automation Bundles · Databricks Repos · **Databricks Apps** · OAuth M2M, U2M, and on-behalf-of.

**Deliberately out of scope:** Clean Rooms (no second party), Lakebridge (legacy migration, not applicable), Lakeflow Designer (visual authoring is inconsistent with a code-first delivery model), Lakeflow Connect (no managed connector exists for NHTSA artefacts; Auto Loader is the correct primitive), Iceberg managed tables (no interoperability requirement), Delta Sharing (no external consumer — the public surface reads a pre-aggregated Lakebase table, not a share).

---

## 10. Scale Characteristics

| Dimension | Status | Evidence |
|---|---|---|
| **Volume** | **Met — measured** | 5,801,279 service bulletin rows, 2,240,289 complaints, 244,925 recall rows, 154,367 investigation rows — all counted from the live files (§3) — plus 20,000 fleet vehicles |
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
| 9 | **Model A (harm-weighted) and the lead-time backtest** |
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
| NHTSA API rate limiting | Low | 60 s polling against a daily-refreshed source; backoff on `429`; flat files carry the corpus. The API supports no conditional-request mechanism, so polling stays conservative. The flat-file host does support `If-Modified-Since` (§8.3), so the daily corpus pull costs nothing when unchanged. |
| Fleet roster read as unrepresentative | Medium | Provenance table published; every VIN decoded against live vPIC |
| Demo dependent on a live recall occurring | Medium | Seeded replay scenario; live pipeline runs on schedule with visible timestamp |
| Park It demo returns nothing | Medium | `DO_NOT_DRIVE` covers 211 of 15,211 campaigns (1.39%) and is empty for 2010–2011 — the field was added May 2025 and backfilled unevenly. Seed the demo from a 2015-or-later campaign, where coverage is stable. |
| LLM spend exceeds budget on a shared workspace | Medium | `ai_extract` runs per surfaced cluster, not per complaint (§4.2); component comes from `COMPDESC` rather than from prose. Embedding cost is bounded by chunk count and is the one large fixed cost, incurred once. |
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
| Deployed application | **Databricks App**, deployed via Declarative Automation Bundle; Render-hosted console as the Phase 1 surface (§8.7) and thereafter a read-only evidence page |
| Two or more of three Vs | Volume verified, variety met, velocity met on the operational Lakebase CDF path (§8.3) |
| Business problem | Fleet safety exposure, reactive and proactive, ranked by reported harm |
| Real consumer | Fleet maintenance and safety managers with an existing budget line |
| Quality control | Golden set, MLflow Evaluate, published metrics, DQ Monitoring, expectations |
| System design | Three-principal OAuth model, human gate, deterministic severe-recall path, no dual-write |
| MLflow | Two registered models, agent tracing, LLM-as-judge, drift-triggered retraining |
| Embeddings, chunking, vector search | 512-token chunks, AI Search Delta Sync Index, hybrid retrieval |
| Unstructured data | 2.24M complaint narratives, recall text, 5.8M service bulletins |

---

*Source availability and schema stability verified against live NHTSA endpoints. Product naming current as of August 2026.*
