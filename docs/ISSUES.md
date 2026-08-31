# FleetGuard — development issues log

Running record of every problem hit during the build, what actually caused it, and how it
was resolved. Kept because several of these were **silent** — they produced correct-looking
output while being wrong — and would cost the same time again if rediscovered.

**Conventions.** Newest first within each section. `Status` is one of `resolved`,
`open`, `watch` (resolved but could regress or recur). Anything marked **SILENT** produced
no error and passed the obvious check.

---

## Open / watch

| ID | Area | Issue | Status |
|---|---|---|---|
| I-028 | Phase 5 | **Lakebase CDF destination undecided — Phase 5 parked 2026-08-31.** Naming is settled: Postgres `fg_<entity>`, CDF output `lb_fg_<entity>_history`. What is *not* settled is where they live. The existing workspace CDF maps `databricks_postgres.bootcamp_students` → `bootcamp_students.bootcamp_cdc`, a schema owned by `zach@zachwilson.tech` holding 354 cohort tables — using it breaks the "own schemas only" rule and CDF is schema-level so all 11 tables would land there. The alternative is a mapping into `bootcamp_students.fleetguard` (owned), but **Lakebase CDF is UI-only** — not configurable via CLI or API — so it needs manual setup. Decide before creating any Postgres table: renaming later orphans the history table (see collision note below). | **open** |
| I-018 | Cost | **Sized (see I-025).** Embedding is ~275M tokens ≈ **$28–36 one-off** — not the problem. The AI Search *endpoint* is **~$403/month recurring** and is the real exposure. Mitigation is index lifecycle (billing stops 24h after the last index is deleted), not corpus trimming. Still open only as a decision on how long to leave the index up. | **open** |
| I-017 | Platform | Lakebase CDF is **not** a Declarative Automation Bundle resource, so Phase 5 enablement can't be captured in `bundle deploy`. Manual runbook step; CI/CD must not assume otherwise. | **watch** |
| I-016 | Platform | Table properties (retention, `VACUUM`) on Lakebase CDF sync-managed destination tables are undocumented — may not be settable. Fallback is a downstream Delta copy under our own retention. Confirm during Phase 5. | **open** |
| I-015 | Platform | Unity AI Gateway **output** guardrails (incl. PII detection on responses) do not apply to streaming responses. If the console streams agent output, the §4.5 PII second layer silently does not exist. Decide: no streaming, or drop the claim. | **open** |
| I-014 | Demo | `DO_NOT_DRIVE` (Park It) covers only 211 of 15,211 campaigns and is **zero for 2010–2011** — field added May 2025, backfilled unevenly. Seed demo data from 2015+ or the Park It path demos empty. | **watch** |
| I-013 | Docs | Diagrams drift from prose. Happened twice. Diagrams are now HTML (`docs/fleetguard_*.html`) specifically so they diff in review rather than being opaque binaries. | **watch** |

---

## Backtest

### I-027 — Volume anomaly alone is a weak discriminator (1.44× over placebo) — **SILENT**
*Date:* 2026-08-31 · *Status:* open — informs Phase 9

Ran the lead-time backtest early, since it needs only silver. Three successive results,
each of which would have been reported as a success if the next check hadn't been run:

| version | method | detection rate | median lead |
|---|---|---:|---:|
| v1 | earliest anomaly in 24-month window | 40.4% | **409 d** |
| v2 | sustained runs, run nearest open date | 16.0% | **197 d** |
| v2 + naive placebo | control = any never-investigated series | — | 160× separation |
| **v2 + volume-matched placebo** | **control matched on complaint volume** | **16.0% vs 11.1%** | **197 d vs 343 d** |

**What went wrong at each step.**
1. *v1's 409 days was an artifact.* Taking the earliest anomaly in a wide window produced a
   near-flat lead-time distribution — as many detections at the 630–719 day window edge as
   at 0–89 days — and earliest-vs-latest medians differed 4.6× (409 vs 89). A detector
   tracking a real ramp does not do that.
2. *The naive placebo's 160× separation was also an artifact.* Investigated series carry a
   median of 32 complaints; never-investigated series, 2. The control arm filled with
   series too small to ever trip `MIN_COUNT = 5`, so it could not fire by construction.

**The defensible result.** Against a volume-matched control, the detector fires on
investigated series **16.0%** of the time versus **11.1%** on matched never-investigated
series (two-proportion z ≈ 2.62, p ≈ 0.009). Real but modest — a **1.44× lift**, not the
160× the broken control implied.

**One genuinely positive signal.** Real detections cluster nearer the open date (median 197
days) while placebo detections scatter toward the window midpoint (343 days, ~half of the
24-month window). That is what a detector tracking a real ramp looks like, and it is not an
artifact of the detection-rate comparison.

**What this means for Phase 9.** §4.3 defines Model A as volume anomaly *combined with*
HDBSCAN over embeddings. This measures the volume half alone and shows it is **not
sufficient on its own** — the semantic half is load-bearing, not an enhancement. Knowing
this in week 1 rather than week 4 is the entire reason for running the backtest early.

The harness (`gold_lead_time_backtest`, `gold_lead_time_control`, `gold_lead_time_summary`)
is now the measurement instrument for that improvement, with a control arm built in.

---

## Pipeline (Phase 1 — chunking / AI Search sizing)

### I-026 — 512-token chunking is a near no-op on complaint narratives
*Date:* 2026-08-31 · *Status:* open (design decision)

Measured on `silver_complaint`: mean narrative 517 chars (~130 tokens), p95 1,477, max
**2,132** — `CDESCR` is `CHAR(2048)`, so a narrative physically cannot exceed ~530 tokens.
Only **1,390 of 2,209,123** rows (0.06%) could ever split at 512 tokens. Chunking complaint
narratives yields 1.0006 chunks per row.

Chunking is *not* pointless project-wide — it is genuinely needed elsewhere:

| source | mean chars | max | rows > 2,048 chars |
|---|---:|---:|---:|
| complaint narrative | 517 | 2,132 | 1,390 (0.06%) |
| recall defect description | 424 | 1,982 | 0 |
| TSB summary | 220 | 4,155 | 305 |
| **investigation summary** | **2,417** | **5,940** | **102,256 (66%)** |

So the chunker earns its place on investigation summaries, where two-thirds of rows exceed
a single chunk. Decision needed: keep `complaint_chunk` as a near-1:1 table (satisfies the
stated chunking requirement, keeps one uniform retrieval path, future-proofs for longer
sources), or index `silver_complaint.narrative` directly and chunk only the long sources.

### I-025 — Storage-optimized AI Search endpoint is the wrong choice at this scale
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** §4.3 specified a "storage-optimized endpoint given the vector count".

**Root cause.** The trade was backwards. Measured pricing: standard = 2M vectors/unit at
**$0.28/unit/hour**; storage-optimized = 64M vectors/unit at **$1.28/unit/hour**, minimum
one unit. At ~2.21M vectors, standard needs two units ($0.56/hr) versus storage-optimized's
one ($1.28/hr) — **2.3× more expensive for identical capability**. Storage-optimized only
wins past ~8M vectors, where standard would need five units.

**Second finding, more important.** The **recurring endpoint cost dominates the one-off
embedding cost by more than 10×**: ~$403/month for the endpoint versus ~$28–36 once for
embedding 275M tokens. The intuition that embedding is the expensive step is wrong here.
Endpoint billing stops 24 hours after the last index is deleted, so index lifecycle
management — not corpus trimming — is the lever that matters.

---

## Pipeline (Phase 1 — silver)

### I-024 — TSB rows are not TSB bulletins (same shape as I-010)
*Date:* 2026-08-31 · *Status:* resolved

`bronze_tsbs` holds 5,801,279 rows but only **258,438 distinct `NHTSA_ID`** values — each
bulletin repeats per make/model/year, ~22× on average. The 5.8M figure is legitimate as a
row count and as Volume evidence, but "5.8M service bulletins" would be wrong. Silver
exposes both grains so the distinction can't be lost downstream.

### I-023 — "Dedup on ODI number" would delete 27.9% of the complaint corpus — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** Proposal §4.2 specified silver "deduplication on ODI number".

**Root cause.** `ODINO` is not a row key. `CMPL.txt` states plainly: *"THIS NUMBER MAY BE
REPEATED FOR MULTIPLE COMPONENTS."* Measured: 2,240,289 rows, **1,615,482 distinct
`ODINO`** — deduplicating on it would discard **624,807 rows (27.9%)**, each a legitimate
distinct component report on a real complaint. Even `(ODINO, COMPDESC)` is not unique
(2,186,858 distinct), so 53,431 rows share both.

**Why it was dangerous.** The pipeline would have run clean, produced a plausible row
count, and quietly thrown away more than a quarter of the defect signal that Model A
clusters on — biased specifically against multi-component defects, which are the severe ones.

**Resolution.** `CMPLID` is the true row key (2,240,289 distinct = row count). Silver
deduplicates on `CMPLID` as a defensive guard against re-ingest, never on `ODINO`. `ODINO`
is retained as a complaint-group key for joining components of the same report. Proposal
§4.2 corrected.

---

## Pipeline (Phase 1 — bronze)

### I-022 — `DO_NOT_DRIVE` is `Yes`/`No`, not `YES`/`NO` — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** Bronze validation query `WHERE DO_NOT_DRIVE='YES'` returned **0** against an
expected 211 campaigns.

**Root cause.** Stored values are title-case `Yes` / `No`. The offline ground-truth parse
had applied `.strip().upper()`, so the discrepancy was in the *check*, not the data — row
counts matched exactly (2,128 `Yes` / 242,797 `No`).

**Why it matters.** A case-sensitive comparison on this column returns zero rows and looks
like "no Park It recalls exist" rather than like a bug. Silver must normalise case; any
Park It predicate uses `UPPER(...)`.

### I-021 — `CF_PARTITON_INFERENCE_ERROR` on Auto Loader
*Date:* 2026-08-31 · *Status:* resolved

Auto Loader attempts Hive-style partition discovery on the source directory. These
directories have no `key=value` partitioning, and inference fails. Fixed by passing
`partitionColumns => ''` explicitly on every `read_files` call.

### I-020 — Auto Loader requires a directory, not a file
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** `Input path s3://.../FLAT_CMPL.txt is not a directory`.

**Root cause.** `STREAM read_files('<dir>/FILE.txt')` is invalid — Auto Loader monitors a
directory for new files.

**Resolution.** Volume reorganised into per-source subdirectories (`cmpl/`, `rcl/`, `inv/`,
`tsbs/`), which is better design anyway: four different schemas no longer share one
listing, and TSB chunks can be added without a pipeline change. The ingest notebook gained
an idempotent `migrate_legacy_layout()` that moves root-level files server-side — switching
layouts cost a rename rather than a 2.6 GB re-download, since the watermark would otherwise
have returned 304 and never re-placed the files.

**Follow-on.** The TSBS flow then failed on a stale Auto Loader checkpoint still pointing
at the old path. Fixed with a *selective* full refresh:
`start-update --json '{"full_refresh_selection": ["bronze_tsbs"]}'`. Note the CLI's
`--full-refresh` flag is a **boolean**, not a table list, and `--full-refresh-all` blocks
rather than returning an update id.

### I-019 — `DELTA_CLUSTERING_COLUMN_MISSING_STATS`
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** Pipeline failed at `SETTING_UP_TABLES`: liquid clustering could not find
cluster column `_ingest_date` in the stats schema.

**Root cause.** Delta collects file statistics on the **first 32 columns** only. Appending
the metadata columns after 51 data columns left `_ingest_date` at position ~56, outside the
stats window.

**Resolution.** Metadata columns moved to the **front** of the `SELECT` in all four bronze
definitions. This also avoids collecting stats on the wide free-text columns (`CDESCR` is
2,048 chars), which is desirable independently.

**Gotcha.** The tables had already been created with the old column order, so the fix did
not take until they were dropped — the error message kept showing the *stored* schema, not
the new one. When a clustering or schema change doesn't appear to apply, check whether a
prior failed run already materialised the table.

---

## Data & parsing

### I-012 — `read_files` silently mis-parses the ODI flat files — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** In-workspace `read_files` reported `PROD_TYPE = 'V'` on 2,168,077 rows;
the offline ground-truth parse said 2,168,220. 143 rows unaccounted for.

**Root cause.** The ODI flat files are tab-delimited with **no quoting convention**, but
complaint narratives are free text containing `"`. Spark's CSV reader treats `"` as a quote
character by default, swallowing tab delimiters and shifting fields.

**Why it was dangerous.** `_rescued_data` was **0 both with and without the fix**. The
proposal leaned on "zero rescued rows" as its schema-stability evidence — that check passes
while the data is quietly wrong.

**Resolution.** All `read_files` calls use `quote => '\0'` alongside `sep => '\t'`,
`header => false`, `encoding => 'ISO-8859-1'`. Ingest validation now asserts against known
column cardinalities (`PROD_TYPE` counts, distinct investigation count = 5,344) rather than
trusting the rescue column. Recorded in `CLAUDE.md` and proposal §3.

### I-011 — "Deterministic VIN-range match" is not implementable
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** §2 and §7 specified scope matching as a deterministic VIN-range check.

**Root cause.** Three independent blockers: `FLAT_RCL_POST_2010` has **no VIN-range
columns** (scopes by make/model/year + `BGMAN`/`ENDMAN`); complaint `VIN` is `CHAR(11)`, a
partial that identifies nothing; and `api.nhtsa.gov/recalls/recallsByVin` returns 403 —
NHTSA publishes no VIN→recall lookup.

**Resolution.** Reframed as deterministic set membership on `(make, model, model_year)`
intersected with the manufacture-date window. Model B now scores *residual* ambiguity
(string variants, missing manufacture dates) rather than performing the match. The
"no LLM in the severe path" guarantee is unchanged and no longer rests on a false claim.

### I-010 — Backtest population overstated by two orders of magnitude
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** §3 and §10 cited "154,367 investigation rows" as the backtest evidence base.

**Root cause.** 154,367 is a **row** count; INV rows are make/model/year granular. Distinct
investigations: **5,344**. Post-2010: **777**. With ≥30 prior-year complaints: **497**.

**Resolution.** Proposal now states the distinct-investigation count and the 497-item
working set. Confirmed independently in-workspace via `read_files`
(`COUNT(DISTINCT _c0) = 5344`).

### I-009 — `ai_extract` over 2.24M rows, partly redundant
*Date:* 2026-08-31 · *Status:* resolved

**Root cause.** §4.2 ran `ai_extract` in silver to derive component from prose — but
`COMPDESC` (field 12) already ships as a populated structured component field.

**Resolution.** Component comes from `COMPDESC`. `ai_extract` moved to per-surfaced-cluster
(hundreds of rows, Phase 9) rather than per-complaint. Moves the cost out of the ingest path
where it gated every downstream phase.

### I-008 — TSB corpus undercounted 2.4×
*Date:* 2026-08-31 · *Status:* resolved

**Root cause.** The cited "2.4M rows" is exactly the `TSBS_RECEIVED_2020-2024` chunk
(2,406,749). Someone measured one file and labelled it the corpus. Actual total across all
seven chunks: **5,801,279**.

### I-007 — `ETag` advertised but ignored — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** `static.nhtsa.gov` returns both `Last-Modified` and `ETag` on `HEAD`.

**Root cause.** `If-Modified-Since` works correctly (`304`, 0 bytes). `If-None-Match` with
the **exact advertised ETag** returns `200` and the full 370 MB body. The ETag is published
and ignored.

**Why it was dangerous.** ETag-based change detection would look correct in review and
silently re-download ~2 GB on every poll.

**Resolution.** Change detection uses `If-Modified-Since` only. Verified live: on re-run,
`FLAT_CMPL` and TSBS returned 304 and were skipped.

### I-006 — Recalls API URL was dead
*Date:* 2026-08-31 · *Status:* resolved

`api.nhtsa.gov/recallsByVehicle` returns **403**. Correct path includes the `/recalls`
segment: `api.nhtsa.gov/recalls/recallsByVehicle`. `CLAUDE.md` had it right; the proposal
did not. Third dead-URL incident on this project.

### I-005 — Heavy-truck vPIC decode assumed weak; it isn't
*Date:* 2026-08-31 · *Status:* resolved

§3 carried a build note warning that Class 8 decode would be incomplete. Measured across
Freightliner, Peterbilt, Kenworth, Mack, Volvo, International: make, model, year,
`Truck-Tractor`, and `Class 8: 33,001 lb and above` all resolve. Note inverted.

**Secondary finding:** vPIC returns full attributes even when the check digit fails
(`ErrorCode 1`). Do **not** gate decode success on `ErrorCode == 0`.

---

## Platform & tooling

### I-004 — `CANNOT_DETERMINE_TYPE` on the 304 path
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** Ingest job succeeded on first run, failed on second with
`[CANNOT_DETERMINE_TYPE] Some of types cannot be determined after inferring`.

**Root cause.** On the 304 branch, `etag` and `landed_file` are both `None`.
`spark.createDataFrame([Row(...)])` cannot infer a type for an all-null column.

**Resolution.** Explicit `StructType` on the watermark write. Note the failure mode: only
surfaced on the **second** run, because the first downloaded everything. Re-running a job
after its state changes is a distinct test, not a repeat of the first.

### I-003 — Databricks Apps OBO scope names were stale
*Date:* 2026-08-31 · *Status:* resolved

Recorded scopes `dashboards.genie`, `files.files`, `iam.access-control:read`,
`iam.current-user:read` were wrong. Current vocabulary: `ai-gateway`, `apps`, `files`,
`genie`, `model-serving`, `postgres`, `sql`, `vector-search`, `sql:restricted-query`, plus
`catalog.*` / `workspace.*` SDK scopes with `:read` modifiers. Useful consequence: the Apps
phase can scope narrowly, unlike the Render phase where `all-apis` is the only documented
option for a custom OAuth app integration.

### I-002 — Product-name drift
*Date:* 2026-08-31 · *Status:* resolved

- "Databricks Asset Bundles" → **Declarative Automation Bundles** (CLI still `databricks bundle`).
- Diagrams said **"Agent Bricks"**; the project uses **Mosaic AI Agent Framework**. Different
  products — Agent Bricks is Knowledge Assistants / Supervisor Agents.
- Confirmed *not* stale, do not "fix" these: **"AI Search"** and **"Unity AI Gateway"** are
  both current names.

### I-001 — Workspace is a shared metastore, not a private one
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** The `abhi` profile's catalogs are owned by other people.

**Root cause.** It is a shared DataExpert.io bootcamp metastore. `main` (owner
`zach@zachwilson.tech`), `tabular` (`gudetayared@gmail.com`), and `bootcamp_students`
(`eumardassis@gmail.com`) hold ~296 schemas belonging to other students. Catalog creation
is unavailable.

**Resolution.** Project owns exactly one schema: **`bootcamp_students.fleetguard`**.
Medallion layers are table-name prefixes (`bronze_`/`silver_`/`gold_`), not sibling schemas.
Hard rule: never write outside it. Also note the `abhi` OAuth session had expired and needed
`databricks auth login` before any work.

---

## Process

### I-000 — Lead-time interval conflation — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** §3 stated "median gap of 118 days" in the section establishing the evidence
base for early detection.

**Root cause.** 118 days is the **investigation-open → recall-issued** interval — regulatory
latency. FleetGuard's claim is **complaint-accumulation → investigation-open**, a different
measurement that is not yet made. Presenting the former where the latter belongs implied a
result the system has not demonstrated.

**Resolution.** §3 now tabulates all three intervals and marks the only FleetGuard claim as
an unmeasured Phase 9 target, supported by the signal that does exist (674/777 post-2010
investigations have prior complaints, median 341 in the prior year). §6 states the
regulatory interval is never reported as a system result.

**Lesson.** A real, correctly-measured number placed in the wrong slot is more dangerous
than no number — it survives casual review precisely because it is genuine.
