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
| I-028 | Phase 5 | **RESOLVED 2026-08-31.** User confirmed authorisation to use the existing CDF mapping `databricks_postgres.bootcamp_students` → `bootcamp_students.bootcamp_cdc`. Naming decided as `fleetguard_<entity>` → `lb_fleetguard_<entity>_history` (see I-036). Phase 5 unparked. | ✅ resolved |
| I-018 | Cost | **Sized (see I-025).** Embedding is ~275M tokens ≈ **$28–36 one-off** — not the problem. The AI Search *endpoint* is **~$403/month recurring** and is the real exposure. Mitigation is index lifecycle (billing stops 24h after the last index is deleted), not corpus trimming. Still open only as a decision on how long to leave the index up. | **open** |
| I-017 | Platform | Lakebase CDF is **not** a Declarative Automation Bundle resource, so Phase 5 enablement can't be captured in `bundle deploy`. Manual runbook step; CI/CD must not assume otherwise. | **watch** |
| I-016 | Platform | Table properties (retention, `VACUUM`) on Lakebase CDF sync-managed destination tables are undocumented — may not be settable. Fallback is a downstream Delta copy under our own retention. Confirm during Phase 5. | **open** |
| I-015 | Platform | Unity AI Gateway **output** guardrails (incl. PII detection on responses) do not apply to streaming responses. If the console streams agent output, the §4.5 PII second layer silently does not exist. Decide: no streaming, or drop the claim. | **open** |
| I-014 | Demo | `DO_NOT_DRIVE` (Park It) covers only 211 of 15,211 campaigns and is **zero for 2010–2011** — field added May 2025, backfilled unevenly. Seed demo data from 2015+ or the Park It path demos empty. | **watch** |
| I-013 | Docs | Diagrams drift from prose. Happened twice. Diagrams are now HTML (`docs/fleetguard_*.html`) specifically so they diff in review rather than being opaque binaries. | **watch** |

---

## Tooling / process

### I-047 — A probe that tests a simpler expression than production proves nothing
*Date:* 2026-09-01 · *Status:* resolved

Before spending ~30M tokens embedding 205,219 narratives, the plan was deliberately to
probe `ai_query` on a few rows first. The probe passed — 1024-dim vectors, exactly as
wanted. The full job then failed immediately:

```
[DATATYPE_MISMATCH.CAST_WITHOUT_SUGGESTION] Cannot resolve "embedding":
cannot cast "STRUCT<..., errorMessage: STRING>" to "ARRAY<FLOAT>"
```

**Cause:** `failOnError => false` changes `ai_query`'s **return type**. Instead of the bare
`returnType`, it yields `STRUCT<result: ARRAY<FLOAT>, errorMessage: STRING>`. The probe had
omitted `failOnError`, so it exercised a *different expression* from the one production
ran — and passed for that reason.

**Fix:** unpack the struct, and keep `errorMessage` as a stored column so a swallowed
failure is visible as text rather than inferred from a `NULL`:

```sql
SELECT r.result AS embedding, r.errorMessage AS error_message
FROM (SELECT ai_query(..., returnType => 'ARRAY<FLOAT>', failOnError => false) AS r ...)
```

Note `errorMessage` is `''` (empty string), not `NULL`, on success — so failures are
detected by `embedding IS NULL`, not by the message being null.

**Practice adopted:** a pre-flight probe must run the **exact expression**, with every
option the production call uses. A simplified probe tests a different thing and its passing
is not evidence. This one cost only a fast failure because the job dies at planning time —
but the same mistake in an expression that *runs* would have spent the full budget before
surfacing.

### I-046 — A latency probe that never sees the row absent has measured nothing
*Date:* 2026-09-01 · *Status:* resolved · **CDF latency now measured**

First attempt at the §8.3 capture-latency measurement reported **21.55 s** with
`polls = 1`. It found the probe row on its *first* check, so it never observed the row
absent — that is an **upper bound, not a measurement**, and most of the 21.55 s was Spark's
own cold query-startup cost rather than CDF. Quoted as "measured latency" it would have
been wrong in both directions at once: too slow (it included startup) and unfounded (it
never bracketed the event).

A related trap, avoided from the start: the history table's `_timestamp` is the **Postgres
commit** timestamp, not the moment the row became queryable in Delta. Differencing it
against the writing client's clock yields ~0.3 s — a flattering number that measures clock
skew, not replication.

**Fixes, all three needed:** warm the query path with the exact query shape before the
clock starts (steady-state cost printed, so the resolution floor is visible — 0.57 s here);
poll tightly (0.5 s); and require `observed_absent` before treating a run as a measurement,
recording `is_upper_bound = true` otherwise.

**Result (3/3 true measurements, `ops_cdf_latency`):**

| probe | latency | polls |
|---|---|---|
| 1 | 7.13 s | 5 |
| 2 | 14.74 s | 13 |
| 3 | 15.63 s | 14 |

Range **7.1–15.6 s**, mean **12.5 s**. The spread is not noise — it is the signature of a
**periodic ~15 s flush**: a write lands wherever it falls in the current window, so latency
scatters up to the flush interval. This corroborates Databricks' documented ~15 s as a
*flush interval*, not as a typical latency.

**How to state it:** "measured 7–16 s end to end, n=3, consistent with a ~15 s flush" —
never a single averaged number, and never below the observed floor. Databricks publishes no
SLA here, so the worst observed case (15.6 s) is the one a demo should be sized against.
This comfortably supports §8.3's **sub-minute** claim, which is what the proposal actually
needs.

### I-045 — `psycopg[binary]` 3.3.5 aborts the serverless kernel: FIPS self-test failure
*Date:* 2026-09-01 · *Status:* resolved

`fleetguard-load-reference-from-gold` died with `SIGABRT` (exit 134) inside
`psycopg.pq.import_from_libpq` — at `import psycopg`, before any project code ran.
Deterministic across two runs.

**The misleading part:** `fleetguard-create-remaining-tables` uses the *identical*
environment spec (`psycopg[binary]`, `databricks-sdk>=0.89.0`, client 3) and was re-run
**the same day as a control — it passed.** That looks like it exonerates the spec. It does
not. A serverless environment is resolved and **cached per job**: job 08's was built before
psycopg 3.3.5 shipped, the new job's was built after. Identical spec text, two different
resolved builds. Nothing in the repo changed; the dependency moved underneath it.

**Root cause** (from `ops_psycopg_probe`): psycopg-binary 3.3.5 bundles its own OpenSSL,
which aborts on load in this environment with

```
crypto/fips/fips.c:154: OpenSSL internal error: FATAL FIPS SELFTEST FAILURE
```

**Fix:** select the pure-Python implementation, which uses the *system* libpq (16.0.15)
and loads cleanly — verified `IMPORT OK 3.3.5 libpq 160015`, returncode 0:

```python
import os
os.environ.setdefault("PSYCOPG_IMPL", "python")
import psycopg   # must come after
```

Chosen over pinning a version because a pin only holds until someone rebuilds, and because
picking a "known-good" version would have meant guessing at one. Applied to notebooks 08
and 10. **Caveat:** the pure implementation is slower than the C one, which matters for the
989k-row exposure `COPY` — measure before assuming the reference-load rate carries over.

**Technique worth reusing:** an abort in the notebook kernel destroys the output that would
explain it. `11_probe_psycopg_env.py` imports in a **subprocess**, so the crash is
contained and its stderr survives — that is the only reason the FIPS line was recoverable.
And a probe must **persist** its findings: `get-run-output` returns an empty
`notebook_output` unless the notebook calls `dbutils.notebook.exit()`, so the first probe
run succeeded with its diagnosis stranded in the run page.

### I-044 — Wrong claim: CDF materialises history tables on DDL, not on first write
*Date:* 2026-09-01 · *Status:* resolved (claim corrected)

Notebook 08 asserted, and `STATUS.md` repeated, that *"CDF creates a destination table on
the first write, not on `CREATE TABLE`, so the ten new history tables appear once rows are
inserted."* **This is false.** Measured 2026-09-01, before any row was written to ten of
the eleven tables:

```
SHOW TABLES IN bootcamp_students.bootcamp_cdc LIKE 'lb_fleetguard*'   -> 11 tables
lb_fleetguard_depot_history      63 rows   (60 insert + update pre/post + delete, = I-038)
lb_fleetguard_vehicle_history     0 rows   <- exists, empty
lb_fleetguard_recall_campaign…    0 rows   <- exists, empty
```

CDF replicates the **DDL**. All eleven destinations existed the moment the `CREATE TABLE`s
committed, with exact names and **no `_1` collision suffixes**.

**Consequences.** The naming decision (I-036) is proven for all eleven tables, not just the
one round-tripped in I-038 — the largest naming risk is fully retired. And the stated
first rationale for the reference load ("writing rows materialises them") was void; the
load is still needed, but for **data**, which is a different justification and was corrected
rather than quietly kept.

This is the class of error the project conventions exist for: a plausible,
confidently-stated platform behaviour that nobody checked because nothing depended on it
being true — until it did.

### I-043 — **SILENT** Index watcher exited `0` with `ready=False`; "completed" ≠ "ready"
*Date:* 2026-08-31 · *Status:* resolved (practice changed)

The background watcher polling the AI Search index sync finished and reported
`completed (exit code 0)`. It had **not** observed the index becoming ready — it ran a
fixed 200 iterations at ~60 s and exited on the *iteration cap*. The final logged line was
`22:49:46 ready=False indexed=899650`.

**Confirmed general on 2026-09-01:** this is not specific to the watcher. `databricks jobs
run-now` also returns **exit 0 for a run whose `result_state` is `FAILED`** — seen twice
with `fleetguard-load-reference-from-gold` (`INTERNAL_ERROR / FAILED`, exit 0). Always read
`state.result_state` from `jobs list-runs`; never trust the CLI's exit status.

Exit `0` here means "the loop finished counting", not "the sync finished". Read as the
latter — which is the natural reading of a green completion notice — it would have put
"index ready" into `STATUS.md` while the index was at 51% of its corpus, and the next
session would have run the full-corpus hybrid test against a partial index and drawn
conclusions from it.

Measured at 23:47 the same evening: **1,130,850 of 1,746,601 chunks (64.7%), `ready:
false`**, sustaining ~4,000 rows/min. Roughly 2.5 h still to run.

**Root cause:** a bounded `for` loop with the ready-check as a `break`, and no distinct
exit status for "cap reached" versus "condition met".

**Practice adopted:** a watcher must encode its own verdict in its exit status — non-zero
(or a loud final line) when it times out without the condition being met. Never infer
success from a background task's exit code alone; re-check the live resource. Same family
as I-012 (`_rescued_data` = 0 not proving a clean parse): the green signal was necessary,
not sufficient.

Also noted: `databricks vector-search-indexes get-index` returns JSON by default, but
adding `-o json` produced unparseable output. Drop the flag. (Distinct from the
`query-index` Go SDK unmarshal bug noted in `src/search/09_hybrid_query_test.py`.)

### I-042 — Blind `sed`/`str.replace` edits caused three silent no-ops and one real bug
*Date:* 2026-08-31 · *Status:* resolved (practice changed)

Four incidents in one session, all from editing code by blind string substitution:

1. Three `str.replace()` calls silently matched nothing — the formatter had reflowed the
   target — while the script still printed "updated". The change appeared applied, the
   notebook re-ran unchanged, and the missing result looked like a platform problem.
2. A `sed` renaming an unused loop variable `src` → `_src` matched **both** loops in
   `migrate_legacy_layout()`. The second loop's body uses `src`, so the ingest job would
   have raised `NameError` at runtime. Lint was happy; only reading the diff caught it.

**Practice adopted:** use the `Edit` tool, which fails loudly when the target is absent,
rather than `str.replace`/`sed` which return silently on no match. When a shell edit is
genuinely necessary, verify the result (`grep -c` the marker) before acting on it.

Ruff config also corrected: notebook directories now exempt `F821`/`E402` (Databricks
injects `spark`, `dbutils`, `display` at runtime), while `src/fleetguard/` stays strict
because it is plain importable Python.

## Phase 5 — Lakebase

### I-037 — No CREATE privilege on the Postgres schema — RESOLVED
*Date:* 2026-08-31 · *Status:* resolved

`06_create_depot_and_verify` aborted at pre-flight with *no CREATE privilege on
bootcamp_students*. The guard fired before any DDL, so nothing was written.

**Resolved by a direct grant to the user.** Confirmed after the fact: `can_create = true`
while `role memberships` is **still empty** — so `CREATE` was granted straight to
`abhisek.bastia17@gmail.com`, not inherited through a role.

**Correction to advice given during triage.** The first recommendation was
`GRANT "users" TO "abhisek.bastia17@gmail.com"`, on the assumption that `users` was a group
role because it owned 85 tables in the schema. That assumption was never verified and was
**wrong**: `users` and `student` both have `rolcanlogin = true` — they are login users, not
group roles. The actual group roles are `databricks_all_writer_perms`,
`databricks_superuser` (the only one conferring CREATE) and
`databricks_synced_table_helper`. In Postgres users and roles are the same object, so the
distinction is `rolcanlogin`, and it should have been checked before recommending a grant.

### I-038 — Lakebase CDF round-trip VERIFIED end to end
*Date:* 2026-08-31 · *Status:* resolved — **Phase 5 done-when met**

The project's single riskiest unknown works. `fleetguard_depot` created in
`databricks_postgres.bootcamp_students` with `REPLICA IDENTITY FULL` (`relreplident = 'f'`),
60 rows inserted, 1 updated, 1 deleted. The destination appeared as
**`bootcamp_students.bootcamp_cdc.lb_fleetguard_depot_history`** — exact name, **no `_1`
suffix**, so no silent collision.

| `_pg_change_type` | rows |
|---|---:|
| `insert` | 60 |
| `update_preimage` | 1 |
| `update_postimage` | 1 |
| `delete` | 1 |

All five metadata columns present as documented: `_pg_change_type`, `_pg_lsn`, `_pg_xid`,
`_timestamp`, `_sort_by`. `REPLICA IDENTITY FULL` is doing its job — `update_preimage`
carries the full prior row rather than just the key.

**Still to measure:** end-to-end latency. All `_timestamp` values land in the same second,
so the capture side is fast, but a properly timed write is needed before §8.3's ~15s figure
can be called measured rather than documented. That is a Phase 11 task.

**Naming validated.** `fleetguard_<entity>` (I-036) survives the round-trip intact, so the
remaining ten tables can be created with confidence.

## Phase 3 — chunking + AI Search

### I-039 — Retrieval returns text-identical siblings; dedupe key is `odi_number`
*Date:* 2026-08-31 · *Status:* open — Phase 7 search tool must handle it

An ANN query for *"car suddenly sped up on its own"* returned what looked like the same
Nissan narrative three times. It is **not** the same complaint: `complaint_id` is distinct
on all 10 top results. It is the `ODINO` structure from I-023 — one complaint filed against
several components becomes several `CMPLID` rows carrying **identical narrative text**, so
each embeds to a near-identical vector.

Keeping those rows is still correct (deduping on `ODINO` would have discarded 27.9% of the
corpus), but retrieval has to compensate. **`search_similar_complaints()` must dedupe by
`odi_number`, not `complaint_id`** — `complaint_id` looks unique and will not collapse them.

### I-040 — Phase 3 done-when MET on a partially-synced index
*Date:* 2026-08-31 · *Status:* resolved

Phase 3 required *"a hybrid query returns component-code exact matches AND semantically
related narratives in the same result set."* Verified at ~42% sync — behavioural retrieval
quality is per-query, so it does not need the full corpus.

- **`columns_to_sync` works.** It did not appear in the returned index spec, which was an
  open worry; `make`, `model`, `component`, `any_harm` all come back. Harm-filtered
  retrieval was therefore possible.
- **Hybrid genuinely differs from ANN.** On *"SERVICE BRAKES, HYDRAULIC pedal went to
  floor"*, HYBRID surfaced narratives containing the literal token `HYDRAULIC BRAKES`
  that ANN ranked lower — BM25 doing the job §4.3 says it exists for.
- **Semantic half works on pure paraphrase.** *"car suddenly sped up on its own"* uses none
  of the corpus vocabulary (no "unintended acceleration", no `VEHICLE SPEED CONTROL`) and
  retrieved exactly those complaints.
- **Harm filter PASSES.** `filters_json={"any_harm": true}` returned 10/10 harm-bearing
  results — §4.3's *"restrict a semantic search to complaints that involved a fire or an
  injury"* is real, not aspirational.

**Tooling note:** the CLI cannot read this endpoint. `databricks vector-search-indexes
query-index` receives **HTTP 200** and then fails with `invalid character 'r' after
top-level value` — a Go SDK unmarshalling bug, not an index fault. Use the Python SDK.

### I-041 — Index sync is ~5x slower than estimated
*Date:* 2026-08-31 · *Status:* watch

Measured 4,336 rows/min on a STANDARD endpoint, so 1,746,601 chunks take **~6.7 hours**,
not the 30–90 minutes estimated when the index was created. Cost impact is negligible
(~$1.88 of endpoint time) but the schedule impact is real: an index rebuild is most of a
working day. **Do not plan a re-index inside the demo window.** If the index has to be
rebuilt with different columns or a different source, start it the night before.

### I-034 — Chunk-count formula used the stride, not the window — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** The first `silver_complaint_chunk` build produced **1.0269** chunks per
complaint — 59,485 complaints split — against a predicted 1.0006 (~1,390 splits).
`MIN(LENGTH(chunk_text))` was **1**.

**Root cause.** Chunk count was computed as `ceil(len / stride)` with stride 1792, rather
than accounting for the 2048-character window. A 1,793-character narrative therefore
produced a second chunk starting at offset 1792 — containing a *single character*.

**Why it mattered.** It ran clean and produced a plausible table. The cost is real:
~58,000 spurious chunks that would each have been embedded and stored as a vector, and
one-character entries polluting retrieval results.

**Resolution.** `chunks = max(1, ceil((len - window) / stride) + 1)`. Rebuilt: **1.0006**
chunks per complaint, 2,780 split chunks — exactly the 1,390 long narratives × 2 measured
independently in I-026. Also added a `LENGTH(TRIM(narrative)) >= 20` floor, which drops
14,383 narratives too short to carry retrievable signal but long enough to bill for.

### I-035 — Index scope: under 2M vectors, subset size is cost-free
*Date:* 2026-08-31 · *Status:* resolved (decision recorded)

A standard AI Search unit holds 2M vectors at $0.28/hour, so **every scope below 2M costs
the same $6.72/day** — a 57k-row toy subset saves nothing over a 1.7M-row one. Measured
options:

| scope | chunks | units | $/day |
|---|---:|---:|---:|
| fleet make/model + 2018 | 56,941 | 1 | 6.72 |
| fleet make/model | 115,499 | 1 | 6.72 |
| any_harm only | 212,207 | 1 | 6.72 |
| received 2020+ | 601,422 | 1 | 6.72 |
| **post-2010 investigation series** | **1,746,601** | **1** | **6.72** |
| all chunks | 2,196,091 | **2** | 13.44 |

Chosen: the post-2010 investigation series. It is exactly the population the Phase 9
backtest evaluates, covers 80% of the corpus, and stays under the threshold — the full
corpus would double the cost for coverage the backtest does not use. Source table
`silver_complaint_chunk_indexed`.

---

## Recalls API integration

### I-032 — "New campaign" alerts were false positives from model-string mismatch — **SILENT**
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** The first `gold_recall_alert` build reported **9 campaigns the flat file did
not have**, covering 7,584 vehicles. A compelling demo result.

**Root cause.** All 9 were already in `silver_recall`, some with 30+ rows. The anti-join
matched on `campaign_number` **plus** make/model/model_year, and the model strings differ
between API and flat file (API `F-250` vs flat file `F-250 SD`) — the I-030 mismatch again.
Every campaign therefore looked novel.

**Why it was dangerous.** It fails in the flattering direction and would have been *shown to
judges*: "nine new campaigns the daily file hasn't caught yet," all of them already known.

**Resolution.** `NHTSACampaignNumber` is a globally unique NHTSA identifier, so novelty is
determined by campaign number **alone**. Alerts dropped 9 → 0, which is the correct answer:
all 653 campaigns returned by the API are present in the flat file. Mechanism verified by
negative control — holding 3 campaigns out of the flat file makes exactly 3 alerts fire.

### I-031 — Recalls API rejects vPIC model names with a misleading 400
*Date:* 2026-08-31 · *Status:* resolved

**Symptom.** 65 of 163 fleet combos (40%) returned HTTP 400 on the first sweep.

**Root cause.** Two compounding problems.
1. The API returns **HTTP 400 with a body reading `"Results returned successfully"`** when
   it does not recognise a make/model/year combination. Status and body disagree, so a
   client that trusts either one alone draws the wrong conclusion. Confirmed *not*
   throttling: `ford/f-150/2020` returns 200 repeatedly while `CHEVROLET/SILVERADO/2019`
   reliably 400s.
2. The rejected combos used **vPIC's model vocabulary**, which the recalls API does not
   share. Measured: `SILVERADO` → 400 but `SILVERADO 1500` → 200 (10 campaigns);
   `F-250` → 400 but `F-250 SD` → 200; `SIERRA` → 400 but `SIERRA 1500` → 200.

**Resolution.** Poll using `gold_fleet_exposure.recall_model` — the NHTSA-vocabulary name
already proven to join against the flat file — instead of the vPIC name. Success rate went
**60% → 100% (200/200)**, campaign rows 1,017 → 2,117, distinct campaigns 449 → 653.

**Note this is the same root cause a third time** (I-030 exposure matching, I-032 false
alerts, I-031 API rejection). vPIC and NHTSA recall data do not share a model vocabulary,
and every component that joins them has to bridge it explicitly.

### I-033 — "60-second polling" is not achievable as specified
*Date:* 2026-08-31 · *Status:* resolved (claim corrected)

`recallsByVehicle` requires make **and** model **and** modelYear; omitting any returns
`Count: 0` with a success message rather than an error. There is no "recent recalls" call,
so polling means sweeping the fleet's combos.

Measured: **200 combos, 100 seconds, at a polite 2 req/s.** §4.1's literal "every 60
seconds" would mean ~288k requests/day against a public API that §3 explicitly commits to
not using in bulk. §8.3's "~85 seconds worst case" followed from that and was equally
unfounded. Corrected to a measured sweep-plus-interval figure.

---

## Phase 2 — fleet registry

### I-030 — Model-string variance makes exact recall matching insufficient — **measured**
*Date:* 2026-08-31 · *Status:* resolved (design validated)

§4.3 asserts Model B exists to score "manufacturer and model-string variants". That is now
measured rather than assumed, and the effect is larger than expected.

Only **91 of 163** fleet make/model/year combinations match a recall exactly. NHTSA's
dominant spelling frequently differs from vPIC's: the fleet holds `F-250`, while the recall
corpus carries `F-250 SD` (612 rows) against only 17 rows of plain `F-250` — plus
`REDUNDANT F-250` and `REDUNDANT  F-250` (double space).

**Concrete consequence:** all **2,116** F-250s in the roster match across **22 campaigns**
purely as `MODEL_VARIANT`. Exact matching returns **zero** of them. Across the whole fleet,
`MODEL_VARIANT` rows (725,356) outnumber `EXACT` rows (263,686) nearly 3:1.

`gold_fleet_exposure` therefore records `match_basis` per row: `EXACT` needs no model,
`MODEL_VARIANT` is the residual tier Model B scores in Phase 4. The deterministic guarantee
in §7 applies to the `EXACT` tier only, which is the honest framing.

### I-029 — Complaint-frequency weighting produced a delivery fleet with no vans
*Date:* 2026-08-31 · *Status:* resolved

First roster build sampled VIN prefixes by global complaint frequency and produced 20,000
vehicles that were **100% pickups and SUVs** — no Transit, no Sprinter, no ProMaster, and
no Class 8 at all, despite §3 claiming light-through-Class-8 scope for a last-mile delivery
and utility fleet. Pickups dominate complaint volume and crowded everything else out.

Fixed by stratifying candidate selection per segment (VAN / PICKUP / HEAVY) with separate
quotas, then sampling to a target mix of 40/45/15. Segment is assigned from **vPIC's
`BodyClass` and `GVWR`**, not the complaint's make string — necessary because `VOLVO`
covers both Class 8 tractors and passenger cars. Roster now spans Class 1D through Class 8
across 47 models.

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
