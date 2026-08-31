# FleetGuard — project status

**Last updated:** 2026-08-31 (end of session) · **Demo window:** 25–30 September 2026

One page answering "where are we". Design lives in `FleetGuard_Proposal.md`, sequencing in
`../PLAN.md`, and every problem hit during the build in `ISSUES.md`.

---

## Phase status

| Phase | Status | Detail |
|---|---|---|
| **1 — Ingestion + bronze/silver/gold** | 🟡 **~90%** | Ingest job, bronze (4), silver (9) all built and validated. **Outstanding:** chunking → `complaint_chunk`, and 2 of 5 gold tables that are blocked on Phases 3/9. Ingest is **deliberately manual** — no schedule, to avoid consuming shared-workspace compute before it's needed. |
| **2 — Fleet registry** | ✅ **Done** | 20,000 vehicles / 60 depots / ~989k exposure rows. 400 VINs independently vPIC-verified, 400/400 exact. |
| **3 — Chunking + AI Search** | ✅ **Done-when met** | Hybrid retrieval verified (I-040): exact-token + semantic in one result set, `columns_to_sync` works, harm filter PASSES 10/10. Index still syncing (~42%, ~6.7h total — I-041); re-run the test after completion to confirm nothing changes. |
| **4 — Model B + golden set** | ⬜ Not started | Scope now measured: variant matches outnumber exact 3:1 (I-030). |
| **5 — Lakebase + CDF** | ✅ **Schema done** | All **11 tables** created, every one `REPLICA IDENTITY FULL`. CDF round-trip verified end to end on `fleetguard_depot` (I-038). **Outstanding:** populate the tables, and a timed write to measure real capture latency. Unblocks 6–8. |
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

⚠️ **Now billing:** AI Search endpoint `fleetguard-vs` (STANDARD, 1 unit) — **~$6.72/day**,
started 2026-08-31. Nothing else recurs: no Lakebase tables, no schedules.
Stop it with `databricks vector-search-indexes delete-index bootcamp_students.fleetguard.complaint_chunk_idx`
then `databricks vector-search-endpoints delete-endpoint fleetguard-vs` — billing ends 24h
after the last index is deleted.

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

## Testing

Three layers, doing different jobs. Full rationale in `src/pipelines/expectations/README.md`.

| Layer | What | Run |
|---|---|---|
| **Unit** (`tests/test_vin.py`, `test_chunking.py`, `test_naming.py`) | Pure logic, no Databricks. **91 tests.** | `pytest` |
| **LDP expectations** (`src/pipelines/**/*.sql`) | Row-level, in-pipeline. Post-routing invariants + explicit `_dq_failures` quarantine split. | runs with the pipeline |
| **Data quality** (`tests/test_data_quality.py`) | Cross-table invariants against live tables. **21 tests, 73 s.** | `pytest -m integration --run-integration` |

Logic that has already been wrong once is extracted into `src/fleetguard/` so it is
testable off platform, and the bugs are encoded as **regressions**:

- `TestRegressionI034` — the single-character-chunk bug (stride vs window)
- `test_transliteration_is_many_to_one` — a VIN checksum property the suite *discovered*:
  `A`/`J`/`1` share a transliterated value, so some substitutions are invisible to the
  check digit. My original test asserted the opposite and failed.
- `test_complaint_parse_is_correct_by_cardinality_not_by_rescued_data` — the check that
  would have caught I-012, where `_rescued_data` read 0 while 143 rows were mis-parsed.
- `test_nothing_is_dropped_silently` — asserts `bronze = silver + quarantine` exactly.
- `test_no_detection_leaks_past_the_open_date` — a detection dated on or after the
  investigation opened is leakage, not lead time.

**The rule:** a check that cannot fail is not a check. Every assertion here has either
failed during the build or exists because something adjacent to it failed silently.

---

## Open decisions blocking progress

| # | Decision | Blocks |
|---|---|---|
| **I-026** | **Chunking scope.** 512-token chunking is ~1.0006 chunks/row on complaint narratives (`CDESCR` is `CHAR(2048)`). It genuinely earns its place on investigation summaries (66% exceed one chunk). Keep the near-1:1 table, or index narratives directly? | Phase 3 |
| **I-018** | **Index lifecycle.** How long to leave the AI Search endpoint up. Suggested: subset while developing, full corpus from ~20 Sept. | Phase 3 cost |
| **I-015** | **Streaming vs PII guardrail.** AI Gateway output guardrails don't apply to streaming responses — either no streaming, or drop the §4.5 "second layer" claim. | Phases 7–8 |

---

## Risks, honestly

1. ~~**Phase 5 is completely untested.**~~ **RETIRED 2026-08-31** — the CDF round-trip is
   verified end to end (I-038). What remains is the other 10 tables plus a timed
   latency measurement, both routine. This was the project's largest risk and it is gone.
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

---

## Picking this up tomorrow

**Running unattended right now:** the AI Search index sync. It was ~42% at 22:11 and moves
at 4,336 rows/min, so it should be complete overnight (~6.7 h total, I-041). First thing:

```bash
databricks vector-search-indexes get-index \
  bootcamp_students.fleetguard.complaint_chunk_idx --profile abhi
```

Then re-run the hybrid test to confirm nothing changed qualitatively at full corpus:
`fleetguard-hybrid-query-test`.

**Billing:** `fleetguard-vs` is the only recurring cost, ~$6.72/day. Everything else is
manual-trigger. Stop it by deleting the index then the endpoint.

**Highest-value next steps, in order:**

1. **Populate the Lakebase tables from gold** — `gold_fleet_vehicle` → `fleetguard_vehicle`,
   `gold_fleet_exposure` → `fleetguard_vehicle_exposure`, etc. This also makes the other ten
   CDF history tables materialise, since CDF creates a destination on first *write*, not on
   `CREATE TABLE`. Unblocks the §8.3 velocity demo.
2. **Time a write end to end** — §8.3's ~15 s capture figure is still *documented*, not
   *measured*. One timed insert closes that.
3. **Phase 9 semantic upgrade** — HDBSCAN over the now-populated index, re-run the backtest
   harness, and see whether the 16.0% / 11.1% gap widens. This is the differentiator.

**Do not** attempt an index rebuild inside the demo window — it is most of a working day.
