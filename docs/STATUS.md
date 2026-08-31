# FleetGuard — project status

**Last updated:** 2026-08-31 · **Demo window:** 25–30 September 2026

One page answering "where are we". Design lives in `FleetGuard_Proposal.md`, sequencing in
`../PLAN.md`, and every problem hit during the build in `ISSUES.md`.

---

## Phase status

| Phase | Status | Detail |
|---|---|---|
| **1 — Ingestion + bronze/silver/gold** | 🟡 **~90%** | Ingest job, bronze (4), silver (9) all built and validated. **Outstanding:** chunking → `complaint_chunk`, and 2 of 5 gold tables that are blocked on Phases 3/9. Ingest is **deliberately manual** — no schedule, to avoid consuming shared-workspace compute before it's needed. |
| **2 — Fleet registry** | ✅ **Done** | 20,000 vehicles / 60 depots / ~989k exposure rows. 400 VINs independently vPIC-verified, 400/400 exact. |
| **3 — Chunking + AI Search** | ⬜ Not started | **Next up.** Confirmed load-bearing by the Phase 9 baseline. First step that costs money. |
| **4 — Model B + golden set** | ⬜ Not started | Scope now measured: variant matches outnumber exact 3:1 (I-030). |
| **5 — Lakebase + CDF** | ⏸ **Parked** | Naming decided; **CDF destination schema undecided** (I-028). Gates 6–8. |
| **6 — OAuth wiring** | ⬜ Not started | Blocked on 5. |
| **7 — Agent tools + write path** | ⬜ Not started | Blocked on 5. |
| **8 — App + external surface** | ⬜ Not started | Blocked on 5. |
| **9 — Model A + backtest** | 🟡 **Baseline done** | Volume-anomaly half measured with a control arm. Semantic half outstanding (needs Phase 3). |
| **10 — Governance** | ⬜ Not started | Recommend a visible slice, not the full matrix. |
| **11 — Deployment hardening** | ⬜ Not started | |
| **12 — Second connector** | ❌ **Cut** | Deliberately dropped for schedule. |

---

## What exists in the workspace

**Schema:** `bootcamp_students.fleetguard` (owned by `abhisek.bastia17@gmail.com`, inside a
*shared* bootcamp metastore — never write outside it).
**20 tables, ~16.4M rows.**

| Layer | Tables | Rows |
|---|---|---|
| bronze (4) | `bronze_complaints` · `bronze_recalls` · `bronze_investigations` · `bronze_tsbs` | 2,240,289 · 244,925 · 154,367 · 5,801,279 |
| silver (9) | `silver_complaint` (+quarantine) · `silver_recall` (+q) · `silver_investigation` (+q, +`_case`) · `silver_tsb` (+`_bulletin`) | 2,209,123 · 244,701 · 154,191 · 5,801,279 |
| gold (6) | `gold_fleet_vehicle` · `gold_fleet_depot` · `gold_fleet_exposure` · `gold_lead_time_backtest` · `gold_lead_time_control` · `gold_lead_time_summary` | 20,000 · 60 · ~989k · 777 · 67 · 2 |
| ops (2) | `ops_ingest_watermark` · `ops_recall_poll_state` | `If-Modified-Since` cursor · recall poll cursor |
| api (2) | `bronze_recall_api` · `gold_recall_alert` | 2,117 rows / 653 campaigns · 0 alerts (correct — nothing novel) |

**Compute:** 1 pipeline (`fleetguard-bronze-silver`, IDLE) · 5 jobs, **all manual** ·
1 serverless SQL warehouse.

**APIs integrated:** vPIC (`DecodeVINValuesBatch`, authoritative for make/model/year) ·
recalls (`recallsByVehicle`, 200/200 combos, 100 s sweep) · `static.nhtsa.gov` flat files
(`If-Modified-Since`, verified 304).

**Nothing is billing continuously.** No AI Search endpoint, no Lakebase tables, no schedules.

---

## Measured results

Everything here is measured against live data, not estimated. Full derivations in
`ISSUES.md`.

### Lead-time backtest — the differentiator

The claim is *complaint accumulation → ODI investigation opens*. Volume-anomaly half of
Model A only; no embeddings yet.

| Arm | n | Detected | Rate | Median lead |
|---|---:|---:|---:|---:|
| **Real** (investigated series) | 777 | 124 | **16.0%** | **197 days** |
| **Placebo** (volume-matched control) | 606 | 67 | 11.1% | 343 days |

Two-proportion z ≈ 2.62, **p ≈ 0.009** — statistically real, practically modest (1.44×
lift). A secondary signal is stronger than the headline: real detections cluster near the
open date while control detections scatter toward the window midpoint, which is the shape a
detector tracking a genuine ramp produces.

**Implication:** volume anomaly alone does not carry the differentiator. The semantic half
(Phase 3 → 9) is load-bearing, not an enhancement.

### Corpus

- Complaints **2,240,289** (`LDATE` 1995-01-01 → 2026-08-27) · recalls **244,925** rows /
  **15,211** campaigns · investigations **154,367** rows but **5,344 distinct** · TSBs
  **5,801,279** rows but **258,438 distinct bulletins**.
- Backtest population: **777** post-2010 investigations, **497** with ≥30 prior-year complaints.
- Park It (`DO_NOT_DRIVE`): **211 of 15,211** campaigns; **zero for 2010–2011** — demo from 2015+.
- Harm fields never null; among harm-alleging complaints, police report 39.0%, medical 12.2%.

### Fleet

20,000 vehicles, 47 models, GVWR Class 1D → Class 8. Mix 45% pickup / 40% van / 15% heavy.
Exposure: **EXACT 263,686** rows vs **MODEL_VARIANT 725,356** — variants outnumber exact
~3:1. All 2,116 F-250s match *only* as variants (`F-250 SD` is NHTSA's dominant spelling).

### Cost, sized but not yet incurred

| Item | Cost |
|---|---|
| Embedding ~275M tokens (one-off) | **$28–36** |
| AI Search endpoint (**recurring**) | **~$403/month** (2 standard units) |

Recurring dominates one-off by >10×. Billing stops 24h after the last index is deleted, so
index lifecycle — not corpus trimming — is the lever.

---

## Open decisions blocking progress

| # | Decision | Blocks |
|---|---|---|
| **I-028** | **Lakebase CDF destination.** Existing workspace CDF writes to `bootcamp_students.bootcamp_cdc`, owned by someone else (354 cohort tables); alternative is a UI-only mapping into our own schema. CDF is schema-level — all 11 tables go wherever it points. | Phases 5 → 6 → 7 → 8 |
| **I-026** | **Chunking scope.** 512-token chunking is ~1.0006 chunks/row on complaint narratives (`CDESCR` is `CHAR(2048)`). It genuinely earns its place on investigation summaries (66% exceed one chunk). Keep the near-1:1 table, or index narratives directly? | Phase 3 |
| **I-018** | **Index lifecycle.** How long to leave the AI Search endpoint up. Suggested: subset while developing, full corpus from ~20 Sept. | Phase 3 cost |
| **I-015** | **Streaming vs PII guardrail.** AI Gateway output guardrails don't apply to streaming responses — either no streaming, or drop the §4.5 "second layer" claim. | Phases 7–8 |

---

## Risks, honestly

1. **Phase 5 is completely untested and gates the entire live-demo path.** Lakebase CDF is
   Public Preview, not bundle-deployable, UI-only to configure. If it doesn't work here
   there is no identified fallback for demonstrating sub-minute velocity.
2. **Scope vs schedule.** Eight phases remain in four weeks. Phase 1 alone produced 8 logged
   issues, 4 of them silent. Cut list already agreed: Phase 12 (done), Feature Store online
   serving, Genie Agent, Unity AI Gateway, governance reduced to a visible slice.
3. **The differentiator is real but modest so far.** 1.44× lift is defensible, not
   impressive. Phase 3 has to improve it — and if it doesn't, §6 already commits to
   publishing the floor honestly, which is the fallback.

---

## Document map

| File | Purpose |
|---|---|
| `STATUS.md` | This page — where we are |
| `FleetGuard_Proposal.md` | The design, corrected against measured data |
| `../PLAN.md` | Phase sequencing and definitions of done |
| `ISSUES.md` | Every problem hit, root cause, resolution. **Silent failures flagged.** |
| `fleetguard_e2e.html` / `fleetguard_identity.html` | Current diagrams (editable, diffable) |
| `../CLAUDE.md` | Verified facts — measured numbers that must not be re-derived |
