# FleetGuard — project status

**Last updated:** 2026-09-01 · **MVP target: 7 September** · **Demo: 25–30 September**

> **MVP = one vertical slice working end to end:** recall lands → exposure ranked → human
> approves → work orders written to Lakebase → visible in UC via CDF → visible in a browser.
> Scope, cut order and the explicit *not-in-MVP* list are in
> [`ENHANCEMENTS.md`](ENHANCEMENTS.md#mvp--target-7-september-2026-6-days). Landing MVP on
> the 7th leaves ~18 days to improve a working system rather than finish one.

One page answering "where are we". Design lives in `FleetGuard_Proposal.md`, sequencing in
`../PLAN.md`, and every problem hit during the build in `ISSUES.md`.

---

## Phase status

| Phase | Status | Detail |
|---|---|---|
| **1 — Ingestion + bronze/silver/gold** | 🟡 **~90%** | Ingest job, bronze (4), silver (9) built and validated. Chunking done — `silver_complaint_chunk_indexed`, 1,746,601 chunks. **Outstanding:** `gold_emerging_cluster` + the scope tables that follow it, both blocked on Phase 9's semantic arm. Ingest is **deliberately manual** — no schedule, to avoid consuming shared-workspace compute before it's needed. |
| **2 — Fleet registry** | ✅ **Done** | 20,000 vehicles / 60 depots / ~989k exposure rows. 400 VINs independently vPIC-verified, 400/400 exact. |
| **3 — Chunking + AI Search** | ✅ **DONE** | Index complete: **1,746,601 chunks, `ready: true`**, matching source exactly. Done-when **re-verified at full corpus** — hybrid differs from ANN on 2 of 3 queries, harm filter 10/10, near-duplicates 10/10 distinct. The earlier check ran at 42% and was repeated before being quoted. |
| **4 — Model B + golden set** | ⬜ Not started | Scope now measured: variant matches outnumber exact 3:1 (I-030). |
| **5 — Lakebase + CDF** | ✅ **DONE** | 11 tables, all `REPLICA IDENTITY FULL`; **all 11 CDF history tables exist with exact names, no `_1` suffixes** (I-044 — CDF replicates DDL, correcting an earlier wrong claim). Reference data loaded: depot 60, vehicle 20,000, campaigns 592. **Capture latency measured: 7.1–15.6 s** (I-046). **Outstanding:** the 989k exposure load — a *scope decision*, not unfinished plumbing. |
| **6 — OAuth wiring** | ⬜ Not started | Unblocked — 5 is done. |
| **7 — Agent tools + write path** | ⬜ Not started | Unblocked. Needs the exposure-load scope call to populate the work queue. |
| **8 — App + external surface** | ⬜ Not started | Unblocked. |
| **9 — Model A + backtest** | ✅ **DONE — result is negative** | **The semantic hypothesis is falsified (I-049).** Subdivision *lowered* detection 13.3% → 11.2%, left lift flat (1.24× → 1.26×), and gave **0.0 days** extra lead on shared detections. Published result stays the volume-anomaly measurement: **16.0% vs 11.1%, 1.44×, p≈0.009**. Done-when explicitly required publishing a possibly-negative number as-is; met. |
| **10 — Governance** | ⬜ Not started | Recommend a visible slice, not the full matrix. |
| **11 — Deployment hardening** | ⬜ Not started | |
| **12 — Second connector** | ❌ **Cut** | Deliberately dropped for schedule. |

---

## What exists in the workspace

**Schema:** `bootcamp_students.fleetguard` (owned by `abhisek.bastia17@gmail.com`, inside a
*shared* bootcamp metastore — never write outside it).
**34 tables** (excluding pipeline materialisations and event logs).

| Layer | Tables | Rows |
|---|---|---|
| bronze (4) | `bronze_complaints` · `bronze_recalls` · `bronze_investigations` · `bronze_tsbs` | 2,240,289 · 244,925 · 154,367 · 5,801,279 |
| silver (10) | `silver_complaint` (+quarantine, +`_chunk`, +`_chunk_indexed`) · `silver_recall` (+q) · `silver_investigation` (+q, +`_case`) · `silver_tsb` (+`_bulletin`) | 2,209,123 · **1,746,601 chunks** · 244,701 · 154,191 · 5,801,279 |
| gold — fleet (3) | `gold_fleet_vehicle` · `gold_fleet_depot` · `gold_fleet_exposure` | 20,000 · 60 · 989,042 |
| gold — backtest (6) | `gold_lead_time_backtest` · `_control` · `_summary` · `gold_backtest_scope` · `_complaint` · `_embedding` | 777 · 67 · 2 · 6,649 · 205,219 · **205,219 vectors** |
| ops (7) | `ops_ingest_watermark` · `ops_recall_poll_state` · `ops_hybrid_query_test` · `ops_lakebase_load` · `ops_cdf_latency` · `ops_psycopg_probe` · `ops_pg_privilege_diagnostic` | measurement + cursor state |
| api (2) | `bronze_recall_api` · `gold_recall_alert` | 2,117 rows / 653 campaigns · 0 alerts (correct — nothing novel) |

**Lakebase** (`databricks_postgres.bootcamp_students`): 11 `fleetguard_*` tables, 20,652 rows
loaded. **CDF** (`bootcamp_students.bootcamp_cdc`): 11 `lb_fleetguard_*_history` tables,
exact names, no collision suffixes.

**Compute:** 1 pipeline (`fleetguard-bronze-silver`, IDLE) · **16 jobs, all manual** ·
1 serverless SQL warehouse · 1 AI Search endpoint.

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
| **NEW** | **Exposure load scope.** `gold_fleet_exposure` → `fleetguard_vehicle_exposure` is **989,042 rows**. `COPY` is not the constraint (~25 s at the reference rate); the question is what ~1M change events do to a CDF pipeline **shared with ~296 other students**. Options: full, `EXACT`-only (263,686), or a demo slice. Re-measure throughput first — the `PSYCOPG_IMPL=python` fix (I-045) uses the slower pure-Python driver. | Phase 7's work queue |
| **I-018** | **Index lifecycle.** How long to leave the AI Search endpoint up. Now the only recurring cost at ~$6.72/day; ~24 days to demo ⇒ ~$160 if left running throughout. | Cost |
| **I-015** | **Streaming vs PII guardrail.** AI Gateway output guardrails don't apply to streaming responses — either no streaming, or drop the §4.5 "second layer" claim. | Phases 7–8 |
| ~~I-026~~ | ~~Chunking scope.~~ **Resolved by measurement** — the full-corpus hybrid test returned 10/10 distinct `complaint_id`, so the near-1:1 chunk table causes no near-duplicate retrieval problem and needs no read-time dedupe. | — |

---

## Risks, honestly

1. ~~**Phase 5 is completely untested.**~~ **FULLY RETIRED 2026-09-01** — schema, load, CDF
   replication and capture latency are all measured. This was the project's largest risk.
2. ~~**The differentiator is still the risk, and the verdict is imminent.**~~ **RESOLVED
   2026-09-01 — and the answer is no.** The semantic arm was built and measured; it makes
   detection *worse* and adds no lead time (I-049). The fallback is now the position: the
   differentiator is the **measured 16.0% vs 11.1% volume-anomaly result with a control arm
   (1.44×, p≈0.009)**. This is a genuine risk *retired*, not deferred — the number is known,
   defensible, and will not move between now and the demo.

   **The remaining risk is presentational, not technical.** The result is modest, so the
   demo has to sell *rigour* — a control arm, a falsified hypothesis, a published negative —
   rather than a big number. That is a stronger story than an unfalsifiable 10×, but it has
   to be told deliberately.
3. **Scope vs schedule — now the top schedule risk.** Phases 4, 6, 7, 8, 10, 11 are all
   unstarted with **24 days** left, and 6/7/8 (the demo surface) are a dependent chain.
   Phases 3 and 5 are done, so nothing is blocked *technically* — the constraint is purely
   build time. Cut list already agreed: Phase 12 (done), Feature Store online serving,
   Genie Agent, Unity AI Gateway, governance reduced to a visible slice.
4. **A pattern worth naming: the tooling lies about success.** Three distinct variants in
   one day — a watcher exiting `0` at 51% (I-043), `jobs run-now` returning `0` for a
   `FAILED` run, and the CLI reporting `Error: timed out` while the job ran on healthily.
   **Only `state.result_state` describes the job.** Every verification in this project reads
   the live resource, never the client's exit code.

---

## Document map

Each document has exactly one job and a stated lifecycle. A frozen doc that gets edited
loses its integrity; a living doc that doesn't get edited becomes a lie.

| File | Purpose | Lifecycle |
|---|---|---|
| **`ARCHITECTURE.md`** | **What the system is — the living spec** | Living; update with the code |
| `ENHANCEMENTS.md` | Evaluated backlog — adopt / defer / reject, each with a reason | Living |
| `FleetGuard_Proposal.md` | What was *proposed*, before the build | **FROZEN** 2026-08-31 |
| `STATUS.md` | This page — where the build has got to | Living, high-churn |
| `ISSUES.md` | Every problem hit, root cause, resolution. **Silent failures flagged.** | Append-only |
| `../PLAN.md` | Phase sequencing and definitions of done | Living |
| `../CLAUDE.md` | Verified facts that must not be re-derived | Living |
| `fleetguard_e2e.html` / `fleetguard_identity.html` | Diagrams (editable, diffable) | Living |

The proposal is **not** updated to match findings. Its header tabulates the known
contradictions with measured results — that gap is the record of what the build taught us,
and erasing it would destroy the only evidence of what was believed at the outset.

---

## Where we are — 1 Sep

**AI Search index: COMPLETE.** `ready: true`, **1,746,601 of 1,746,601** chunks (100%),
matching `silver_complaint_chunk_indexed` exactly. ~7 h end to end.

**Phase 3 done-when re-confirmed at full corpus** (`fleetguard-hybrid-query-test`, results
in `ops_hybrid_query_test`). The earlier verdict was taken on a 42%-built index, so it had
to be repeated before it could be quoted:

| Query | ANN vs HYBRID |
|---|---|
| component code + symptom | **differs** — hybrid surfaces `FOUNDATION COMPONENTS:HOSES, LINES/PIPING` |
| pure paraphrase | **differs** — hybrid finds `ENGINE`/`VEHICLE SPEED CONTROL`; ANN drifts to `AIR BAGS` |
| `TAKATA airbag inflator` | identical — both saturate on `AIR BAGS` |

Harm filter **10/10 PASS**. Near-duplicate audit **10/10 distinct `complaint_id`** — the
sibling-chunk concern (I-023) does not materialise at full corpus, so the agent's search
tool does not need read-time dedupe after all.

**Billing:** `fleetguard-vs` is the only recurring cost, ~$6.72/day. Everything else is
manual-trigger. Stop it by deleting the index then the endpoint.

---

## Phase 5 — Lakebase loaded and CDF measured (1 Sep)

**Reference tables loaded**, each reconciling exactly against source:

| table | rows | load |
|---|---|---|
| `fleetguard_depot` | 60 | 0.0 s |
| `fleetguard_vehicle` | 20,000 | 0.5 s (~40k rows/s) |
| `fleetguard_recall_campaign` | 592 | 0.0 s |

**CDF replicated all of it.** Depot's history reconciles exactly to the I-038 test —
61 insert / 60 `update_preimage` / 60 `update_postimage` / 1 delete, which is the original
60 inserts plus 59 upsert-conflicts plus the one previously-deleted row re-inserted. That
confirms `ON CONFLICT DO UPDATE` **and** `REPLICA IDENTITY FULL` in one arithmetic check.

**All 11 history tables exist with exact names, no `_1` suffixes** — the naming risk
(I-036) is fully retired across every table, not just the one round-tripped in I-038.

**CDF capture latency MEASURED** (`ops_cdf_latency`, 3/3 true measurements):
**7.1 – 15.6 s, mean 12.5 s.** State it as a *range consistent with a ~15 s flush*, never
as one averaged number; size demos against the **15.6 s worst case**. Comfortably supports
§8.3's sub-minute claim. See I-046 for why the first attempt (21.55 s) was an upper bound
and not a measurement.

---

## Phase 9 — semantic arm: DONE, result is NEGATIVE (1 Sep)

**The hypothesis is falsified.** Grouping complaints by *what they describe* rather than by
NHTSA's component code does **not** surface defect ramps earlier. Both groupings recomputed
on the identical 37-month working set:

| grouping | arm | detected | rate | median lead |
|---|---|---|---|---|
| v2 component | REAL | 103/777 | **13.3%** | 240 d |
| v2 component | PLACEBO | 65/606 | 10.7% | 360 d |
| v3 semantic | REAL | 87/777 | **11.2%** | 202 d |
| v3 semantic | PLACEBO | 54/606 | 8.9% | 273 d |

Lift **1.24× → 1.26×** — unchanged. Detection **fell**.

**The decisive number** is in the paired view: on the **70** investigations both groupings
detect, subdivision produced **0.0 days** of extra lead time (REAL: 70 both, 33 v2-only,
17 v3-only).

Had the mechanism worked and merely been outweighed by fragmentation, those shared
detections would still fire *earlier*. They do not. Subdivision fired on **fewer things,
not the same things sooner** — so this is a falsified mechanism, not an under-tuned one.

**Read the 13.3% correctly.** That is v2 **recomputed on the restricted 37-month embedded
set**, existing only to be a like-for-like comparator for v3. The headline result remains
the full-corpus measurement below. Never quote the 13.3/11.2 pair as the project's result.

### Published result — the differentiator

> **16.0% detection at a median 197-day lead, against 11.1% on a volume-matched placebo.**
> **1.44×, z ≈ 2.62, p ≈ 0.009.** Volume-anomaly detector, full silver corpus.

Modest, real, falsifiable, defended by a control arm — and now also defended by a
*published negative* on the obvious "just add embeddings" improvement.

**Phase 3 is not invalidated.** Hybrid retrieval is verified and load-bearing for the
agent's search tool (§4.3). Retrieval and clustering are different uses of the same
embeddings; only the clustering claim is retired.

**What it cost:** ~$5 of embeddings, one abandoned 85-minute HDBSCAN run (I-048), about
half a session. Cheap for closing the project's central open question 24 days out rather
than discovering it mid-demo.

---

## Next steps, in order

**Phase 9 is closed.** All measurement work is done; what remains is building the demo
surface and correcting the proposal.

1. **Phases 6 → 7 → 8 — the demo surface, and now the only critical path.** A dependent
   chain, all unstarted, 24 days out, nothing blocking them technically. This is where the
   remaining time should go.
2. **Correct the proposal against measured reality.** Three edits, all now evidenced:
   - §3/§6: the semantic-clustering claim is **falsified** (I-049) — remove or restate it.
     The differentiator is the 16.0% / 11.1% / 1.44× volume-anomaly result.
   - §8.3: the ~15 s CDF figure is no longer documented-not-measured — it is **7.1–15.6 s**.
   - §4.3: keep the hybrid-retrieval claim; it is verified and independent of the clustering
     result.
3. **The 989k-row exposure load** — a scope call, not a build task. See Open decisions.
   Needed for Phase 7's work queue.

**Verification discipline** (earned the hard way, I-043): never infer success from a
background task's exit code — re-check the live resource. The overnight index watcher
exited `0` while its own last line read `ready=False`. Also: don't add `-o json` to
`vector-search-indexes get-index`; it breaks the output, which is already JSON.

**Do not** attempt an index rebuild inside the demo window — it is most of a working day.
