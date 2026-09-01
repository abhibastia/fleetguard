# FleetGuard — architecture

**Living document. Must be true now.** Last reconciled against the workspace: **2026-09-01**.

Companion documents, each with one job:

| Document | Job | Lifecycle |
|---|---|---|
| [`FleetGuard_Proposal.md`](FleetGuard_Proposal.md) | What was proposed, before the build | **Frozen** at 2026-08-31 |
| **This file** | What the system *is* | Living — update with the code |
| [`STATUS.md`](STATUS.md) | Where the build has got to | Living, high-churn |
| [`ENHANCEMENTS.md`](ENHANCEMENTS.md) | Evaluated backlog — adopt/defer/reject, with reasons | Living |
| [`ISSUES.md`](ISSUES.md) | Every problem hit, root cause, resolution | Append-only |
| [`../PLAN.md`](../PLAN.md) | Phase sequencing and definitions of done | Living |
| [`../CLAUDE.md`](../CLAUDE.md) | Verified facts that must not be re-derived | Living |

Every number below is measured against the live workspace. Anything not yet measured is
marked **(planned)** and carries no figure.

---

## 1. What FleetGuard does

A fleet operator running thousands of vehicles learns about safety defects the same way a
private owner does — when a recall posts. FleetGuard delivers two capabilities against
NHTSA's public defect corpus.

**Reactive — recall response.** A campaign posts; FleetGuard resolves its scope against the
VIN roster, ranks exposure by depot and severity, and issues work orders under human
approval. Deterministic, and the demo's spine.

**Proactive — early warning.** Detect a complaint pattern before NHTSA opens an
investigation. **Measured: 16.0% of investigations detected at a median 197-day lead,
against 11.1% on a volume-matched placebo** (1.44×, z ≈ 2.62, p ≈ 0.009).

> **Precision about what is predicted.** The system predicts **that NHTSA will open an
> investigation**. It does *not* predict recall issuance, and it does not identify affected
> VINs. Investigation → recall is separate regulatory latency (median 118 days across 886
> campaigns) — real context, **never reported as a system result**. These three intervals
> have been conflated once already; keep them apart.

---

## 2. Build state

| Phase | State |
|---|---|
| 1 Ingestion + medallion | 🟡 ~90% — `gold_emerging_cluster` descoped by §6 findings |
| 2 Fleet registry | ✅ Done |
| 3 Chunking + AI Search | ✅ Done — verified at full corpus |
| 4 Model B + golden set | ⬜ Not started |
| 5 Lakebase + CDF | ✅ Done — loaded and latency-measured |
| 6 OAuth wiring | ⬜ Not started |
| 7 Agent tools + write path | ⬜ Not started |
| 8 App + external surface | ⬜ Not started |
| 9 Model A + backtest | ✅ Done — **result is negative**, see §6 |
| 10 Governance · 11 Hardening | ⬜ Not started |
| 12 Second connector | ❌ Cut for schedule |

Everything lives in one schema, `bootcamp_students.fleetguard`, inside a **shared** bootcamp
metastore. Catalog creation is unavailable, so **medallion layers are table-name prefixes**
(`bronze_`, `silver_`, `gold_`), not sibling schemas.

---

## 3. Data sources — all verified live

| Source | Artefact | Measured |
|---|---|---|
| Complaints | `FLAT_CMPL.zip` | 2,240,289 rows, 51 fields |
| Recalls | `FLAT_RCL_POST_2010.zip` | 244,925 rows / **15,211 campaigns** |
| Investigations | `FLAT_INV.zip` | 154,367 rows / **5,344 investigations** |
| TSBs | `TSBS_RECEIVED_<range>.zip` × 7 | 5,801,279 rows |
| vPIC | `DecodeVINValuesBatch` | Authoritative for make/model/year |
| Recalls API | `api.nhtsa.gov/recalls/recallsByVehicle` | 200/200 fleet combos |

**Traps that cost real time** (full detail in `CLAUDE.md`):

- `FLAT_RCL.zip` is a dead S3 key → 404. Use `FLAT_RCL_POST_2010.zip`.
- `api.nhtsa.gov/recallsByVehicle` **without** the `/recalls` segment returns 403. There is
  **no VIN→recall lookup**; `recallsByVin` 403s.
- The host advertises an `ETag` and **ignores** `If-None-Match`. Only `If-Modified-Since`
  works — building on the ETag re-downloads 2.6 GB per run while looking correct.
- Row counts are entity-vs-row traps: 154,367 investigation *rows* are 5,344
  *investigations*. Never quote a row count when you mean entities.

---

## 4. Pipeline

```
static.nhtsa.gov ──► Volume (per-source subdirs) ──► bronze_* ──► silver_* ──► gold_* ──► Lakebase ──► CDF ──► UC
   If-Modified-Since      Auto Loader needs a         4 tables    + quarantine   fleet +    Postgres   7–16 s
   only                   DIRECTORY, not a file       8.44M rows  reconciled     backtest   11 tables
```

### 4.1 Ingestion

Manual-trigger by design — no schedule, to avoid consuming shared-workspace compute before
the demo. Scheduling is Phase 11.

Change detection is `If-Modified-Since` against `ops_ingest_watermark`. Files land in
**per-source subdirectories** (`cmpl/`, `rcl/`, `inv/`, `tsbs/`) because Auto Loader
monitors directories, not files.

### 4.2 Bronze → Silver (Lakeflow Declarative Pipeline)

**Every `read_files` call must set `quote => '\0'`.** The ODI files are tab-delimited with
no quoting convention, but narratives contain `"`. Spark's default quote handling swallows
tabs and shifts fields — measured at **143 rows silently corrupted**, with `_rescued_data`
reading **0 in both cases**. Zero rescued rows is necessary, not sufficient; validate
against known cardinalities instead.

Quality control **routes rather than drops**: failure reasons are computed once in a staging
view and drive both the silver and quarantine predicates, so the two cannot drift.

| Grain | Bronze | Silver | Quarantine |
|---|---|---|---|
| Complaints (V+T scope) | 2,209,695 | 2,209,123 | 572 |
| Recalls | 244,925 | 244,701 | 224 |
| Investigations | 154,367 | 154,191 | 176 |

`bronze = silver + quarantine` on every table. Entity-grain: `silver_investigation_case`
**5,233** investigations (**777** opened 2010+, the backtest population);
`silver_tsb_bulletin` **258,438** bulletins.

### 4.3 Gold — fleet registry

`gold_fleet_vehicle` 20,000 · `gold_fleet_depot` 60 · `gold_fleet_exposure` 989,042.

VIN prefixes come from real complaint VINs with the check digit recomputed, but
**make/model/year always come from vPIC** — complaint VINs are dirty (`!FTEW1EG2GK`, GMC
WMIs labelled RAM). 400 generated VINs independently verified: 400/400 exact.

> **Exact matching is insufficient, and the demo must say so.** Only 91 of 163 fleet
> combinations match a campaign exactly. `MODEL_VARIANT` matches (725,356) outnumber `EXACT`
> (263,686) roughly 3:1 — all 2,116 F-250s match only as variants, because `F-250 SD` is
> NHTSA's dominant spelling. `match_basis` carries the tier, and the deterministic guarantee
> applies to **`EXACT` only**.

### 4.4 Retrieval

`complaint_chunk_idx` on endpoint `fleetguard-vs` — Delta Sync, **HYBRID**,
`databricks-gte-large-en` (1024-dim), **1,746,601 chunks**, matching source exactly.

Verified at full corpus: hybrid differs from pure ANN on 2 of 3 probe queries, harm-filtered
retrieval passes 10/10, and near-duplicate retrieval is a non-issue (10/10 distinct
`complaint_id`) — so the agent's search tool needs no read-time dedupe.

**This is the load-bearing use of embeddings.** See §6 for the use that failed.

### 4.5 Operational store — Lakebase + CDF

11 Postgres tables, `fleetguard_<entity>`, every one `REPLICA IDENTITY FULL` (a hard CDF
prerequisite — without it the WAL carries only the key and `update_preimage` is useless).

CDF replicates to `bootcamp_students.bootcamp_cdc` as `lb_fleetguard_<entity>_history`. All
11 exist with exact names and **no `_1` collision suffixes**.

- **CDF replicates DDL**, so destinations appear at `CREATE TABLE`, not on first write.
- **Capture latency: 7.1–15.6 s** (n=3, all true measurements). State it as a range
  consistent with a ~15 s flush; size demos against the **15.6 s worst case**.
- CDF configuration is **UI-only** — no CLI, no API, not a bundle resource. It is a manual
  runbook step in any rebuild.

---

## 5. Models

**Model A — emerging defect detector.** Volume anomaly against each series' own trailing
history, plus harm weighting. A detection is a **sustained run** of ≥2 consecutive firing
months, dated at the run *nearest* the investigation open date.

> Taking the *earliest* run in the window instead produced a 409-day median that was pure
> artefact — as many detections at the window edge as near the open date. The nearest-run
> rule is load-bearing, not a detail.

Harm is a **smoothed severity multiplier with shrinkage toward the component base rate**,
never a raw sum: 96.0% of complaints report zero injuries, so an unsmoothed weight
degenerates into a fatality lookup.

**Model A does not use clustering.** See §6.

**Model B — recall-to-fleet matcher (planned).** Scores the `MODEL_VARIANT` residual that
exact matching misses. Not built.

---

## 6. The measured result, including what failed

### Published result

> **16.0% of 777 post-2010 investigations detected, median 197-day lead.**
> **Placebo: 11.1% of 606, median 343 days.** 1.44×, z ≈ 2.62, p ≈ 0.009.

The **shape** is better evidence than the rate: real detections cluster near the open date
(197 days) while placebo detections scatter toward the window midpoint (343 days) — what a
detector tracking a genuine ramp looks like, rather than one firing on background variance.

### Honest limits

- **It misses ~5 of every 6 investigations.** 124 of 777.
- The control arm fires at 11.1%, so a majority of detections would have occurred on a busy
  series with no defect. A 1.44× edge, not an oracle.
- The placebo covers 606 of 777 — 171 investigations had no volume-matched
  never-investigated series available. Rates are per-arm, so the comparison holds, but the
  control is a **78% subset, not a mirror**.

### The semantic hypothesis was tested and falsified

The proposal claimed semantic clustering would surface defects earlier, and that *"the
semantic half is load-bearing rather than an enhancement."* Both were tested.

| grouping | REAL | PLACEBO | lift |
|---|---|---|---|
| Component (v2) | 13.3% | 10.7% | 1.24× |
| Semantic (v3) | **11.2%** | 8.9% | 1.26× |

Detection **fell**. And on the **70** investigations both groupings detect, subdivision
produced **0.0 days** of extra lead.

**That zero is decisive.** Had the mechanism worked and merely been outweighed by
fragmentation, shared detections would still fire earlier. They don't. Subdivision fired on
*fewer things*, not the same things *sooner* — a falsified mechanism, not an under-tuned one.

> The 13.3/11.2 pair is **v2 and v3 recomputed on the restricted 37-month embedded set**, so
> the comparison isolates the grouping change. **Never quote it as the project's result.**

HDBSCAN was abandoned before this: it labelled **85% of embeddings noise** at every
parameterisation tried, and normalisation — the obvious suspect — changed noise by 0.4
points. Complaint narratives are a continuum of phrasings, not density islands.

**What survives.** Clustering failed as a *detector*, not as an *aggregator*. k-means
subdivision assigns every complaint to a coherent group, which remains valid for explaining
a signal and for keeping `ai_extract` spend proportional to what an operator sees.

---

## 7. Invariants

Things that must stay true. Each is enforced by a test, an expectation, or a constraint —
and each was violated at least once.

| Invariant | Enforced by |
|---|---|
| `bronze = silver + quarantine` on every table | Integration test + pipeline expectations |
| Complaint VIN is an 11-char **partial**, never an identifier | Design; `vin.py` guards shape |
| Fleet make/model/year comes from **vPIC**, never from complaint VINs | Fleet registry build |
| `DO_NOT_DRIVE` compared with `UPPER(...)` | Stored title-case `Yes`/`No`; a case-sensitive predicate silently returns zero rows |
| `read_files` sets `quote => '\0'` | All bronze SQL |
| Chunk count is window-driven, not stride-driven | `chunking.py` + regression test |
| Every Lakebase table `REPLICA IDENTITY FULL` | Creation script refuses to commit otherwise |
| Project tables are `fleetguard_`-prefixed | Name guard; shared schema |
| Backtest population is exactly **777** investigations | Assertion in scope build |
| No detection dated on or after its investigation opened | Integration test |

---

## 8. Non-goals

Stated so they are not mistaken for omissions.

- **No VIN-level recall matching.** `FLAT_RCL` has no VIN-range columns; campaigns scope by
  make/model/year + manufacture window. A "VIN-range match" cannot be built on this data.
- **No pre-2010 recall coverage.** Deliberate — operators don't run vehicles that old.
- **No `ai_extract` on the ingest path.** `COMPDESC` already ships structured; re-deriving it
  across 2.24M rows is pure cost.
- **No streaming agent output** while the PII output guardrail is claimed — AI Gateway output
  guardrails do not apply to streaming responses. Pick one.
- **No writes outside `bootcamp_students.fleetguard`**, with one explicitly authorised
  exception: the CDF destination `bootcamp_students.bootcamp_cdc`.

---

## 9. Operations

**Cost.** `fleetguard-vs` (AI Search, STANDARD, 1 unit) at **~$6.72/day** is the only
recurring charge. Everything else is manual-trigger. Billing stops 24 h after the last index
is deleted. Embedding was ~$5 one-off.

**Rebuild.** `src/setup/00_create_all_objects.py` creates the foundations nothing else
creates, prints a 14-step rebuild order naming each producer, and verifies present-vs-expected
across 34 objects. It is deliberately **not** pure DDL: most tables here are derived, and
`CREATE TABLE` for `silver_complaint` would yield an empty table with the right name — a
rebuild that looks successful and isn't.

**Manual steps no script covers:** Lakebase CDF enablement (UI-only), and AI Search
endpoint/index creation (kept manual because it is the only recurring cost — it should never
be resurrected by accident). An index rebuild is ~7 h; never attempt one inside a demo window.

**Verification discipline.** Never infer success from a CLI exit code. Three variants have
been observed in one day: a watcher exiting `0` at 51% complete, `jobs run-now` returning `0`
for a `FAILED` run, and the CLI reporting `Error: timed out` while the job ran on healthily.
**Only `state.result_state` describes the job.**

---

## 10. Testing

Three layers, deliberately separate:

| Layer | Coverage | Run |
|---|---|---|
| Unit | 91 tests, no Databricks | `pytest` |
| Pipeline expectations | In-pipeline, `_dq_failures` quarantine split | With the pipeline |
| Data quality | 21 tests against the live workspace | `pytest -m integration --run-integration` |

Logic that has been wrong once lives in `src/fleetguard/` (`vin.py`, `chunking.py`,
`naming.py`) so it is testable off-platform, with each past bug encoded as a named
regression. The data-quality tests deliberately **do not trust `_rescued_data`** — they
assert measured cardinalities instead.
