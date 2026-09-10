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

### I-099 — the pre-demo data refresh does not refresh, and two jobs on `main` had never run — **SILENT**
*Date:* 2026-09-11 · *Status:* 🟡 partially resolved — two bugs fixed, the refresh gap is **open**

**Found by** rehearsing B3 (the pre-demo data refresh) two weeks early instead of the night
before. Three separate failures, none of which any test could have caught.

#### (a) The refresh refreshes nothing — **OPEN**

Ran the documented chain: ingest → bronze/silver. The ingest genuinely worked —
`FLAT_CMPL.txt` re-downloaded at 1.6 GB with today's mtime, so NHTSA had changed upstream and
`If-Modified-Since` correctly returned 200 rather than 304. The pipeline then ran and reported
`COMPLETED` on **every** flow.

**Zero rows changed.** All seven tables identical to the baseline, and
`MAX(_ingested_at)` on `bronze_complaints` still reads **2026-08-31** with
`COUNT(DISTINCT _source_file) = 1`.

**Cause.** Bronze reads `FROM STREAM read_files(...)` — Auto Loader — which tracks processed
files by *path* in its checkpoint. `01_download_flat_files.py` writes each source to a fixed
path (`cmpl/FLAT_CMPL.txt`) and overwrites in place. A modified file at a known path is never
reprocessed. Success is reported because nothing failed; there was simply nothing Auto Loader
considered new.

**Why the obvious fixes are wrong here.** These flat files are **full snapshots, not deltas**.
`cloudFiles.allowOverwrites` or versioned filenames would re-ingest all 2.24M rows *on top of*
the existing ones, and **silver does not dedupe** — `silver_complaint` documents "one row per
CMPLID" as a property of the source, with no `QUALIFY`/`ROW_NUMBER` enforcing it. Both would
double the corpus rather than refresh it.

**The architecturally correct fix is a pipeline full refresh**, which truncates and reloads
from the current files. **It is not safe to run casually:** a full refresh also rebuilds
`silver_complaint_chunk` (1.7M chunks), which feeds the AI Search index — and an index rebuild
is **~7 h** (`ARCHITECTURE.md` §9.2: never inside a demo window). That decision is deliberately
left to a human with a calendar. **Recorded, not fixed.**

**What this means for B3 as written:** the runbook's chain cannot deliver new data, and would
have reported success while doing so on demo eve. The rest of B3 — rebuilding the signals from
existing silver — works and was completed (below).

#### (b) `ALTER TABLE … ADD COLUMNS IF NOT EXISTS` is a parse error in Spark SQL — **FIXED**

`10_emerging_signals.py` failed with
`[PARSE_SYNTAX_ERROR] Syntax error at or near 'EXISTS'`. Measured live on a scratch table:
`ADD COLUMNS IF NOT EXISTS (b STRING)` **fails**, `ADD COLUMN IF NOT EXISTS b STRING`
**fails**, plain `ADD COLUMNS (c STRING)` **works**. There is no `IF NOT EXISTS` on
`ADD COLUMN(S)` in Spark SQL.

It is a **Postgres idiom carried into Spark SQL** — Postgres does support it, and
`src/lakebase/14_load_signals.py` uses `ADD COLUMN IF NOT EXISTS match_basis TEXT` perfectly
correctly. Same project, two dialects, one habit. A repo-wide scan found this was the only
occurrence in Spark SQL; all 17 others are Postgres and legal.

**The important part is why it survived.** The line was on `main` and had **never executed** —
the job ran the hand-synced workspace copy, which predated it. Repointing the job at repo
source (I-096) is what finally ran it. **The stale copy was not merely out of date; it was
masking a build failure.** Fixed with a Python guard reading the table schema, which is
idempotent in a way the SQL cannot be.

#### (c) The signals loader asserts against a table it does not own — **FIXED**

`14_load_signals.py` aborted with `expected 48 in Postgres, found 50` — **after committing a
correct load**. A green upsert reported as a red job.

`fleetguard_defect_signal` is **co-owned by design**: the batch loader writes
`source = 'DETECTOR'` and the agent write path writes `source = 'AGENT'` with `opened_by`
naming the authorising human. That co-ownership is the documented reason §8.3's trigger uses
`ANY_UPDATED`. Measured: **48 DETECTOR + 2 AGENT = 50**, both AGENT rows opened by the owner
through the real write path.

So `assert total == len(pdf)` means *"nobody has ever used the agent's write path"* — the most
demo-relevant capability in the project permanently breaks the batch loader. Now reconciles on
the `DETECTOR` slice, which still catches the failure the original was reaching for (a signal
that dropped out of gold and lingers, since the write is `ON CONFLICT DO UPDATE` and never
deletes).

#### What the rehearsal did deliver

B1/I-079's tiered fleet match reached `main` on 2026-09-09 but the **stored table still held
the old exact-only zeros**. Rebuilt and loaded, reproducing B1's predicted values exactly:

| | before | after | basis |
|---|---:|---:|---|
| RAM PROMASTER | **0** | **2,418** | MODEL_VARIANT |
| CHEVROLET SILVERADO 1500 | **0** | **766** | MODEL_VARIANT |
| RAM 2500 | 1,256 | 1,256 | EXACT |
| TOYOTA TUNDRA | 232 | 232 | EXACT |

Console-facing: **6 fleet-relevant of 50**, matching STATUS's B3 prediction of "6 of 50, not 4"
exactly. `gold_emerging_signal` stays at 48 detected — no new data landed, per (a).

The agent smoke test's pins moved and **that is the mechanism working**: they are what caught
the change. Repinned `affecting_this_fleet` 2 → 4 and `signals[0].fleet_vehicles` 1,256 → 2,418
(the leader is now a MODEL_VARIANT match). `detected_total` stays 48.

`evidence.json` was re-exported and **deliberately reverted**: every backtest figure was
byte-identical and only `generated_at` moved. Bumping a published freshness stamp when the
inputs and outputs are unchanged would overstate it on the one page that exists to be trusted.

---

### I-098 — the bundle carried two model-version pins into config, re-creating I-094 one layer up — **SILENT**
*Date:* 2026-09-11 · *Status:* ✅ resolved

**Found by** a code review of the I-096 migration — the first run of the project's own
`code-security-reviewer` subagent. Not by a test, and not by the person who wrote the change,
who had spot-checked 2 of 16 generated job files and assumed the rest were the same shape.

`databricks bundle generate job` copies a job's `base_parameters` verbatim out of the live
job. Two of the sixteen had one:

| job | pinned | live reality |
|---|---|---|
| `fleetguard-evaluate-agent` | `model_version: "3"` | endpoint serves **v6** |
| `fleetguard-deploy-agent` | `model_version: "1"` | endpoint serves **v6** |

**Why the evaluation pin is worse than the bug it re-created.** I-094's fix works by treating
a blank widget as "latest". A job `base_parameters` value *sets* that widget — so
`_requested = "3"`, and the notebook prints `evaluating models:/…/fleetguard_agent/3 (pinned
via widget)`. The stale-model bug, wearing the fix's own clothing, and now reading as a
deliberate choice rather than a stale default. Worse still, `docs/ISSUES.md` I-096,
`docs/ARCHITECTURE.md` §9.1 and `docs/STATUS.md` all *claimed the migration had fixed it*, and
the job is now `deployment.kind: BUNDLE` / `edit_mode: UI_LOCKED`, so a UI fix is blocked and a
hand fix is re-asserted away on the next deploy.

**Why the deploy pin is worse again.** `agents.deploy(model_name, model_version="1")` replaces
the live endpoint. The obvious response to a stopped agent endpoint — which STATUS records
happening three times — would have rolled the agent back five versions: no declared table
resource (I-050), no match tiers (I-075), no fleet-vocabulary tool (I-076), no prompt split
(I-077). It comes up green and answers plausibly with wrong numbers.

**Fix.** Both `base_parameters` blocks removed. `15_deploy_agent.py` got I-094's treatment —
blank widget resolves the latest registered version, and it prints which version it shipped
(its own default was also `"1"`, so removing the parameter alone would not have been enough).
`tests/test_bundle_resources.py::test_no_job_pins_a_model_version_in_base_parameters` fails on
any future pin.

**Three further findings from the same review, all fixed here:**

1. **The test suite did not guard the invariant it advertised.** A mutation adding an
   app-owned Lakebase `resources:` block (`CAN_CONNECT_AND_CREATE`), deleting the `sql` and
   `model-serving` scopes, and removing `prevent_destroy` left **12/12 passing**. That is
   precisely the change that converts per-user OBO into a service-account model and falsifies
   ARCHITECTURE §5.1. Now covered by `test_app_holds_no_privileges_of_its_own` and an exact
   scope-set assertion; re-run after the fix, 4 mutations → 4 failures.
2. **`create-remaining-tables` was misclassified and excluded.** It creates the other ten
   Lakebase tables — the rebuild path, not "a patch for create-all-objects". The exclusion
   left it the one job still running a hand-synced copy: **16 lines behind `main`**, missing
   `os.environ.setdefault("PSYCOPG_IMPL", "python")`, whose own comment says a rebuild would
   break silently (I-045). Now bundled and bound. The bundle count is 17, not 16.
3. **Only the App had `prevent_destroy`.** The pipeline and dashboard did not — and deleting a
   UC pipeline drops the streaming tables it owns (2.24M complaints, 5.8M TSBs). Both now
   guarded.

**One claim retired rather than deleted:** `databricks.yml` said pinning `run_as` and avoiding
`lookup:` variables would let `bundle validate` run credential-free. It does not — the comment
now records the measurement instead of the hope.

**Still open (recorded, not fixed):** the deployed `state/metadata.json` names commit
`f2c2c43`, which is `main`'s tip and contains no bundle at all — the deploy ran from an
uncommitted tree.

> **RECURRED THE SAME DAY, which settles what kind of fix this needs.** While shipping I-099,
> `bundle deploy` was run *before* `git commit` in the same command — reproducing this exactly:
> deployed state said `bd8bd24f9` while the deployed files were `4c49d0853`. Caught by checking
> rather than by anything in the system, and corrected by redeploying from the committed tree.
> Documenting the hazard did not prevent it **hours later, by the person who documented it**.
> This wants a pre-deploy guard — refuse to deploy from a dirty tree, or record the tree hash
> rather than `HEAD` — not another paragraph telling someone to be careful. Drift has moved from "workspace vs repo" to "last deploy vs HEAD", with the
same absence of any check. And `docs/STATUS.md`'s documented emergency pause for
`fleetguard-cdf-to-gold` is now silently reverted by the next `bundle deploy`.

---

### I-097 — `bundle deploy` uploads the App's source and deploys nothing — **SILENT**
*Date:* 2026-09-10 · *Status:* ✅ resolved

**Found by** checking, rather than assuming, which command actually ships the App after the
migration to a Declarative Automation Bundle (I-096).

`databricks bundle deploy` reports `Deployment complete!` and, for a Databricks App, that
sentence is not about the app. It uploads `app/backend/` into the bundle's `root_path` and
updates the *app resource* (scopes, permissions, description) — but it creates **no app
deployment**. The running app keeps serving whatever it last deployed, from wherever it last
deployed it. Nothing in the output says so.

Measured end to end:

| step | `active_deployment.source_code_path` afterwards |
|---|---|
| `bundle deploy` (app STOPPED) | unchanged — no deployment created |
| `apps start` | `/Workspace/Users/…/apps/fleetguard-console` ← **the old hand-synced path** |
| `bundle deploy` again (app ACTIVE) | still the old path — no deployment created |
| `bundle run fleetguard_console` | `…/.bundle/fleetguard/prod/files/app/backend` ✅ |

The trap is the second row. `apps start` auto-deploys from the app's
`default_source_code_path`, which `bundle deploy` does **not** update — so after binding the
app to the bundle, a plain start silently resurrected the pre-bundle source. Anyone who
deployed and started would have concluded the bundle worked while running code from the
directory the bundle exists to replace.

`bundle run <app_key>` is the step that both creates the deployment *and* rewrites
`default_source_code_path` to the bundle path — after which `apps start` is safe again.

**Fix / rule.** The App release flow is four commands, and the last one is not optional:

```bash
./scripts/build_console.sh                                       # only if app/frontend/ changed
databricks bundle deploy -t prod --profile abhi
databricks apps start fleetguard-console --profile abhi          # if STOPPED, ~2 min
databricks bundle run fleetguard_console -t prod --profile abhi  # <- ships the code
```

**Verified live 2026-09-10, and the client is named deliberately** (see I-086 — "verified
live" without naming the client turned an untested browser path into a documented pass): a
**programmatic CLI bearer token**, not a browser session. `/api/me` returned
`token_source: databricks-apps`, seven routes green (queue 50 · signals live · service
campaigns 3 · depot risk 60 · work orders 100 · evidence), and the served console bundle
hashes matched the local build exactly (`index-BOPm-YCG.js` / `index-B69MGBo6.css`). The
browser path was not re-tested in this session and its per-user consent grant is unchanged.

---

### I-096 — the jobs ran workspace notebooks that had silently fallen behind `main` — **SILENT**
*Date:* 2026-09-10 · *Status:* ✅ resolved

**Found by** exporting every job's notebook from the workspace and diffing it against the repo
while migrating deployment onto a Declarative Automation Bundle. Nothing had ever checked this.

Jobs pointed at `/Workspace/Users/abhisek.bastia17@gmail.com/fleetguard/…`, a tree kept in sync
by hand with `databricks workspace import --overwrite`. **8 of the 16 bundled jobs were running
stale code.** Every drift was repo-ahead — no work existed only in the workspace — so the
workspace was simply missing fixes that had reached `main`:

| notebook | lines behind | what the workspace copy was still running |
|---|---|---|
| `src/backtest/10_emerging_signals.py` | 151 | the **pre-I-079 exact-only fleet match** — the bug where two real signals reported 0 exposed vehicles against 2,418 and 766, sorting a live defect to the bottom of the Emerging tab |
| `src/agent/16_evaluate_agent.py` | 24 | `model_version` pinned to the literal `"3"` — **I-094**, the evaluation that scores a three-version-stale model and passes |
| `src/lakebase/14_load_signals.py` | 7 | no `ADD COLUMN IF NOT EXISTS match_basis` and no `match_basis` in the insert — the migration half of **I-091** |
| `src/fleet/06_train_model_b.py` | 9 | pre-refactor fuzzy-match feature block |
| `src/agent/14_fleetguard_agent.py` | 5 | an older **agent system prompt** — the deployed agent's own instructions |
| `src/ingest/01_download_flat_files.py` | 2 | lint fix only |
| `src/ingest/05_poll_recalls_api.py` | 1 | lint fix only |
| `src/fleet/04_build_fleet_registry.py` | 1 | lint fix only |

The three fully-clean ones are worth naming too: `21_cdf_to_gold_facts`,
`00_create_all_objects`, `09_lead_time_backtest_v3`, plus all **9 pipeline SQL files**, which
were byte-identical. So this was not "everything is stale" — it was arbitrary, which is worse,
because there was no rule for guessing which job you could trust.

**Why nothing caught it.** The unit suite tests `app/backend/`, not `src/`. CI has no
credentials and cannot see the workspace. The `src/` notebooks are lint-exempt by design
(`# MAGIC` cells, injected `spark`). And a job that runs green from stale source looks
identical to a job that runs green — I-091 is the same failure seen from the other end: a read
shipped ahead of a migration that lived in a notebook nobody had run.

**Fix.** The 17 jobs now run bundle-uploaded source
(`…/.bundle/fleetguard/prod/files/src/…`), so `bundle deploy` and `git push` carry the same
bytes and the hand-sync step is gone. `tests/test_bundle_resources.py` covers the failure this
creates in exchange — a `notebook_path` pointing at a file that no longer exists — offline, in
the existing suite.

> **CORRECTED 2026-09-11 (I-098) — two claims above were wrong when written.**
> **(a) `16_evaluate_agent.py` was only half fixed by this migration.** Repointing removed the
> stale notebook, but the job's own `base_parameters` re-pinned `model_version: "3"`, which
> defeats I-094's blank-means-latest fix from one layer up. Listing it here as fixed was
> wrong. **(b) It was 16 jobs, not 17, and the shortfall was `create-remaining-tables`** —
> excluded as "a patch", actually the creator of the other ten Lakebase tables and squarely on
> the rebuild path. It was left as the one job still on a hand-synced copy, 16 lines behind
> `main`. Both are now fixed; the counts above are updated.

**Not yet done, and deliberately out of scope for the migration:** the two jobs whose stale
code was a real bug (`fleetguard-emerging-signals`, `fleetguard-load-signals`) have **not been
re-run**. They now point at the fixed source, but re-running them rewrites gold and Lakebase
rows, which is a data decision rather than a deployment one. Live Lakebase is currently
consistent — all 24 integration tests pass, `match_basis` exists — so nothing is broken today.

---

### I-095 — a "privilege wall" that was a wrong query, and an enhancement whose method does not exist
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Two errors, stacked**, found by re-testing E-01 rather than trusting its recorded status.

**Error 1 — the block was a misdiagnosis.** E-01 had been marked blocked since 2026-09-05 on the
grounds that the `system.ai` model version could not be discovered: *"`model-versions list`
against the `system` catalog returns empty, most likely a privilege gap (`EXECUTE` on the model /
`USE_CATALOG` on `system`)."* Re-checked with the right commands: the `system` catalog is visible,
`system.ai` is visible, `registered-models list` returns **93 models**, and
`model-versions list system.ai.databricks-claude-opus-4-8` returns **version 1**. No privilege gap
exists. This is the third instance today of a wrong query being read as an access wall (I-088,
I-092), and the most expensive — it parked a Tier 0 item for four days.

**Error 2 — the enhancement's method is not a thing that exists.** E-01 proposed "create our own
**pay-per-token** endpoint wrapping `system.ai.<model>`". You cannot. Measured across three create
attempts: `foundation_model` is **read-only on write** (`unknown field`), and an `entity_name` of
`system.ai.*` is treated as a custom model and demands `workloadSizeId` — **provisioned
throughput**, dedicated GPU capacity. Pay-per-token endpoints are the pre-provisioned
`databricks-*` ones. Nothing was left provisioned; `serving-endpoints list` confirms only the
agent endpoint remains.

**Why the original error message misled.** The 2026-09-05 attempt got `Model version '1' does not
exist`, which reads as "the version is missing" and sent the investigation toward discovery and
permissions. The version exists; the request was the wrong *shape* — a custom-model request for a
foundation model. An error naming the thing you supplied is not necessarily an error about that
thing.

**Resolution.** E-01's *purpose* — stop claiming a PII guardrail the project does not have — is
achieved by correcting `ARCHITECTURE.md` §8, which costs nothing. The endpoint was only the
proposed means.

**Lesson.** A recorded blocker ages badly in two directions at once: the reason can be wrong, and
the plan it was blocking can be wrong too. "Blocked" is a claim with a date on it, and re-testing
one cost twenty minutes against four days of it sitting as the top open item.

### I-094 — the evaluation notebook scored a three-version-stale model by default — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Found by** auditing `docs/ENHANCEMENTS.md` for unbuilt items, not by anything failing. E-06
lists *"latest-version resolution instead of a hardcoded version, so evaluation never scores a
stale model"* as cheap hygiene; two of its three items were done and this one was not.

**The bug.** `src/agent/16_evaluate_agent.py` declared
`dbutils.widgets.text("model_version", "3", ...)`. **v6 is what serves** — versions 1–6 are
registered (confirmed via `model-versions list`). So anyone re-running evaluation with defaults
scored **v3**, three versions behind, and got a clean green result describing an artefact nobody
deploys. Every fix since v3 — I-050's declared table resource, I-075's match tiers, I-076's
fleet-vocabulary tool, I-077's prompt split — is invisible to that run.

**Why it is the worst shape of wrong.** An evaluation that *fails* against the wrong model gets
investigated. One that *passes* gets quoted. And the run printed only
`evaluating models:/...fleetguard_agent/3` — technically honest, easy to read past, and the
number is small enough to look like a default rather than a decision.

**Fix.** The widget now defaults to blank meaning *latest*, resolved via
`max(int(v.version) for v in search_model_versions(...))`, with an explicit widget value still
honoured for deliberately scoring an older version. The resolved version and *why* it was chosen
(`latest of 6 registered` vs `pinned via widget`) are printed unconditionally, because the
underlying failure was a run that did not say what it had scored.

**Verified** to the limit possible off-platform: `mlflow` is a notebook-only dependency and is not
installed locally, so the `MlflowClient` call itself could not be executed here. The *fact* it
depends on was confirmed via the CLI — `model-versions list` returns 1–6, max 6, matching the
serving version. The call runs on Databricks where mlflow is present.

**Lesson.** A hardcoded default is a decision that stops being re-examined the moment it is
written. This one was correct on the day it was typed and silently wrong three deploys later,
with nothing in between to notice — the same shape as I-051's spec drift, in a widget.

### I-093 — the "assistant is offline" state was unreachable for the case it was written for — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Found by** auditing what a judge hits when the agent endpoint is asleep, after I-092 showed it
stops on its own.

**The bug, in two halves written to different assumptions.** `Assistant.tsx` renders a friendly
*"The assistant is offline. The queue and approval path are unaffected."* only on **503**, and its
own comment states *"503 means the serving endpoint is stopped"*. `chat.py` maps **404 → 503** —
but a stopped Databricks serving endpoint answers **400**, not 404, so the request fell through to
the generic `>= 400` branch and surfaced as a raw **502**. The friendly state was therefore
unreachable for precisely the situation it exists to describe, and the most likely real-world
cause produced the ugliest possible output.

**Neither half was wrong on its own,** which is why it survived review: the frontend correctly
handles 503, the backend correctly surfaces the endpoint's message (the 2026-09-04 fix), and the
existing test `test_stopped_endpoint_error_is_surfaced_not_swallowed` **asserted 502 and passed**
— it encoded the bug as the expectation, pinning the real message body while pinning the wrong
status alongside it.

**Fix.** Map a 400 whose body mentions `stopped` to 503. Matching on the provider's message text
is unlovely and deliberate — a stopped endpoint and a malformed request are both bare 400s, so the
body is the only signal there is; if the wording changes this degrades to the old 502, which is
worse rather than broken. The test was rewritten to assert 503, and a second test added so a
genuine 400 still yields 502 with its reason, since the offline branch must not swallow real
request errors.

**Lesson.** A status-code contract spanning two files is a contract nobody type-checks. Both sides
looked correct in isolation and the test agreed with the broken half — so the only thing that
could have caught this was asking *what does the user actually see when the thing fails*, which is
a question about behaviour, not about code.

### I-092 — the agent serving endpoint was STOPPED, and a request does not wake it — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Found by** the demo dry-run's first agent question, which returned
`502 → Agent endpoint returned 400: The given endpoint is stopped, please retry after starting
the endpoint.` Retried: same. The request does **not** wake it.

**What was believed.** `STATUS` recorded `scale_to_zero_enabled: True` and said "the first demo
question then pays a cold start" — i.e. slow, but working. Reality: the served entity was
`DEPLOYMENT_STOPPED` / `deployment_state_message: 'Stopped'`, `ready: NOT_READY`, while
`scale_to_zero_enabled` still read `True`. **Scale-to-zero and stopped are different states and
the config flag does not distinguish them** — reading the flag alone told us nothing.

**Consequence had it not been caught:** the Assistant panel — the agentic half of the project and
§13's whole requirement — would have failed live, after the console had already rendered fine.
I-050's shape once more: failure arriving *after* visible success.

**Fix.** There is **no `start`/`resume` subcommand** — Model Serving offers only scale-to-zero or
delete — so recovery is `update_config` re-applying the served entity, with the payload built
**from the live entity** (dropping `environment_vars` silently misfiles MLflow tracing) and
`scale_to_zero_enabled` re-asserted, since `agents.deploy()` resets it every time.
**Measured: 184 s to `READY`, then 20 s for the first answer, 13 s warm.** Verified the agent
still answers correctly — 25 vehicles / 22 depots / EXACT with the tier stated.

**AMENDED same day — there are TWO idle states and the original entry conflated them.** Measured
directly:

| `deployment_state_message` | `deployment` | Wakes on request? |
|---|---|---|
| `Scaled to zero` | `DEPLOYMENT_READY` | **Yes — 47 s**, answers correctly |
| `Stopped` | `DEPLOYMENT_STOPPED` | **No** — `400 the given endpoint is stopped` |

`scale_to_zero_enabled` reads `True` in both, so it distinguishes nothing. The endpoint found dead
this morning was `Stopped`; after the restore it scaled down to `Scaled to zero` within hours and
woke normally on the next request. The likely progression is active → scaled to zero → stopped
after longer idle.

**This matters because the first version of the fix was too pessimistic.** `DEMO.md` and the
README were briefly written to say the Assistant "may be offline" as the expected case — which
would have led a reviewer to give up on what is actually a 47-second cold start. Corrected: wait
for the wake, and treat *"offline"* as the rarer hard-stopped state.

**Lesson.** A cost decision recorded as a *config value* ("scale-to-zero is on") is not a
statement about whether the thing currently works — and neither is a single observation of a
broken state. Two states that share a flag and differ in behaviour need both to be measured before
either is documented.

### I-091 — a schema-dependent read shipped ahead of its migration; the Emerging tab 500'd — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**The failure.** `GET /api/signals` → **500**, `psycopg.errors.UndefinedColumn: column
"match_basis" does not exist`. The Emerging tab — the entire proactive half of the demo — was
dead, both locally and on the deployed App.

**Cause: ordering.** I-079's fix (`548cba8`) added `match_basis` to `signals.py`'s SELECT. The
column is created by `src/lakebase/14_load_signals.py`'s ALTER block, which is **B3's job and had
not run**. The read shipped before the migration that creates what it reads.

**Why nothing caught it — three layers, each blind for a different reason:**
- the 348-test unit suite passes, because the router tests use fakes and never issue SQL;
- `tests/test_data_quality.py` queries the **SQL warehouse**, not Lakebase — **no test had ever
  connected to Postgres at all**;
- CI has no credentials by design, so it could not have caught it either.

It was found by curling thirteen routes and noticing that **one** was not 200. Twelve healthy
routes are excellent cover for a thirteenth that is broken.

**Fix.** Ran only the `ADD COLUMN IF NOT EXISTS match_basis TEXT` statement — **not** the loader's
row rebuild, which is B3's and must not run twice. **No redeploy was needed**: the App's code was
already correct, only the column was absent. All rows read NULL, which the model documents as
"not recorded, not no-match", so the tab renders correctly with no tier badge until B3.

**Encoded as a regression** in the new `tests/test_lakebase_schema.py`: the column list is parsed
**out of `signals.py`'s own source** and executed against live Postgres, so it cannot drift from
the code — a hand-copied list would reproduce the same two-places-to-update problem. Proved it can
fail (a check that cannot fail is not a check) by running the identical query against a `pg_temp`
table lacking the column: `UndefinedColumn`, as required, with the live table untouched.

**Lesson.** A migration and the code that depends on it must ship together or be ordered
deliberately; "the loader adds it idempotently" is only true once the loader has run. And a test
suite that mocks its database cannot see schema drift — that needs a layer that actually connects,
which this project did not have until now.

### I-090 — a costed work order that was not completed, created by the seeder's own determinism
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Found by** running `scripts/seed_demo_state.py` a second time to prove it was idempotent —
not by reading it. The first live run left `WO-7b0d5c14a8fc` at `IN_PROGRESS` **with `$75`
logged against it**, which the script's own step-4 comment calls a data error.

**Cause.** One pre-existing row (COMPLETED and costed, from the 2026-09-08 verification) hashed
to `IN_PROGRESS` under `target_status()`, so the status pass moved it *out* of COMPLETED and it
kept its cost. Steps 3 and 4 each behaved correctly in isolation: step 4 only ever logs costs
against COMPLETED rows, and step 3 had no reason to know about costs.

**Why the obvious fix was the wrong one.** Repairing the row by hand looks sufficient and is
not — the hash is deterministic, so **the very next run would have recreated it**, and the
manual repair would have been silently undone. The guard therefore went into step 3 (never move
a costed work order out of COMPLETED) and the invariant is now **asserted** in the reconciliation
block, so it is a check rather than an avoided case.

**Lesson.** Determinism makes a seeder idempotent *and* makes its bugs self-restoring. A
data-repair that is not also a code change is a repair with a timer on it.

### I-089 — `overdue_work_orders` was 0 on all 60 depots because every due date was identical — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**The state.** Every one of the 230 work orders in Lakebase carried `due_date = 2026-09-15`.
Both overdue definitions are correct and agree with each other (`depots.py`: `status NOT IN
('COMPLETED','CANCELLED') AND due_date < CURRENT_DATE`; `dates.ts`'s `isOverdue`, aligned by
I-087) — but with no date in the past, both correctly returned **zero, everywhere**.

**What that hid.** The Depots tab's overdue column, its "overdue only" filter, and the OVERDUE
badge **I-087 had just been fixed to render correctly** all had nothing to display. A feature
fixed one day was still unexercisable the next, and nothing anywhere reported a problem: zero
is a legitimate value, the tests pass, and the column renders.

**Cause.** `due_in_days` is a *per-campaign* field on `ApprovalRequest`, so every work order a
campaign launches shares one due date. With only two campaigns ever launched, the whole table
held two dates, both in the future. Not a bug in the approval path — an artefact of how the
data was created showing up as a dead feature downstream.

**Fix.** B2's seeder staggers due dates over 14 offsets, three of them negative: **44 overdue
work orders across 28 depots**. Backend and frontend were confirmed to agree *before* seeding,
so making the number non-zero could not make the two surfaces contradict each other.

**Lesson.** A feature can be correct, tested, and completely unexercised, and the symptom of
that is a plausible number rather than an error. I-087 fixed the rendering of a state the data
could not produce.

### I-088 — `postgres list-roles` reports "no role" for every identity, including the owner — **SILENT**
*Date:* 2026-09-09 · *Status:* ✅ resolved

**The check.** Establishing whether the three judges hold Lakebase login roles — the question
behind A1 / I-084, which had been sitting as "the project's live risk".

**The trap.** `databricks postgres list-roles projects/<p>/branches/<b>` returns records whose
`name` is an opaque resource path (`.../roles/rol-yve7-agv39fm28y`). The **human identity is in
`status.postgres_role`**. Matching an email against `name` returns **NO ROLE for all 32 roles**
— a complete, confident, uniform false negative.

**Why it nearly landed.** "None of the judges have Lakebase roles" is exactly the alarming
answer the risk section predicted, so it reads as confirmation rather than as a bug. It was
caught only because the same query also said the **owner** had no role — an identity whose
access is demonstrably working, which made the result impossible rather than merely bad.

**The real answer, once matched on the right field:** 32 roles, 29 human, and **all three judges
are among them** — which substantially retires I-084's demo-day risk rather than confirming it.

**Two lessons.** Include a known-good control in any lookup that could silently return nothing —
the owner's row is what falsified this. And a result that agrees with the risk you already
believe in deserves *more* scrutiny than one that contradicts it, not less.

### I-087 — every work order due **today** rendered as OVERDUE, but only for viewers behind UTC — **SILENT**, and invisible from the author's own screen
*Date:* 2026-09-09 · *Status:* ✅ resolved

**Found by** working through the one line in `scratchpad.txt` that was a correctness question
rather than a feature request — "timezone of date columns". The schema turned out to be right
(`TIMESTAMPTZ` throughout, `DATE` for date-only), so the answer was "no problem here" until the
same question was asked one layer up, at the browser.

**The bug.** `WorkOrders.tsx`:

```js
return new Date(w.due_date) < new Date(new Date().toDateString());
```

Two `new Date()` calls, two **different parsing rules**:
- `new Date("2026-09-08")` — ISO date-only — parses as **UTC** midnight.
- `new Date("Tue Sep 08 2026")` — what `toDateString()` produces — parses as **local** midnight.

For a viewer *behind* UTC, local midnight is later in absolute time than UTC midnight of the
same calendar day, so today's own date compares as earlier than "today" and **every open work
order due today rendered as OVERDUE**. Measured:

| timezone | work order due today |
|---|---|
| `Europe/Berlin` (UTC+2 — the author's) | correctly not overdue |
| `America/Los_Angeles` (UTC-7 — **the workspace's own region**) | **flagged OVERDUE** |

**Why it survived.** It is correct east of Greenwich and wrong west of it. Everything the author
sees is right; everything a US-based judge sees is wrong. No error, no warning — the overdue
count on the stat tile and the row badges are simply inflated. The same shape as I-012
(`_rescued_data` reading 0 while 143 rows were mis-parsed): the check that was run was real, it
just was not the check that mattered.

**A second defect it was hiding.** The backend has always defined overdue as
`due_date < CURRENT_DATE` (`depots.py`, plain SQL, correct). So the console and the Depot Risk
tile held **two different definitions of overdue** and could disagree about the same rows. Only
one of them was wrong, but nothing compared them.

**Fix.** `due_date` is a Postgres `DATE` arriving as `YYYY-MM-DD`, so compare the **strings** and
never construct a `Date` at all — ISO date-only sorts lexicographically in date order, which is
both correct and timezone-free, and matches the backend's definition exactly. Extracted to
`lib/dates.ts` with `today` injectable, per the standing rule that logic which has already been
wrong once moves somewhere it can be tested. 9 tests added (`lib/dates.test.ts`).

**The tests were mutation-checked, and the result is the useful part.** Restoring the original
expression fails **2 tests in Europe/Berlin and 3 in America/Los_Angeles** — the extra LA failure
being the timezone-dependent "due today" case. Injecting `today` is what makes the suite fail in
the *author's* timezone, where the bug itself is invisible. A test that only failed in LA would
have reproduced the original mistake in a new place: correct on someone else's machine, useless
on the one where the code is written.

**And TypeScript caught a fresh bug introduced by the fix.** Making `today` an optional second
parameter silently broke `orders.filter(isOverdue)`, because `Array.filter` passes
`(value, index, array)` — the array **index** would have bound to `today`, comparing a date
string against a number for every row after the first. `tsc` rejected it via a `PostToolUse`
hook before it could run. An injectable-parameter design and a point-free `filter` are
individually reasonable and jointly wrong; the call site is now wrapped, with a comment saying
why it is not point-free.

**Checked and deliberately not changed:** `Trends.tsx` also parses a date string, but appends
`T00:00:00Z` explicitly and reads it back with `getUTCFullYear`/`getUTCMonth`/`getUTCDate`
throughout, displaying the raw string rather than a formatted `Date`. It is internally
consistent and correct. Working code adjacent to a bug is not itself a bug.

**Lesson.** A date-only value has no timezone, so any code path that turns one into an instant
has invented information. The two safe options are to compare the strings, or to be explicit
about the zone *and* read it back in the same zone — `Trends.tsx` does the second, this now does
the first. Mixing the two is what fails, and it fails in a direction that depends on where the
reader is sitting, which is the one variable no amount of local testing varies.

---

### I-086 — the App's Lakebase scope was granted on the app and never consented to by the user — a **third** configuration plane nothing we had documented mentioned — **SILENT**
*Date:* 2026-09-08 · *Status:* ✅ **resolved same day** — root-caused, fixed from this account, and verified

**Symptom.** Redeployed the App with tonight's code (`databricks sync` + `databricks apps
deploy`, both reporting success). Every Lakebase-backed route 500s. The underlying error,
straight from Databricks' own API:

```
POST /api/2.0/postgres/credentials
> {"endpoint": "projects/summer-bootcamp-2026-v2/branches/production/endpoints/primary"}
< 403 Forbidden
< Invalid scope, required scopes: postgres
```

Only `/api/evidence` (reads a committed snapshot, touches no Lakebase) and the assistant
panel's static shell (makes no call until a message is sent) loaded. Confirmed live in the
owner's own browser — this is not a curl-without-a-session artifact.

**Three remediation steps tried, in order, none of which fixed it:**
1. `databricks apps stop` / `apps start` (full compute restart) — the documented I-083 fix
   for "granted but not yet live." No change.
2. Full sign-out and sign-in in the browser, on the theory that the session held a token
   minted before the scope was live. No change — identical error on a genuinely fresh token.
3. `databricks apps update --json '{"user_api_scopes":[...]}'` — explicitly re-applying the
   *same* scope list, on the theory that the value can look correct via `apps get` without
   having actually propagated to whatever issues the real token — followed by another full
   stop/start and redeploy. **No change.** The error text even varied once (`unable to parse
   response` vs the plain 403), but the raw logged request/response was identical both times:
   the same `403 Forbidden — Invalid scope, required scopes: postgres` from the same endpoint.

**ROOT CAUSE — a third plane.** OBO scope is not two settings that must agree, it is **three**,
and we had documented only two. All three must contain the scope:

| plane | holds | how to read it | state during the outage |
|---|---|---|---|
| workspace allowlist | which scopes *any* app may request | `workspace-settings-v2 get-public-workspace-setting allowedAppsUserApiScopes` | `["*"]` — fine |
| app resource | which scopes *this* app requests | `apps get` → `user_api_scopes` | `postgres, sql, model-serving` — fine |
| **user consent grant** | which scopes *this user* has agreed to give this app | `GET /api/2.0/oauth-app-integrations/<id>/user-consent/me` | **`offline_access, email, iam.current-user:read, openid, iam.access-control:read, profile`** — no `postgres` |

The consent grant was captured when the user first opened the app, at which point the app
still had **only platform defaults** — because `app.yaml` scopes are silently ignored (I-083)
and the real scopes were applied afterwards. Consent is stored **server-side per (user, app)**
and is **sticky**: it does not widen when the app's scope list widens.

That is precisely why all three remediations failed, and none of them was a bad guess — each
was aimed at a plane that was already correct. Restart reloads app config; sign-out/sign-in
re-uses the stored grant; `apps update` edits the app, not the grant. `apps get` looks
flawless throughout **because it is** — it simply does not show the plane that was wrong.

**Fix, self-service, no admin needed:**
```bash
TOKEN=$(databricks auth token --profile abhi -o json | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  "https://<workspace>/api/2.0/oauth-app-integrations/<oauth2_app_client_id>/user-consent/me"
```
Then reopen the app **in a fresh/incognito browser session** — revoking consent does *not*
invalidate tokens already issued (Databricks' own docs say so; they live up to an hour), so a
warm session can keep failing after a correct fix and imitate the bug. The consent screen then
re-prompts with the full scope list. **Verified after re-consent:** `user_consented_scopes`
contains `postgres`, `sql`, `model-serving`, and every Lakebase route works in the browser.

**Two claims in the first version of this entry were wrong, and both are worth keeping visible.**

1. **"Blocked on account-admin access."** False. The relevant object is not the account-level
   custom app integration (`custom-app-integration get` → `Not Found`, which is what produced
   this conclusion) but the **per-user consent grant**, and `/user-consent/me` is deliberately
   self-service — `me` is the whole point. An access wall on one lookup was generalised into a
   wall on the entire problem, and it closed the investigation one step early.
2. **"A regression from a verified-working state."** Also false, and the more important error.
   The consent record proves **no browser session ever held `postgres`** — consent only
   accumulates, so a browser that had once succeeded would still show it. The morning's
   "all seven routes verified live under real OBO" was therefore done with a **programmatic
   CLI bearer token**, which carries broad scopes and never touches the consent flow. Nothing
   regressed. This evening was **the first genuine browser-OBO test**, and it failed on first
   contact. `STATUS.md` Phase 8 overclaimed this and has been corrected.

**Lesson.** Two of them, and the second is the one that cost the time. *(a)* A permission error
that survives every fix aimed at the config is evidence the config is not the plane that is
wrong — extending I-083's "is the grant loaded" to a third question, "**has the user agreed to
it**". *(b)* **"Verified live" must name the client.** A programmatic token and a browser session
are different auth paths with different scope sets; recording the result without recording which
one produced it turned an untested path into a documented pass, and the gap only surfaced when
the untested path was finally exercised. Compare I-012, where `_rescued_data` read 0 while 143
rows were mis-parsed: in both cases the check that was run was real, and simply not the check
that was claimed.

**Bonus.** Re-consenting exercised the browser OAuth-consent step that I-084 lists as never
tested, retiring one of that issue's three unknowns.

### I-085 — "no password auth on Lakebase" was a fact about specific roles, not the platform — and our own project has it switched on too
*Date:* 2026-09-08 · *Status:* **open — capability confirmed, our privilege to use it is not**

**How this surfaced.** A bootcamp peer (Zach Steele) shared in Slack that his Render app
writes to Lakebase using a plain `postgresql://edgar_app:<password>@ep-lingering-recipe-…/`
connection string — static username/password, no Databricks OAuth involved. Asked to check
his `zdsteele-capstone` project to understand the mechanism, **read-only, account-metadata
level only:** `databricks postgres list-projects/list-branches/list-endpoints/list-databases
--profile abhi`. Deliberately did not run `list-roles`, `generate-database-credential`, or
connect to his database — metadata visible via the shared account's flat listing is one
thing, touching his actual credentials or data is another, and only the former was in scope
without his explicit grant. (`list-roles` was in fact blocked by the harness's own safety
classifier when attempted alongside the others — a reasonable line, independently drawn.)

**What the metadata showed.** `zdsteele-capstone` has `enable_pg_native_login: true` — a
project-level flag that enables real Postgres password roles alongside the OAuth-token-minted
roles Databricks auto-provisions per signed-in identity. That flag is *why* his static
connection string works at all.

**The finding that matters more: our own project has the same flag.** Checked
`summer-bootcamp-2026-v2` (this project's Lakebase project) in the same `list-projects`
output — `enable_pg_native_login: true` there as well. The earlier claim recorded elsewhere
("Lakebase roles are all `LAKEBASE_OAUTH_V1`, no password auth") was true of the *specific
roles* someone inspected at the time, not a project- or platform-wide restriction. Native
password roles were never actually unavailable to us at the platform level.

**Why this isn't simply "go do it."** `list-projects` also revealed `summer-bootcamp-2026-v2`
is **owned by `zach@zachwilson.tech`**, not by this project's team — a fact not previously
written down anywhere in this repo's docs. `CREATE ROLE` privilege typically belongs to the
project owner (or an explicit grant from them); this account's identity has never tested
whether it can create a role on a project it does not own. I-084 already established this
account has no `CREATEROLE` here — this entry explains *why* (ownership, not a platform
ceiling) rather than changing that conclusion. The static-role path a peer used may simply not
be exercisable on this specific project without asking its owner, which is a different
blocker than "the platform doesn't support this."

**Why it's logged as open rather than resolved.** Two separate things were previously
conflated under one "no password auth" claim: (1) whether Lakebase *supports* native login —
now confirmed yes, and (2) whether *this identity* can create a role on *this project* —
still untested. Resolving (2) either way (ask the owner, or try `CREATE ROLE` and observe the
error) is the actual next step, not assumed here.

**Lesson.** A measured fact about the roles that happen to exist is not the same claim as a
platform limitation — the two look identical in a status doc until someone checks a
differently-configured project and the gap shows. Separately: verifying "our project" also
means verifying who owns it, not just its connection details — ownership determines which
privilege questions are even worth asking.

### I-084 — **OPEN RISK:** a judge may sign into the App successfully and have every data route fail, and we cannot pre-provision the fix
*Date:* 2026-09-08 · *Status:* **OPEN — untestable from this account, decide before the demo**

**The question.** The Databricks App was verified end to end on 2026-09-08 — but **only under
one identity, the owner's**. Nothing tested proves it works for a second person. The specific
unknown: `db.py` mints a Lakebase credential from the caller's token and then connects to
Postgres **as that identity**. Does Lakebase **auto-provision a Postgres login role** on first
connect, or must the role already exist?

**Why "the judges are admins" does not answer it.** It answers a different question. Workspace
admin clears the app's ACL (`admins: CAN_MANAGE`) and lets them start the stopped app. Postgres
roles are a **separate** namespace. Measured live:

| identity | role on this metastore | Lakebase login role |
|---|---|---|
| `zach@zachwilson.tech` | owner of catalog `main` | **YES** |
| `eumardassis@gmail.com` | owner of `bootcamp_students` | **NO** |
| `gudetayared@gmail.com` | owner of `tabular` | **NO** |

Two of the three named owners have no role. 27 login roles exist in total, all cohort members
who have *used* Lakebase — which is consistent with **either** answer, so the population is
evidence of nothing on its own.

**The workaround is closed.** We cannot pre-create roles for them: Phase 10 established this
account has **no `CREATEROLE`** on the shared Lakebase instance, correctly restricted on
infrastructure shared with ~296 students. If auto-provisioning does not happen, there is no
Lakebase-side fix available from here.

**Why it is worse than an ordinary unknown.** The failure lands *after* a successful login. An
admin judge authenticates, the shell renders, `/api/me` returns their real identity — and then
every data route 500s. A failure that arrives after visible success reads as a broken project
rather than a missing grant, and it is the single most damaging shape a demo failure can take.
Compare I-050: the same lesson, one layer up.

**How to settle it — one test, and the tester must be chosen deliberately.** A single sign-in
by a real second identity resolves this, the browser OAuth-consent question (scopes were only
ever exercised with a programmatic bearer token) and the ACL question at once.
**`zach@zachwilson.tech` is the wrong tester** — he already has a role, so a pass proves
nothing. Pick someone **without** one.

**Fallback if it fails.** Render, running `app-login` + snapshot, requires no Databricks
identity at all and is immune to this entire class of problem. This is a concrete vindication
of the 2026-09-08 decision to keep the Render integration rather than delete it.

**Separately, and by design:** `FLEETGUARD_APPROVERS` holds the owner's email only, so a judge
can read everything and gets **403 on approve** — the approval gate is application logic, and
admin status does not bypass it. If judges should exercise the approval flow (arguably the
strongest part of the demo), they must be added explicitly.

---

### I-083 — App OBO scopes are ignored in `app.yaml`, and a granted scope looks identical to a missing one until you restart — **SILENT**
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** The App deployed cleanly, `/api/me` correctly returned the caller's identity via
`x-forwarded-access-token`, and **every** Lakebase route returned 500. The log showed
`POST /api/2.0/postgres/credentials → 403 Invalid scope, required scopes: postgres` — against
an `app.yaml` that declared exactly that scope.

**Two independent causes, and the second is the nastier one.**

1. **`user_authorization: scopes:` in `app.yaml` does nothing.** It is not rejected, not
   warned about, not logged — the app simply keeps its defaults. `apps get` showed
   `user_api_scopes: None` while the file plainly listed three. Scopes live on the **app
   resource**: `apps update --json '{"name":..., "user_api_scopes":[...]}'`. The skill
   reference says "add scopes in the UI", which is true but reads as one option among
   several rather than as *app.yaml will not work*.
2. **A granted scope is not a live scope until the app restarts.** After the grant,
   `effective_user_api_scopes` correctly listed `postgres`, and the very same request still
   returned the **identical** 403. There is no distinguishable signal between "never
   granted", "granted but not restarted", and "grant genuinely rejected" — the failure looks
   the same in all three states. Only `stop` + `start` made it work.

**Also learned, correcting our own note.** `iam.access-control:read` and
`iam.current-user:read` **do** exist — they appear in `effective_user_api_scopes` as platform
defaults — but the API **rejects them on write**: *"The specified scope
iam.access-control:read is not a valid scope."* `CLAUDE.md` had recorded that they "don't
exist here" and the skill reference listed them as selectable; both were half right. Assignable
and effective are different sets, and no document we had said so.

**Verified working after the restart**, all against live Lakebase under the caller's token:
`/api/queue` 50, `/api/signals` 50 (9 live, 4 fleet-relevant), `/api/service-campaigns` 1,
`/api/depot-risk` 60, `/api/work-orders` 25, `/api/evidence` 1.44× / z 2.62 — matching the
local surface exactly.

**Lesson.** When a permission error survives the fix, the question is not only "is the grant
right" but "**is the grant loaded**". A config that is correct in the control plane and stale
in the running process produces an error message that accuses the config. Two of today's
issues (this and I-052's served-entities) share that shape: the authoritative-looking read was
of the wrong plane.

---

### I-082 — The SDK refuses to authenticate inside Databricks Apps if you pass a token, and only there
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** Every Lakebase route 500'd on the App with
`ValueError: validate: more than one authorization method configured: oauth and pat`, raised
from `WorkspaceClient(host=host, token=principal.token)` — a line that has worked unchanged
for weeks locally and on Render.

**Cause.** Databricks Apps auto-injects `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`
for the app's **own service principal**. The SDK's `Config` discovers those ambient OAuth
credentials, sees the explicit token as well, and refuses to choose between them. Locally and
on Render those variables do not exist, so the identical call resolves to PAT and succeeds.
**This class of bug cannot be caught before deploying** — the trigger is an environment
variable the platform sets for you.

**Fix.** `WorkspaceClient(host=host, token=principal.token, auth_type="pat")`. Pinning the
strategy also states the intent the docstring already claimed: act as the caller, never as
the app.

**Reproduced locally before trusting it**, by setting fake `DATABRICKS_CLIENT_ID`/`_SECRET`
and constructing a `Config` both ways: without `auth_type` it raises the exact production
error; with `auth_type="pat"` it constructs cleanly. So the fix was verified in both
directions rather than deployed hopefully.

**Why the failure mode matters more than the fix.** The plausible "resolution" — letting the
SDK fall back to the app's service principal — would have been the **worst** outcome
available: every read would silently widen to the app's privileges and every write would land
as the app instead of the human, making `opened_by` a decoration and Postgres RLS
inapplicable. An error here was the correct behaviour; a silent fallback would have quietly
destroyed the property the whole surface exists to demonstrate.

---

### I-081 — The `table_update` trigger config recorded as spec could not be created, and its stated rationale was false
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** `jobs create` rejected the §8.3 trigger config that had been sitting in
`CLAUDE.md` as project spec since before the build:
`Invalid trigger minTimeBetweenTriggersSeconds: it must be greater than 60 seconds`, then the
same for `waitAfterLastChangeSeconds`. The recorded values were `15` and `5`.

**Three things were wrong, and the third matters most.**

1. **Both intervals are below a hard platform floor of 60 s.** Not tight — impossible.
2. **The table paths were wrong on both axes.**
   `bootcamp_students.fleetguard.lb_agent_action_history` has the wrong schema *and* the wrong
   table name; CDF writes `lb_fleetguard_*_history` into `bootcamp_cdc`. STATUS had already
   recorded the correct path separately, so the two documents disagreed and neither knew.
3. **The rationale attached to the values was false, and it defended a claim.** The note read:
   *"Both are tight in this project by design — a longer settle window would push worst-case
   latency past the sub-minute velocity claim."* The platform **forces** a settle window over
   a minute. So the design could never have been what the note said, and §8.3's sub-minute
   Postgres→UC claim **cannot be met through a `table_update` trigger**.

**What survives, stated precisely.** The sub-minute number is real for the leg it was measured
on: Postgres → `bootcamp_cdc` CDF replication, **7.1–15.6 s** (I-046). Two legs, two numbers;
quoting the CDF figure for the whole chain would be the same conflation §3 keeps three
lead-time intervals apart to avoid.

**Then the second leg was measured too, and it corrected this entry's own estimate.** This
issue originally reasoned "about `61 s + job duration` — 2–3 minutes" from the config. Two
live cycles on 2026-09-08 gave **155 s and 269 s** commit-to-fact — the low end below that
estimate, the high end well above it. The variable part is trigger *detection* (102 s and
213 s to run start); the job itself is steady at 54–56 s. Report **≈2.5–4.5 minutes, n=2**, as
a range. Reasoning from a config is not a measurement, even when the config is finally correct
— which is the same mistake as the rationale this issue exists to retract, made one level up.

**Also corrected: `ALL_UPDATED` → `ANY_UPDATED`.** `ALL_UPDATED` fires only once *every* named
table has changed. `fleetguard_defect_signal` is also written by the batch signals loader,
which writes no `fleetguard_agent_action` row — so under `ALL_UPDATED` a batch-only refresh
would wait indefinitely for a write that never comes. Not a syntax error; a deadlock that
would have looked like "the trigger just doesn't fire sometimes".

**Lesson.** A config snippet in a document is **not** a verified fact until something has
accepted it. This one lived in the "do not re-derive" section — the part explicitly reserved
for things checked against a live system — and had never been submitted to an API. Worse, it
carried a *rationale*, which is what made it credible: the reasoning was internally coherent
and entirely fictional. **A number with an explanation attached is harder to doubt than a bare
number, so it deserves more scrutiny, not less.** Same family as I-051.

---

### I-080 — The recorded "latest per key" CDF pattern resurrects deleted rows — **SILENT**
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** None, until it was tested. That is the point.

**Cause.** The pattern in `CLAUDE.md` filtered the change stream **before** ranking:

```sql
WHERE _pg_change_type NOT IN ('delete', 'update_preimage')   -- inner WHERE
... ROW_NUMBER() OVER (PARTITION BY key ORDER BY _sort_by DESC) ... WHERE rn = 1
```

Removing `delete` from the window removes the **tombstone that proves the key is gone**. The
last surviving event for a deleted key is then its own `insert`, which ranks first — so the
row is reported as current. Deletes are not merely ignored; they are *inverted*.

**Measured before writing the derivation.** `lb_fleetguard_agent_action_history`: 4 inserts,
2 deletes, and **2** rows actually live in Postgres. The recorded pattern returned **4**. The
corrected pattern returns **2**, reconciling exactly with live Postgres and with the console's
own audit view.

**Fix** — rank over the tombstones, drop them after:
```sql
SELECT * FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY key ORDER BY _sort_by DESC) AS rn
  FROM lb_<t>_history WHERE _pg_change_type <> 'update_preimage'
) WHERE rn = 1 AND _pg_change_type <> 'delete'
```
`_sort_by` is `BIGINT`, so the ordering is numeric — worth confirming rather than assuming,
since a `STRING` sort key would put `"10"` before `"9"` and silently mis-rank any table past
its ninth change.

**Why this one would have been especially bad here.** The rows it resurrects are exactly the
**test data deliberately cleaned out of live Lakebase on 2026-09-04**. The gold layer would
have quietly re-asserted service campaigns and agent actions the operator had verified as
gone — and CDF history is append-only, so the tombstones would keep re-resurrecting them on
every refresh. Cousin of I-073, where a cleanup was verified from its own connection and had
not committed: both are *deletes that did not take effect where it counted*.

**Proved live, end to end, on the real thing.** A defect signal was opened through the console
by the agent (`AGENT-e525ef828d4b`, FORD TRANSIT, 2,456 vehicles, `EXACT`), the trigger fired
by itself and the fact tables went 2→3 and 50→51. The row was then deleted from Lakebase as
test-data cleanup, the trigger fired again, and the fact tables went back to **2 and 50**.
CDF history holds **2** rows for that signal — the insert and its tombstone, permanently — and
the fact table holds **0**. Under the superseded pattern it would have stayed at 3 and 51 with
the deleted row presented as current, for as long as the table existed.

**Encoded as a regression**, not just fixed: `21_cdf_to_gold_facts` asserts
`live_keys + deleted_keys == distinct_keys`, asserts the fact row count equals the live-key
count, and — while any delete exists — asserts the **superseded pattern still over-counts**.
If that last assertion ever stops firing, the check has gone blind and says so.

---

### I-079 — I-030, third appearance: the **batch detector** joins the fleet on exact make/model, so 2 of 48 signals report 0 fleet vehicles against thousands of real trucks — **SILENT**
*Date:* 2026-09-08 · *Status:* **code fixed 2026-09-09; the stored table still carries the old
numbers until B3 rebuilds it** — see "Fixed" below for exactly what is and is not true yet

**How it surfaced.** Not from a test — from reading the *rationales* of an LLM judge that
"failed" the v6 evaluation. Three `fleetguard_rules` failures complained that the agent quoted
fleet counts (232, 1,256) without a match tier. Chasing whether that was a judge artefact
turned up the reason there is no tier to quote: `gold_emerging_signal.fleet_vehicles` is not
computed with tiers at all.

**Cause.** `src/backtest/10_emerging_signals.py` joins the detector output to the fleet with
`f.make = r.make AND f.model = r.model` — exact, both sides. But `r.make`/`r.model` come from
**complaint data** (NHTSA's spelling) and `f` is built from `gold_fleet_vehicle` (**vPIC's**).
This is I-030 again, in a third code path, after `gold_fleet_exposure` handled it correctly in
Phase 2 (I-030) and `agent_actions.execute` reintroduced it in Phase 7 (I-075).

**Measured.** Two signals report `fleet_vehicles = 0` while the fleet demonstrably holds the
vehicles:

| signal | reported | fleet actually holds |
|---|---:|---:|
| `RAM` / `PROMASTER` — engine and engine cooling | **0** | **2,418** |
| `CHEVROLET` / `SILVERADO 1500` — forward collision avoidance | **0** | 766 |

NHTSA writes `PROMASTER`; the registry writes `PROMASTER 1500` / `2500` / `3500`. So the
published **"2 fleet-relevant"** is really **4 of 48** under the same variant rule the gold
layer already uses.

**Do not confuse this 4 with the console's 4.** The Emerging tab currently shows
"**4** affecting your fleet" out of **50** — that is 48 batch signals plus **2 agent-opened
ones**, of which 2 batch + 2 agent have `fleet_vehicles > 0`. Same digit, different
population, arrived at a different way. If I-079 is fixed the tile becomes **6 of 50**, not 4.
Two numbers this easy to conflate are worth stating together every time either is quoted.

**Read the 2,418 with the caution the tier exists to carry.** It includes 315 `PROMASTER CITY`
— a small van, arguably not the same vehicle as a full-size ProMaster. That is precisely why
`MODEL_VARIANT` is reported rather than blended into an unlabelled count (I-075): the variant
tier is probabilistic, and this is a case where a human should confirm before acting. The
right fix reports 4 signals with their tiers, not a bigger number presented as certain.

**Blast radius is smaller than it looks, which is why it survived.** `live_relevant` is
**0 under both matchings** — neither affected signal is still firing, so nothing in the demo's
live set changes and no screen currently shows a wrong number. The error is confined to the
historical 48 and to the aggregate "N affecting your fleet".

**Fixed in code 2026-09-09 — but read what that does and does not mean.** The *build* is
corrected and the *stored table is not yet rebuilt*, so *right now* `gold_emerging_signal` and
Lakebase still hold the two zeros. Nothing is wrong with that: the rebuild belongs to B3, and
running it twice is exactly what the deferral policy exists to prevent. Do not quote this issue
as "the counts are right" until B3 has run.

What changed:

| file | change |
|---|---|
| `src/backtest/10_emerging_signals.py` | exact join → tiered `EXACT`/`MODEL_VARIANT` match, plus a `match_basis` column and a per-tier summary printed on each run |
| `src/lakebase/14_load_signals.py` | `match_basis TEXT` added to the idempotent `ADD COLUMN IF NOT EXISTS` list and to the load `SELECT` |
| `routers/signals.py`, `api.ts`, `Signals.tsx` | tier carried through the API and rendered as a `VARIANT` badge beside the count |
| `tests/test_signals_routes.py` | new, 7 tests — the router had no dedicated test file before |

**Verified before writing it, against live data.** The tiered expression was run read-only
against the warehouse and reproduces this entry's measured numbers exactly — `RAM PROMASTER`
2,418, `CHEVROLET SILVERADO 1500` 766 — and changes **only those two rows**; the other 46 are
byte-identical. Both remain `is_live = false`, so the live set still moves by zero and no demo
screen changes today. Confirming the blast radius was as small as claimed mattered more than
confirming the two numbers, because "only these two change" is the part that was assumed.

**The tier is reported, not blended, and that is the point.** Fixing the count alone would have
traded a wrong `0` for a misleading `2,418` — that figure includes 315 `PROMASTER CITY`, a
different class of van. `MODEL_VARIANT` is probabilistic; §7's determinism guarantee covers
`EXACT` only. The console now shows the count with a `VARIANT` badge rather than a bare number.

**Agent-opened rows carry NULL, deliberately.** `agent_actions.py` already computes the tier for
its chat reply (I-075) but does not persist it to `fleetguard_defect_signal`. Adding that write
would mean the column must exist in Postgres *before* the code ships, and the migration only
runs in B3 — so persisting it now would break the live agent write path for a cosmetic gain.
NULL reads as "not recorded", which is true, rather than `NONE`, which would falsely claim the
fleet was checked and found empty. Worth folding into B3 once the column exists.

**Still pinned to old numbers, and expected to break in B3:** the agent smoke test
(48 / 2 / RAM 2500 first at 1,256) and the console's "N affecting your fleet" tile, which
becomes **6 of 50**, not 4. Budget time to update the pins rather than treating the break as a
regression — and see the paragraph above about not confusing that 6 with the console's current
4, which is a different population.

**Lesson — the one from I-076, now demonstrated twice in a day.** I wrote that morning that
"when a corpus mismatch is recorded, the question is not *is this path fixed* but *which other
paths join these two things*." I then fixed two paths and did not grep for the third. A search
for `f.make = r.make` would have found it in seconds. **The lesson is only worth what the
search after it is worth.**

Second lesson: **a "failing" LLM judge is evidence about the system, not just about the
judge.** The instinct was to dismiss these as artefacts — and two of the four genuinely are
(a complaint count read as a vehicle count; proposing read as launching, which the
deterministic scorer correctly passed). Reading the rationale of the other two found a real
bug that no deterministic scorer in the suite was looking for.

---

### I-078 — The SDK's `serving_endpoints.query()` silently drops a `ResponsesAgent`'s entire answer — **SILENT**
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** Verifying the freshly-deployed v6, the endpoint returned **HTTP 200** and a
well-formed object — with no answer in it:
`{"id": "...", "served-model-name": "bootcamp_students-fleetguard-fleetguard_agent_6"}`.
The first read of that was "the agent came back empty", i.e. the deploy is broken.

**Cause.** `WorkspaceClient.serving_endpoints.query()` parses the response into
`QueryEndpointResponse`, a dataclass modelled on chat/completions (`choices`, `predictions`,
…). A `ResponsesAgent` replies with `output` items, which is not a field on that dataclass,
so `as_dict()` returns only the keys that happened to match. **Nothing raised, nothing
warned, and the status code was 200.** The agent was fine the whole time.

**Fix.** Query the way the console already does — a raw `POST` to
`/serving-endpoints/<name>/invocations` — and read `output` off the JSON directly. This is
not a workaround so much as using the same path the product uses; `routers/chat.py` never had
this bug because it never used the typed helper.

**Lesson.** This is the *tooling-lies-about-success* pattern (STATUS risk 4) in a new place:
previously it was exit codes and `result_state`. Here it is a **typed client silently
discarding fields it does not model** — a shape where the transport succeeded, the parse
"succeeded", and the payload was quietly thinned. When a response object comes back suspiciously
small, print the raw body before concluding anything about the service that produced it.

---

### I-077 — A *description* rule was read as a *permission* rule, and the agent stopped asking to be allowed to do what it had just been told to do
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** Told plainly to open a defect signal, the agent replied with a bulleted summary
of the request-versus-save mechanism and waited for approval. The user had already authorized
the action; the agent asked for it again, in the words of the rule meant to govern how it
*describes* the write.

**Cause.** `SYSTEM_PROMPT` rule 6 says `open_defect_signal` "is a REQUEST, not a save" and
enumerates phrasings to avoid. That is a claim about **vocabulary** — do not say "saved" when
the console has not yet written — but it reads equally well as a claim about **authority**,
and the model took the stronger reading. Nothing in the prompt said which it was.

**Fix.** Split the one rule into three, and say explicitly what rule 6 is *not*: it "governs
how you DESCRIBE the action — it is NOT a permission gate." New rule 7 states that being asked
to open a signal **is** the authorization, forbids reciting rule 6 back as something to
approve, and gives the model somewhere to go instead of stalling: call `lookup_fleet_models`
(I-076), act on the most defensible reading, and **state the assumption**. Clarifying
questions are reserved for genuinely unanswerable requests, not merely underspecified ones.

**Lesson — new in kind, and the mirror of I-051.** I-051 was a spec that accumulated
*intentions that read as descriptions*. This is a prompt whose *description* read as a
*prohibition*. Both are invisible to tests: the agent's behaviour was safe, well-phrased and
fully compliant with every rule as written — it was just useless. A safety rule that does not
say which of "how to speak" and "what you may do" it constrains will be read as the more
restrictive of the two, because that is the safer guess for the model to make.

---

### I-076 — Every read tool was keyed by `campaign_id`, so the agent was asked to name a fleet scope in a vocabulary it could not inspect
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** Asked to open a steering signal for the RAM 2500, the agent stalled to ask
whether it should instead scope to the "Dodge 2500/3500 cluster". **This fleet holds zero
Dodge vehicles** — verified live, 0 of 20,000 — so that scope would have written a signal
recorded against nobody.

**Cause.** The agent had five tools and none of them answered *"what does this fleet
operate?"*. `lookup_fleet_exposure` is keyed by `campaign_id`, `lookup_emerging_signals`
returns whatever the detector already found. So the make and model reaching
`open_defect_signal` came from **complaint narratives** — NHTSA's vocabulary — while the
count is computed against `fleetguard_vehicle`, which is vPIC's. The agent could not check
its own scope before committing to it, and only learned the fleet count *after* the console
had written the row.

**Fix.** `lookup_fleet_models(make=None)` reads `gold_fleet_vehicle` — deliberately, not
incidentally: Lakebase's `fleetguard_vehicle` is loaded from that table column-for-column, so
the spellings it returns are exactly the strings `agent_actions.execute` will match on. It
returns the make roster **whether or not a make was supplied**, so a model asking about a make
the fleet does not own sees the real alternatives in the same result rather than guessing
twice. `make_in_fleet` is `True`/`False`/`None` (no filter), keeping "I looked and this make
is absent" distinguishable from "I could not look" — `_run_sql` still raises on failure.

**Relationship to I-075.** Same root vocabulary gap, opposite end of the pipe. I-075's
`match_basis` is a **backstop**: it repairs a count after the model has already chosen a
spelling, and states which tier produced it. I-076 fixes it at the **source**, so the model
chooses the fleet's spelling in the first place. Both are wanted — the backstop still covers
the case where the model skips the lookup.

**Verified live before packaging**, against the warehouse with the tool's exact SQL and
parameter binding: 15 makes · 47 make/model combos (46 distinct model names — `SPRINTER` runs
under two makes, so a model name alone does not identify a series) · 20,000 vehicles ·
`FORD`/`F-250` = 2,116 and no `F-250 SD` · `make='ford'` resolves case-insensitively ·
`make='DODGE'` returns `make_in_fleet=False` with an empty model list **and** the full roster.

**Verified again after deploying v6**, against the live endpoint: asked what the fleet
operates, it returned Ford 8,619 / RAM 4,304 / Freightliner 1,676 / F-250 2,116 / RAM 2500
1,256 — every figure matching the warehouse. Replaying the original failure, it scoped to
`RAM` / `2500` and reported the 1,256-vehicle count in the answer, where v5 had offered to
widen to a make the fleet does not own.
The CLI check used a literal make; the parameterised path was exercised separately, because
I-056 was exactly a `StatementParameterListItem` binding that worked in one shape and not
another.

**The resource declaration is the part most likely to be forgotten.**
`gold_fleet_vehicle` had to be added to `resources` in the logging cell. Omitting it breaks
nothing at build or deploy time — it produces a `FAILED` statement at demo time, which is
I-050's exact shape, in a tool added to prevent a different silent-zero bug.

---

### I-075 — I-030 reached the agent path: exact-only model matching reported **0** fleet vehicles for 2,116 real trucks — **SILENT**
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** The first live end-to-end test of `open_defect_signal` produced a well-formed,
correctly-attributed, fully-committed signal — for a genuine corroded-brake-line pattern on
the Ford F-250 — reporting `fleet_vehicles = 0`.

**Cause.** `agent_actions.execute` matched the fleet registry on `make = X AND model = Y`
exactly. But the two sides spell models differently, and the agent sits on the wrong side of
that difference: its evidence is **complaint text**, so it names models the way **NHTSA**
does (`F-250 SD`), while `fleetguard_vehicle` is built from **vPIC** (`F-250`). This is
exactly I-030, which was measured back in Phase 2 for *recall* matching — the same root
cause resurfacing on a new code path that was written without it in mind. Measured live:
`exact = 0`, `variant = 2116`.

**Why it is worse than a normal off-by-something.** Zero is the most damaging value this
column can take. The Emerging tab **orders by `fleet_vehicles DESC`**, so a real brake defect
on 2,116 trucks sorted to the bottom of the operator's page looking like it touched nobody.
Nothing errored, the row was valid, the audit trail was complete, and CDF replicated it
faithfully — the number was simply wrong, and every surrounding signal of correctness said
it was fine.

**It was also non-deterministic**, which is what makes it a trap. Two runs against the same
endpoint, same defect, same trucks: one emitted `F-250 SD` → **0**, the next emitted `F-250`
→ **2,116**. Whether a signal looked urgent or irrelevant depended on the model's spelling
that turn. A single test run — in either direction — proves nothing here.

**Fix.** Match in the same tiers the gold layer already uses, and *report which tier
answered* rather than blending them: `EXACT` → `MODEL_VARIANT` → `MAKE_ONLY` → `NONE`. The
variant test is anchored to a word boundary in both directions
(`%(model)s LIKE model || ' %'` / the reverse), so `F-250` matches `F-250 SD` while `F-2`
does not — verified live. The tier is returned in `ActionResult.match_basis` and persisted
into `fleetguard_agent_action.tool_output`, because the count alone cannot be audited later:
2,116 means "these exact rows" under `EXACT` and "these rows, across a spelling difference"
under `MODEL_VARIANT`. The UI annotates only the variant tier — labelling every count would
train the operator to skip the label.

**Deliberately not widened.** A make with no model match at any tier stays **0**; it does not
fall back to the make-wide count. `MAKE_ONLY` applies only when no model was *supplied*.
Falling back there would manufacture exposure that does not exist — the mirror of the bug.

**Lessons.**
1. **A known data-quality finding does not stay contained to the code path that discovered
   it.** I-030 was measured, documented, and correctly handled in `gold_fleet_exposure` — and
   then silently reintroduced months later by new code joining the same two vocabularies.
   When a corpus mismatch is recorded, the question is not "is this path fixed" but "which
   *other* paths join these two things".
2. **Test the tier logic, not the fixture shape.** The first version of the new tests failed
   against the old code with `KeyError: 'n'` — a fixture mismatch, which proves only that the
   query changed. Re-verified by mutating the *selection logic* while keeping the new SQL:
   the variant test then failed with `assert 0 == 2116`, the actual defect.
3. Same reinforcement as I-074: the SQL was executed against the live database before being
   trusted, because a fake cursor cannot type-check or evaluate it.

---

### I-074 — A bare `%(p)s IS NULL` gives Postgres no type to infer, and no fake-cursor test can catch it
*Date:* 2026-09-08 · *Status:* resolved

**Symptom.** `agent_actions.execute` computes a signal's fleet relevance with an optional
model filter:

```sql
WHERE make = %(make)s AND (%(model)s IS NULL OR model = %(model)s)
```

Every unit test passed. The first live execution failed immediately:
`psycopg.errors.AmbiguousParameter: could not determine data type of parameter $2`.

**Root cause.** psycopg binds parameters **server-side**. Postgres infers each parameter's
type from its first use — and the first use of `$2` here is a bare `$2 IS NULL`, which is
type-agnostic, so there is nothing to infer from. psycopg2's client-side interpolation would
have substituted a literal and never hit this; psycopg3 does not (the same family of
difference as I-066, where `IN %s` stopped expanding tuples).

**Resolution.** Cast at the first appearance: `(%(model)s::text IS NULL OR model = %(model)s)`.

**The part worth keeping.** The router tests added on 2026-09-07 use a fake cursor keyed by
SQL substring — deliberately, because they exist to test *the Python around the SQL*. That
design has a blind spot this bug landed squarely in: **a fake cursor never parses, plans or
type-checks a statement**, so any SQL that is syntactically fine but semantically invalid
passes every one of them. No amount of additional fake-based testing would have caught it;
only executing against a real Postgres did.

So the rule is not "write more unit tests" — it is: **any new SQL must be executed against the
live database at least once before it is trusted**, however well covered its surrounding
Python is. The existing integration suite (`pytest -m integration --run-integration`) is where
that belongs.

### I-073 — A "cleaned up" test-data delete was verified from its own connection and had not committed
*Date:* 2026-09-07 · *Status:* resolved

**Symptom.** After the I-063 concurrency verification, the two test campaigns it created were
deleted and the cleanup reported success — it printed the deleted row counts and then a
`SELECT` showing only the preserved `SC-17V629000-b3b9dfbd` remained. Half an hour later an
unrelated query returned **614 work orders** instead of the expected 25, and both "deleted"
campaigns were still present.

**Root cause.** The verifying `SELECT` ran on **the same connection** as the deletes. A
session always sees its own uncommitted writes, so the check could not have failed regardless
of whether anything committed — it confirmed the statements had *executed*, not that they had
*persisted*. The script also omitted an explicit `commit()`/`close()`, relying on
`psycopg.Connection.transaction()`'s exit behaviour; whatever the precise interaction, the
verification could never have detected the difference.

**Resolution.** Re-ran with an explicit `commit()` and `close()`, then verified from **a fresh
connection** and, separately, through the running API — three independent readers. Confirmed:
one campaign, 25 work orders.

**Lesson, and it generalises past this repo.** *Verifying a write from the connection that
performed it proves nothing about durability.* Any cleanup or migration whose success is
reported to a human must be confirmed by a reader that could observe the failure — a new
connection, a different process, or the application itself. This is the same failure shape as
I-050 and I-012: a check that cannot fail is not a check, and it is more dangerous than no
check because it manufactures confidence. Cheap to get right; it was believed for half an hour
that the demo database was clean when it was not.

### I-063 — Approving the same recall twice created a duplicate campaign and a full duplicate set of work orders
*Date:* 2026-09-02 (found) · 2026-09-07 (resolved) · *Status:* resolved

**Symptom.** `POST /campaigns/{id}/service-campaign` had no idempotency check. A second
approval of the same recall created a second `fleetguard_service_campaign` row and a second
work order per exposed vehicle. **Observed in practice, not just in review:** the 2026-09-04
test-data cleanup turned up six campaigns for recall `17V629000`, several with an identical
auto-generated title minutes apart — repeated approvals during manual testing.

**Held open deliberately for five days.** Whether a second approval should be blocked, return
the existing campaign, or something else is a product decision, not a code-review call. Decided
2026-09-07: **block it, 409.** It matches how the rest of this app behaves — refuse plainly
rather than silently no-op — and the realistic trigger is a demo viewer double-clicking
Approve.

**Why the handler check alone would not have been a fix.** The case being defended against is
a double-click: two requests milliseconds apart. `SELECT`-then-`INSERT` in the handler is a
textbook TOCTOU race, and under Postgres READ COMMITTED both requests reading "nothing there"
before either commits is not an unlucky edge case — for simultaneous requests it is the
*expected* interleaving. Enforcement therefore lives in a **partial unique index**,
`ux_fg_service_campaign_active ON fleetguard_service_campaign (campaign_id) WHERE status =
'LAUNCHED'` (`src/lakebase/19_add_service_campaign_uniqueness.py`). The handler's `SELECT`
remains, but only to produce an error message that names the campaign already running; the
index is what actually serialises the requests.

**Partial, on `status = 'LAUNCHED'`, deliberately.** A cancelled campaign must not block
relaunching the same recall later — launch, cancel because the remedy changed, relaunch is a
real workflow. The migration proves *both* directions against live Postgres, because a test
that only proves rejection would pass just as happily against a plain (wrong) unique index on
`campaign_id`.

**Verified end to end, 2026-09-07.** Sequentially: second approval returns 409 naming the
existing campaign. Concurrently: five simultaneous approvals of one recall yielded **exactly
one 201, four 409s, and one campaign row** with its single set of 384 work orders. Pre-fix
that same input produced five campaigns and 1,920 work orders.

**A test-quality note worth keeping.** The first draft of `tests/test_approval_idempotency.py`
asserted only `status_code == 409` for the duplicate case — and passed with the duplicate
check deleted, because `approve_campaign` has an unrelated 409 ("exposes no vehicles in
scope") that fired instead when the fake returned no exposure rows. Caught by re-running the
new tests against the pre-fix handler, which is the only way that class of false confidence
shows up. **When several code paths return the same status, assert on the message too, and
supply enough fixture data that the other paths cannot fire.**

### I-072 — `datetime.utcnow()` in the audit-log CSV filename is deprecated and scheduled for removal
*Date:* 2026-09-07 · *Status:* resolved

**Symptom.** Trivial, but certain: `audit_log.py`'s CSV export built its filename with
`datetime.utcnow()`, which emits `DeprecationWarning` on Python 3.12+ (confirmed under
`-W error::DeprecationWarning` on this project's 3.14.7) and is scheduled for removal. It
would go from a silent warning to an outright `AttributeError` on a future interpreter — the
kind of thing that surfaces during a version bump rather than in review.

**Resolution.** `datetime.now(UTC)`. The literal `Z` in the format string is kept
deliberately: `strftime` has no directive that renders `Z` for a UTC offset, and the filename
is a display string, not a parsed timestamp. Verified the output format is unchanged
(`fleetguard_audit_log_20260907T144851Z.csv`) rather than only that the lint went quiet.

### I-071 — I-062's fix landed in the providers only; the one caller that bypasses them kept reporting expired sessions as signed in
*Date:* 2026-09-07 · *Status:* resolved

**Symptom.** Found in an end-to-end repo review, not from an incident. `/api/auth/status`
reported `signed_in: true` — and, for a login in `FLEETGUARD_APPROVERS`, `may_approve: true`
— for a session well past its 8-hour TTL. The console draws its signed-in header and its
Approve affordance from exactly this response, so the visible result was a console that
looked fully authenticated while every data request underneath it returned 401.

**Not an access-control hole, and worth being precise about that.** Enforcement was always
correct: every data endpoint resolves through `deps.current_principal` → a token provider →
`_check_not_expired`, all of which honoured the TTL. No expired session ever read fleet data.
What leaked was *UI state*, not data — but "signed in, may approve, everything fails" is a
genuinely confusing state to hand an operator, and the `may_approve: true` half is the kind
of thing that looks like a security finding at a glance even though it isn't one.

**Root cause — the interesting part.** I-062 added server-side expiry after the same class of
review. That fix was correct but incomplete in a specific way: it landed in `auth/tokens.py`,
where the *providers* live, because that is where authorisation happens. `auth_status` is the
one caller that deliberately does **not** go through a provider — it must answer for
unauthenticated callers too, so it reads `deps.SESSIONS` directly. It therefore kept its own,
now-divergent notion of "is this session valid": `if session` — mere existence. Two
definitions of validity, one updated, one not.

**Also closed here: I-062's unbounded-growth half.** Rejecting an expired session on read
never *removed* it, and `SESSIONS` only shrank on explicit logout — which most users never do,
they close the tab. I-062's own writeup named this ("also an unbounded-growth issue on a
long-running Render process") but its resolution only covered the access-control half.

**Resolution.** `tokens.session_is_live()` is now the single predicate; `_check_not_expired`
is its raising twin and delegates to it, so the two cannot drift again — a parametrized test
asserts they agree on the same inputs, which is the actual guard against recurrence.
`auth_status` uses the predicate and drives `deps.prune_sessions()`, which evicts expired
entries (chosen over `current_principal` as the call site: frequent enough to keep the dict
bounded, well off the hot path of every data request). 17 tests in
`tests/test_session_lifecycle.py`, four of which were **verified to fail against the pre-fix
code** before being committed — `signed_in` true, `may_approve` true, the store not
shrinking, and the race below.

**Caught while writing the fix, not after:** the first draft of `prune_sessions` iterated
`_SESSIONS.items()` directly. FastAPI runs sync endpoints in a threadpool, so a login
completing on another worker can insert mid-iteration — `RuntimeError: dictionary changed
size during iteration`, on the endpoint the console calls on every mount. Fixed with a
`list(...)` snapshot and pinned by a test that drives the insert from inside the
comprehension (via `now_fn`) rather than hoping for a real thread collision; that test was
confirmed to fail on the unguarded version. A fix for an unbounded-growth bug that
intermittently 500s the auth endpoint would have been a poor trade.

**Lesson.** When a fix is described as "added to the providers", check for callers that
deliberately bypass the providers — they are usually bypassing them for a good reason
(`auth_status` must serve unauthenticated callers) and are therefore exactly the ones a
provider-level fix cannot reach. The durable repair is one shared predicate, not two correct
implementations.

### I-070 — RLS on `fleetguard_vehicle` overrides app-level scoping intent, silently, for any identity with a real depot assignment
*Date:* 2026-09-05 · *Status:* open (informs future work, not fixed)

**Symptom.** While building (later shelved) role-based views, a depot-risk view explicitly
designed to stay fleet-wide for every viewer — real component numbers across all 60 depots,
no per-depot narrowing, by deliberate decision — started showing `fleet_size: 0` and
`urgent_vehicles_exposed: 0` for every depot except the one identity that had a real row in
`fleetguard_depot_assignment`. Caught via a screenshot review, not a test.

**Root cause.** `fleetguard_vehicle` has had real Postgres RLS since Phase 10
(`ENABLE` + `FORCE`, `src/lakebase/15_enable_depot_rls.py`) — a principal with a row in
`fleetguard_depot_assignment` sees only that depot's vehicles, enforced *below* the
application, for **every** query that touches the table, regardless of what the calling
application code intended. `fleetguard_vehicle_exposure` (the table linking vehicles to
recall campaigns) has no `depot_id` column of its own, so any depot-level rollup of
exposure — vehicles exposed, campaigns affecting a depot, fleet size — has to join through
`fleetguard_vehicle` to get there. The moment *any* identity anywhere has a real depot
assignment, RLS silently narrows that join for that identity, even on an endpoint whose own
code never asked for scoping and whose product decision was explicitly "stay fleet-wide."
This is real enforcement working exactly as designed at the Postgres layer — the surprise is
that it applies unconditionally, with no way for a specific query to opt out while connected
as that identity.

**Not fixed.** The clean fix is denormalizing `depot_id` onto `fleetguard_vehicle_exposure`
(populated once from a privileged load connection, same pattern as the initial fleet-registry
load) plus a static `fleet_size` column on `fleetguard_depot`, so a "stay fleet-wide" view
never needs to touch the RLS-protected table at all. Not built — this finding is what
convinced the project role-based views wasn't worth finishing right now (see `docs/STATUS.md`
Phase 13 / "Next" item 0.5): the feature has zero visible footprint in the default demo state
(nobody is enrolled by default), so paying for a real schema migration to fix a bug nobody
will hit outside of deliberately demoing the feature itself was the wrong trade this close to
a fixed demo date.

**Lesson for whoever picks depot-scoping back up:** any new view or endpoint that claims to
stay fleet-wide regardless of role must be checked against every RLS-protected table it
touches, transitively through joins — "my code doesn't apply a depot filter" is not the same
guarantee as "this data is fleet-wide," once RLS is live on any table in the join path.

### I-069 — A labeled assumption is still an assumption; the fix was real data, not a better disclaimer
*Date:* 2026-09-04 · *Status:* resolved

**Symptom.** A first "cost of early action" feature multiplied one editable "$ assumed cost per
vehicle" input by the count of completed work orders, with a footnote explicitly stating the
dollar figure was an assumption, not a measurement. It looked compliant with this project's own
"never assert a number that hasn't been measured" discipline — the assumption was visible and
adjustable, not hidden.

**Root cause.** Labeling an assumption honestly does not fix the deeper problem: a single flat
multiplier applied uniformly to every vehicle is structurally wrong regardless of how
transparently it's disclosed, because different repairs cost different amounts (a steering-rack
repair on a Class 8 tractor and a brake job on a pickup are not the same cost). The feature was
solving "how do we disclose an assumption honestly" when the real question was "why are we
guessing at all when the actual cost is knowable per repair." Caught not by a code review but by
the user asking what the feature was even for, given vehicles clearly cost different amounts to
service.

**Resolution.** Replaced same-day with real per-work-order cost capture instead of a better
disclaimer: `fleetguard_work_order.actual_cost` (`src/lakebase/
18_add_work_order_actual_cost.py`), logged manually via `PATCH /work-orders/{id}`, summed and
broken down by component and by depot (`GET /api/cost-breakdown`) rather than blended into one
number. Every aggregate reports `costed_count` alongside its total specifically so partial
coverage is never presented as a complete picture. See `docs/ARCHITECTURE.md`'s cost-tracking
entry for the full design. **Lesson for future numbers in this app:** if a claim needs a
disclaimer to be honest, check whether the disclaimer is covering for a wrong design, not just
an unmeasured one — those are different problems with different fixes.

### I-068 — New Lakebase scripts must copy `db.py`'s conditional `PSYCOPG_IMPL`, not `15_enable_depot_rls.py`'s unconditional one
*Date:* 2026-09-04 · *Status:* resolved

**Symptom.** `16_add_work_order_status_check.py`, freshly written by copying
`15_enable_depot_rls.py`'s connection boilerplate, failed immediately on local execution:
`ImportError: couldn't import requested psycopg 'python' implementation: libpq library not
found`.

**Root cause.** `15_enable_depot_rls.py` unconditionally sets
`os.environ.setdefault("PSYCOPG_IMPL", "python")` — safe because that script has only ever
been run from an actual Databricks notebook, where the pure-Python impl is required (I-045,
FIPS self-test failure on serverless). `db.py::_select_psycopg_impl` does this conditionally
(`if os.getenv("DATABRICKS_RUNTIME_VERSION")`) for exactly the reason stated in its own
docstring: macOS has no system libpq, so forcing the pure-Python impl locally breaks import
entirely. Copying the simpler unconditional version into a new script that *is* run locally
(as every Lakebase migration in this session was) reintroduces a bug `db.py` had already
fixed elsewhere in the codebase.

**Resolution.** New script matched `db.py`'s conditional check instead. **Any future
`src/lakebase/*.py` script that might run locally must do the same** — copy the pattern from
`db.py`, not from `15_enable_depot_rls.py`, even though the latter looks like the more direct
template for "a Lakebase migration script."

### I-067 — Postgres has no `ADD CONSTRAINT IF NOT EXISTS`
*Date:* 2026-09-04 · *Status:* resolved

**Symptom.** `ALTER TABLE ... ADD CONSTRAINT IF NOT EXISTS fg_wo_status_check CHECK (...)`
failed with `psycopg.errors.SyntaxError: syntax error at or near "EXISTS"`.

**Root cause.** Unlike `ADD COLUMN IF NOT EXISTS` (which Postgres does support, and which
`CREATE TABLE IF NOT EXISTS` / `DROP POLICY IF EXISTS` elsewhere in this codebase's Lakebase
scripts correctly rely on), there is no `IF NOT EXISTS` variant for `ADD CONSTRAINT` in any
Postgres version. Assumed it existed by analogy with the other `IF NOT EXISTS` forms already
in use — wrong.

**Resolution.** Idempotency has to be a manual existence check against `pg_constraint`
(`SELECT 1 FROM pg_constraint WHERE conname = %(name)s AND conrelid = %(table)s::regclass`)
before running the bare `ADD CONSTRAINT`. `16_add_work_order_status_check.py` does this and
was verified idempotent by running it twice.

### I-066 — psycopg3's `IN %s` does not expand a tuple parameter the way psycopg2's did
*Date:* 2026-09-04 · *Status:* resolved

**Symptom.** `cur.execute("... WHERE status NOT IN %s ...", (ALLOWED_TUPLE,))` failed with
`psycopg.errors.SyntaxError: syntax error at or near "$1"` — the tuple was bound as one
opaque parameter (`$1`) rather than expanded into `('OPEN', 'IN_PROGRESS', ...)`.

**Root cause.** psycopg2 substituted parameters client-side, so a Python tuple passed for an
`IN %s` placeholder was rendered as a literal parenthesised list in the query text before
sending it. psycopg3 binds parameters **server-side** by default — the value is sent as a
single bind parameter, and Postgres has no server-side way to expand one bind parameter into
an `IN (...)` list. The `IN %s` + tuple idiom that "just works" in psycopg2 is a silent
footgun when carried into psycopg3 code (this codebase already uses psycopg3 throughout, per
`db.py`).

**Resolution.** Use `= ANY(%(name)s)` with a Python **list** instead of `IN %s` with a tuple
— psycopg3 adapts a list to a Postgres array cleanly, and `= ANY(array)` is the correct
psycopg3-idiomatic equivalent of "column is one of these values." Fixed in
`16_add_work_order_status_check.py`'s safety check; worth checking for the same pattern if
any future script reaches for `IN %s`.

### I-065 — `aitools tools statement submit --file` silently mangled YAML containing literal `%` characters
*Date:* 2026-09-03 · *Status:* resolved (switched tool), root cause not confirmed

**Symptom.** Deploying `evidence_metrics` (a UC Metric View, `CREATE OR REPLACE VIEW ... WITH
METRICS LANGUAGE YAML`) via `databricks experimental aitools tools statement submit --file
evidence_metrics.sql` failed twice with `[METRIC_VIEW_INVALID_VIEW_DEFINITION] ... Failed to
parse YAML: ... expected <block end>, but found '-'`, at a reported line/column that did not
correspond to anything wrong in the file — the same content parsed clean locally via PyYAML
(`yaml.safe_load`) both times. Shortening the offending comment string changed the *reported*
column but not the failure, ruling out a simple line-length theory.

**Diagnosis.** The YAML's measure names and `expr` values contain literal `%` characters —
`Detection Rate %`, `LIKE 'REAL%'`, `LIKE 'PLACEBO%'` — the one thing unusual about this file
compared to prior successful DDL submitted the same way. Not root-caused to a specific line in
the CLI's source (out of scope to dig into), but the pattern — content that parses correctly
everywhere except through this one code path, specifically containing `%` — is consistent with
printf-style `%`-interpolation somewhere in the experimental command's file-read/submit path
silently corrupting the payload before it reaches the warehouse.

**Resolution.** Switched to the skill's own documented fallback — the stable Statement
Execution REST API (`databricks api post /api/2.0/sql/statements --json @payload.json`,
payload built with Python's `json.dumps` for correct escaping) — which deployed the identical
file content successfully on the first attempt.

**Lesson.** `databricks experimental aitools tools` commands are explicitly unstable (the
skill says so), and this is a concrete case of that instability, not just a version-skew risk.
Any DDL containing literal `%` — metric view measure names, `LIKE` patterns, format strings —
should go through the stable REST API path by default rather than the experimental file-submit
command, at least until this is root-caused or fixed upstream.

---

### I-064 — Metric view almost built on `gold_lead_time_v3`, the newer-sounding but wrong table
*Date:* 2026-09-03 · *Status:* resolved (caught before deploying)

**Symptom.** None visible — this is a near-miss, not a production bug. While building
`evidence_metrics` (the governed metric view for the Evidence page's backtest numbers), the
natural first candidate source was `gold_lead_time_v3`: it's the most recent-sounding of three
lead-time tables (`gold_lead_time_backtest`, `_control`, `_v3`), it's already unioned across
both arms at investigation grain (1,383 rows = 777 real + 606 placebo, matching the published
population exactly), and it has an explicit `detected_v3`/`lead_days_v3` pair that reads as
"the current detector."

**What it actually was.** Querying `gold_lead_time_v3.detected_v3` reproduces **11.2%** for the
real arm — the exact figure `docs/STATUS.md` already documents as the **abandoned, negative**
semantic-clustering result ("Adding semantic clustering was tested and made detection worse
(11.2%, with zero extra lead time)"). `detected_v2` gives 13.3% — an earlier, also-superseded
intermediate value. Neither matches the shipped 16.0%/11.1%. The actual published numbers only
live in `gold_lead_time_backtest.detected` (real arm, verified 124/777 = 16.0%, median lead
197d, exact) and `gold_lead_time_summary` (both arms, pre-aggregated). The "v3" suffix tracks a
column-naming history of experiments run on the same table, not "the latest, most-correct arm."

**Resolution.** Ran the aggregation against all three candidate columns
(`gold_lead_time_backtest.detected`, `gold_lead_time_v3.detected_v2`, `.detected_v3`) before
writing any YAML, matched the results against `docs/STATUS.md`'s already-published figures, and
built `evidence_metrics` on `gold_lead_time_summary` instead — the same table
`scripts/export_evidence.py` already reads for the live `/api/evidence` snapshot.

**Lesson.** A table name suffix implying recency (`_v2`, `_v3`) is not evidence of which
experiment shipped — only a documented, already-published number is. Same failure class as
I-051 (the living spec describing harm weighting that doesn't exist): a plausible-sounding
technical artifact that is not what was actually decided. The check that caught it is the one
`CLAUDE.md` already prescribes for this project — verify against a live system or current docs
before writing a claim down, not against training data, intuition, or a table's name.

---

### I-062 — Session cookies had no server-side expiry — the `max_age` was a client-side courtesy only
*Date:* 2026-09-02 · *Status:* resolved

**Symptom.** During an end-to-end repo review — not triggered by an incident — `deps.py` →
`auth/tokens.py`'s `SessionTokenProvider` and `AppLoginTokenProvider` (the provider Render
actually runs) each checked only whether a session **existed** in the store, never how old
it was. The cookie sets `max_age=SESSION_TTL_S` (8 hours), but that only controls when the
*browser* stops sending the cookie — a session value captured or replayed by any other means
(saved before expiry, logged, proxied) was honoured by the server **forever**.

**Compounding factor.** `SESSIONS` (`deps.py`) is an in-process dict with no pruning except
explicit logout. A session nobody explicitly logs out of — the common case, since most users
just close the tab — stays in memory permanently, so this was also an unbounded-growth issue
on a long-running Render process, not only an access-control gap.

**Resolution.** Both providers now take `session_ttl_s` and an injectable `now_fn` (kept
mockable rather than depending on wall-clock time in tests, matching the module's existing
dependency-injection style) and reject a session whose `created` timestamp is missing or
older than the TTL. **Fails closed on a missing `created` field** — a session without one did
not come from this codebase's own login flow (`routers/auth_routes.py::callback` always
stamps it), and treating unknown age as valid would be exactly the "fall back to a broader
principal" this module's own rules forbid. 16 new tests in `tests/test_auth_seam.py`,
including the TTL boundary (`> ttl`, not `>= ttl` — valid through the last second, not
evicted one tick early) and — separately — full coverage for `AppLoginTokenProvider` and the
`app-login` mode's `FLEETGUARD_DATA_MODE=snapshot` invariant, both of which had **zero**
tests before this review despite being what Render actually runs.

**Lesson.** A cookie's `max_age` is a UI convenience for when the browser stops offering the
credential, not an access-control decision — the server has to independently decide a
session's lifetime is over, and nothing here did. It hid for as long as it did because the
provider Render actually runs, `AppLoginTokenProvider`, had zero tests before this review —
a gap in coverage and a gap in security enforcement, in the same module, found together.

---

### I-061 — No `CREATEROLE` on the shared Lakebase instance; `FORCE ROW LEVEL SECURITY` was the fix, not a workaround
*Date:* 2026-09-02 · *Status:* resolved, and the redesign is a better result than the original plan

**Symptom.** `src/lakebase/15_enable_depot_rls.py`'s first run failed at `CREATE ROLE`:
`InsufficientPrivilege: permission denied to drop role — Only roles with the CREATEROLE
attribute and the ADMIN option on the target roles may drop roles.` Measured directly:
`rolcreaterole = false` for this identity on `projects/summer-bootcamp-2026-v2`.

**This is correct behaviour, not a bug to route around.** The Lakebase instance is shared
infrastructure with ~25+ Databricks identities as Postgres login roles (measured 2026-09-01)
across ~296 bootcamp students. Being unable to create or drop arbitrary Postgres roles on
shared infrastructure is the platform working as intended, and the response was to redesign
the proof, not to seek elevated privileges.

**The redesign turned out stronger than the original plan.** The original design created a
purpose-made role, connected as it, and measured restricted row counts — valid, but it also
implicitly relied on something never checked: whether the table **owner's own connection**
was even subject to the policy. It was not — Postgres exempts table owners from RLS by
default. Adding `ALTER TABLE ... FORCE ROW LEVEL SECURITY` closed that gap and let the whole
proof run under this identity's own connection: no new role needed, and the owner-bypass loop
hole — which would have made "the frontend cannot bypass it" false for any owner-connected
caller regardless of policy content — is now closed rather than merely untested.

**Resolution.** Proof redesigned around three states of this identity's own
`fleetguard_depot_assignment` row (absent → present → removed again), each measured with real
row counts under `FORCE ROW LEVEL SECURITY`, asserted, not trusted. Independently re-verified
after the run via a fresh connection: `relrowsecurity=True`, `relforcerowsecurity=True`,
assignment table empty, unrestricted count restored to 20,000.

**Lesson.** A blocked privilege is sometimes information about the correct design, not an
obstacle to the one already chosen. Checking *why* a permission is denied — here, shared
multi-tenant infrastructure with 296 other identities — pointed at a fix (`FORCE`) that closed
a real gap the original design hadn't noticed yet, rather than just working around the block.

---

### I-060 — Model B's first training run had leakage from its own golden-set label rule
*Date:* 2026-09-02 · *Status:* fixed, retraining

**Symptom.** The first Model B run reported `threshold=1.000, precision=1.000,
recall=0.989, ROC-AUC=0.995` on a 230-row held-out test split. Too clean to trust — this
project has caught that exact shape of number before (I-047, I-058) — so it was checked
before being reported rather than written up as a result.

**Root cause.** `05_build_model_b_golden_set`'s negative rule requires
`recall_model NOT LIKE '%model%'` — so for every `label = 0` row, the feature
`model_is_substring_of_recall` is `False` **by definition of the label**, not by anything
learned. Measured directly: that boolean is `True` for 613/621 (98.7%) positives and
**0/144 (0%)** negatives. A classifier fed that feature does not need to learn anything —
it can threshold on a value that is tautologically tied to the label it is predicting.

This is the same *category* of mistake as I-058 (an evaluation artefact mistaken for a real
result) but a different mechanism: I-058 was a broken scorer; this is training-feature
leakage from the label-construction rule itself. I had already excluded `defect_description`
text from the features for exactly this reason and missed that the negative rule also used
`recall_model`, which I had left in the feature set.

**Resolution.** `model_is_substring_of_recall` and `recall_is_substring_of_model` dropped
from the feature set. The continuous fuzzy-match scores (`ratio`, `partial_ratio`,
`token_sort_ratio`, `token_set_ratio`, `token_jaccard`) are kept — they correlate with the
same real naming convention (trim suffixes land on the *recall* side, e.g. `F-250` →
`F-250 SD`; distinguishing suffixes land on the *vehicle* side, e.g. `PROMASTER` →
`PROMASTER CITY`) without being a hard 0/1 identical to the label rule — but are flagged in
the notebook as still optimistic, since the golden set was not built independently of every
feature that scores it.

**Lesson.** Excluding the *field a label was textually derived from* is not sufficient;
every field referenced **anywhere** in the label-construction logic — including the negative
branch, which is easy to write last and check least — has to be excluded from features too.
"I checked for leakage" needs a specific claim attached: leakage from what, checked how.

---

### I-059 — `gold_fleet_exposure` has no source file in the repo
*Date:* 2026-09-02 · *Status:* open, non-blocking

**Symptom.** Starting Phase 4 (Model B), `src/fleet/04_build_fleet_registry.py` was expected
to contain the `EXACT`/`MODEL_VARIANT` matching logic behind `gold_fleet_exposure`. It does
not — that notebook builds only `gold_fleet_vehicle` and `gold_fleet_depot`. The table exists
live (989,042 rows, matching the proposal's measured figures exactly) but whatever built it
was run ad hoc and never committed.

**Recovered from the live schema** (not from source, since none exists): `match_basis` is
`EXACT` when `model = recall_model` after make and manufacture-window agreement, `VARIANT`
otherwise. Confirmed on real rows — e.g. `FREIGHTLINER SPRINTER` vs recall model
`SPRINTER 4500` (variant), and, more importantly, `FORD TRANSIT CONNECT` vs recall model
`TRANSIT` — a substring relationship where Transit Connect and Transit are genuinely different
platforms. That pairing is a live example of exactly the false-positive risk Model B exists to
score.

**Resolution — deferred, not fixed.** Reconstructing the exact original SQL is not on the
critical path for Model B, which reads the table as-is. Logged so the gap doesn't surprise the
next person, and so Model B's own source is written from the start rather than run ad hoc the
same way.

**Lesson.** A table with no corresponding committed notebook is itself worth treating as a
finding — this project's own convention (every Databricks change gets a runbook, every table a
source file) was skipped once, silently, and only surfaced when someone needed to build on it.

---

### I-058 — The agent's evaluation failed it for *promising not to* invent a recall — **SILENT**
*Date:* 2026-09-02 · *Status:* resolved

**Symptom.** The first E-05 evaluation run failed a hard gate:
`HARD GATE FAILED — never_invents_a_recall: 2/10. Do not deploy.` On inspection the agent had
done nothing wrong. Three separate faults stacked up, each of which made the picture worse.

**Fault 1 — the job's state message was not the outcome.** The run reported
`INTERNAL_ERROR / "Run timed out"`. The actual task output was the assertion above: the
evaluation had *completed* and failed a gate. Reading `state_message` and stopping there is
I-043 again, one layer up.

**Fault 2 — the reported denominator was wrong by 3×.** `never_invents_a_recall` returns
`None` for the seven cases it does not judge. The summary helper scanned `eval_results` and
counted anything not literally `True` as a failure, so *not applicable* became *failed* and
`2/3` was printed as `2/10`. MLflow's own aggregate said `0.667` the whole time. Fixed by
reading `run.data.metrics` — MLflow computes means over applicable cases — and never parsing
the results table by hand.

**Fault 3 — the gate itself was wrong, and this is the interesting one.** The scorer scanned
the whole answer for phrases such as `"a recall exists"`. The agent had written:

> "I **do not know** whether NHTSA has issued a formal recall on this. The complaint search
> does not tell me recall status, and **I won't state that a recall exists** when I can't
> verify it."

That is the exact behaviour the rule exists to enforce, and it was scored as a violation —
because the sentence *mentions* the phrase while *denying* it.

The same run's `Guidelines` judge failed five of ten cases, and those were noise too: two
cases were failed for not stating a match tier on answers that reported no vehicle counts at
all (the guideline said "always", applied where meaningless), one alleged the agent quoted
personal detail when it had named vehicle **makes and models** ("Ford F-150, GMC Sierra"), and
one rationale concluded *"the response does not violate any guidelines directly"* and returned
**no** anyway.

**Resolution.**
- Claim detection is now **sentence-wise** and skips any sentence carrying a negation or hedge,
  with punctuation flattened first so `"No, there is a recall"` cannot hide its negation behind
  a comma. Assertion, not mention.
- Guideline wording made **conditional** ("IF the answer reports a count greater than zero…")
  and the privacy rule made **specific** (names, addresses, phone numbers, plates — vehicle
  make/model is explicitly *not* personal detail).
- `tests/test_scorer_negation.py` pins eight assertion-vs-mention cases, including the real
  sentence above. Milliseconds, off-platform — the bug took an hour-long run to surface and
  now cannot recur unnoticed.
- `17_inspect_eval.py` added: reads an existing evaluation run and returns per-case verdicts,
  rationales **and the agent's own answer**, via `dbutils.notebook.exit` so the CLI can read
  them. A scorer alleging a violation is a claim about text; judging that claim without
  reading the text is how a false positive becomes a "finding".

**Outcome.** Six of eight scorers were perfect, including `never_claims_launched` (1.000) and
`grounded_numbers` (1.000 — no I-050 regression). Every failure was in the measuring
apparatus. **The agent passed.**

**Lesson.** I chose a literal phrase list over an LLM judge on the grounds that "a check on
whether the agent overstepped must not itself be probabilistic". That reasoning was right and
the implementation did not honour it: **deterministic is not the same as correct.** A naive
substring match is reliably wrong rather than unreliably right, and it fails in the most
damaging direction — it punishes the careful, hedged, honest answer, which is exactly the
behaviour the system is built to produce.

Corollary: **an evaluation harness needs its own tests.** It is code that judges code, and
nothing was judging it.

---

### I-057 — Google flagged the deployment as a "Dangerous site" — the login wall was the trigger
*Date:* 2026-09-02 · *Status:* resolved (signature removed; false positive reported)

**Symptom.** After completing GitHub sign-in, Chrome interstitialed the Render deployment:
*"Dangerous site — Attackers on the site that you tried visiting might trick you into
installing software or revealing things like your passwords…"*

**First response was to verify, not to explain it away.** A "dangerous site" warning on your
own deployment has exactly two readings — a compromise, or a false positive — and assuming
the flattering one is how a real compromise gets talked past. Checks run:

```
served /assets/index-CF4cbtpj.js   sha256 9caba468…13301a
local  build, same file            sha256 9caba468…13301a   ← byte-identical
```

The served `index.html` also matched the build exactly: no injected script, no third-party
origin, nothing added. Integrity confirmed; the warning was a classifier verdict, not evidence
of intrusion.

**Root cause.** Safe Browsing was reacting to the *shape* of the site, and it was right to.
The entire anonymous surface was a single "Continue with GitHub" prompt, served from a
zero-reputation `*.onrender.com` subdomain that shares a reputation neighbourhood with
whatever else is hosted there. A credential prompt with no surrounding content on a
throwaway-looking host is a textbook phishing signature.

**Resolution.** Two changes, one of which was worth making regardless:
1. The false positive was reported to Google.
2. **Anonymous visitors now land on the Evidence page** — the measured result, its control arm
   and its stated limits — with sign-in as a header action rather than a wall. An explicit
   link (`#/queue`) is still honoured; only the *default* landing changed, because a shared
   link must go where it says.

**Lesson.** The classifier was describing a genuine design mistake in security language. The
public URL exists to show a measured result to people who will never sign in, and the first
thing it showed them was a form. **When an automated system flags your work, check whether it
is wrong about the facts but right about the shape** — here the "phishing signature" and "bad
front door" were the same defect, and fixing the product fixed the flag.

Durable fix if it recurs: a custom domain. `*.onrender.com` subdomains inherit a shared
reputation; a domain you own builds its own.

---

### I-056 — `StatementParameterListItem` sends values as STRING, so `LIMIT :n` is rejected
*Date:* 2026-09-02 · *Status:* resolved

**Symptom.** The agent's new `lookup_emerging_signals` tool failed on first run:

```
INVALID_LIMIT_LIKE_EXPRESSION.DATA_TYPE — The limit like expression "5" is invalid.
The limit expression must be integer type, but got "STRING".
```

**Root cause.** `StatementParameterListItem(name="lim", value=str(limit))` binds a **string**.
That is fine for `campaign_number = :cid`, where the column is a string anyway, and it is
what the existing exposure tool does — so the pattern was copied without noticing that
`LIMIT` is one of the few positions where the *type* matters, not just the value.

**Resolution.** Coerce to a bounded int in Python and interpolate:

```python
lim = max(1, min(int(limit), 50))
... f"LIMIT {lim}"
```

This is safe where interpolation normally is not: after `int()`, the value cannot carry SQL
no matter what the model passed. Parameter binding is still used everywhere the value is a
string (`campaign_id` remains bound). The SDK's `type` field on the parameter item is the
other candidate fix and was not tried — the bounded int is provably safe and needs no
round trip to confirm.

**What went right.** The failure was **loud**. Under the pre-I-050 code this exact error
would have returned `FAILED` with `result = None`, the tool would have handed back an empty
list, and the agent would have reported "no emerging defects affect your fleet" — the same
false all-clear, in the newest tool, the same day the fix landed. Instead `_run_sql` raised
and the build failed before anything was registered or deployed.

**Lesson.** The I-050 guard paid for itself within hours, on a bug that had nothing to do
with permissions. Worth noting the shape: **a rule that turns silent failures into loud ones
catches classes of bug you did not anticipate**, which is the argument for adding them even
when the specific known failure is already fixed.

---

### I-055 — A file that was never written is indistinguishable from one that fails to import
*Date:* 2026-09-02 · *Status:* resolved

**Symptom.** `uvicorn` refused to start:
`ImportError: cannot import name 'signals' from 'fleetguard_api.routers'`. The obvious
readings — a syntax error, a circular import, a bad `__init__.py` — were all wrong.
`routers/signals.py` **did not exist**. The command that would have created it had been
rejected mid-flight by a tool-permission failure, and the failure notice arrived interleaved
with an unrelated background-job notification, so it read as noise.

**Root cause.** Nothing verified the write. Subsequent steps — wiring the import into
`main.py`, adding the frontend client, building the console — all succeeded, because none of
them touch the missing file. The error surfaced two steps later at *process start*, pointing
at the importer rather than at the absent file.

**Resolution.** File recreated; server starts; `/api/signals` verified against live Lakebase.

**Lesson.** Same shape as I-050 and I-043 at a different layer: **a failed operation that
produces no output is silent, and later steps that do not depend on it will happily pass.**
When a write is the thing that matters, confirm the artefact exists (`ls`, or an import) at
the point of writing — not at the point where something else happens to need it. An
`ImportError` naming a *package* is evidence about the package's contents, not about the
module's code.

---

### I-054 — Render serves a cached `/`, so the console page is not a deploy signal
*Date:* 2026-09-02 · *Status:* resolved (verification method changed)

**Symptom.** After pushing the signals build, `curl https://…/` returned the **previous**
bundle (`index-Bxx1SfyE.js`) for several minutes and a polling watcher reported "still
serving old". The natural conclusion — the deploy had not happened, or had failed — was
wrong. It had already succeeded.

**Root cause.** `/` is cacheable and was being served stale. The hashed asset itself
(`/assets/index-CRjJ01hl.js`) was already `200`, and `/?cb=<random>` returned the new bundle
immediately.

**The tell, and the generalisable part.** `GET /api/signals` returned **401**. That is only
possible if the route exists: `main.py` mounts an SPA catch-all, so an *unknown* path returns
**200 with HTML**, never 404 and never 401. A gated route answering 401 is therefore positive
proof that the new backend is live.

**Resolution.** Verify deploys by probing an API route, not the console page. Cache-bust `/`
when its content genuinely matters.

**Lesson.** Choosing the right probe matters more than polling harder. A watcher that polls a
*cacheable* endpoint reports a false negative indefinitely and looks exactly like a slow
deploy — and on a mounted-SPA app, HTTP status codes carry more information than they
normally would, because 404 has been taken off the table.

---

### I-053 — Lakebase notebooks need the `fgenv` serverless environment, not `%pip`
*Date:* 2026-09-02 · *Status:* resolved

**Symptom.** `fleetguard-load-signals` failed at cell 1 with
`ModuleNotFoundError: No module named 'psycopg'` — while `fleetguard-load-exposure`, running
near-identical code, had always worked.

**Root cause.** The working job declares dependencies in a **serverless environment spec**
attached to the task:

```json
"environments": [{"environment_key": "fgenv",
  "spec": {"client": "3", "dependencies": ["psycopg[binary]", "databricks-sdk>=0.89.0"]}}]
```

with `"environment_key": "fgenv"` on the task. The new job was created without it. The
notebook body carries no `%pip` cell precisely *because* the environment supplies the
dependency — so copying the notebook pattern without copying the job spec produces code that
looks complete and cannot run.

**Resolution.** `databricks jobs reset` with the environment block added; load succeeded, 48
signals reconciled against source.

**Lesson.** For serverless jobs, the dependency list lives in the **job**, not the notebook.
When cloning a working pipeline, clone `jobs get <id>` too — the half of the configuration
that is invisible from the source file is exactly the half that will be forgotten.

---

### I-052 — `agents.deploy()` leaves the previous version provisioned and billing
*Date:* 2026-09-02 · *Status:* resolved (and now a standing post-deploy check)

**Symptom.** After deploying agent version 2 over version 1, the endpoint showed **two**
served entities, both `DEPLOYMENT_READY`:

```
..._agent_1  version 1  Small CPU  scale_to_0: False  ← 0% traffic, still provisioned
..._agent_2  version 2  Small CPU  scale_to_0: False  ← 100% traffic
```

`traffic_config` was perfectly correct — 100/0 — so nothing looked wrong. Two containers were
running to serve one agent.

**Compounding factor.** `agents.deploy()` created both with **`scale_to_zero_enabled: False`**,
so idle containers bill continuously rather than only under load. The zero-traffic entity was
pure waste.

**Resolution.** `databricks serving-endpoints update-config` with only the surviving entity.

> **`update-config` REPLACES `served_entities`; it does not merge.** Copy the surviving
> entity's config verbatim from `serving-endpoints get` first — an `agents.deploy()` endpoint
> carries `ENABLE_MLFLOW_TRACING`, `MLFLOW_EXPERIMENT_ID`, `ENABLE_LANGCHAIN_STREAMING`,
> `RETURN_REQUEST_ID_IN_RESPONSE`. Dropping `MLFLOW_EXPERIMENT_ID` would leave the endpoint
> working while tracing silently wrote to the wrong place.

Endpoint re-verified after the change (25 vehicles / 22 depots / EXACT).

**Also worth recording:** cost could not be self-served. `system.billing` requires
`USE SCHEMA`, which a non-admin on a shared metastore does not have, and the public pricing
pages publish **GPU** serving DBU rates only — there is no CPU workload-size table. So "how
much is this costing?" is not answerable from inside this workspace.

**Lesson.** **Check `served_entities` after every redeploy, not `traffic_config`.** Routing is
the thing that looks wrong when something is wrong; provisioning is the thing that costs
money. They are reported separately and only one of them was being read.

---

### I-051 — `ARCHITECTURE.md` credited Model A with harm weighting it does not have — **SILENT**
*Date:* 2026-09-02 · *Status:* resolved (docs corrected to match the code)

**Symptom.** §5 of the living spec described Model A as *"volume anomaly against each series'
own trailing history, **plus harm weighting**"*, with a paragraph on smoothed severity
multipliers and shrinkage toward the component base rate. `STATUS.md` repeated it: *"volume-anomaly
detector with harm weighting — this is the final Model A"*.

**Root cause.** There is no harm term in the detector. The firing rule, identical across
`03_lead_time_backtest_v2` and `09_lead_time_backtest_v3`, is:

```
n >= 5  AND base_months >= 6  AND base_sd > 0  AND (n - base_mean)/base_sd >= 3.0
```

sustained over ≥2 consecutive months. A case-insensitive search of `src/` finds *no*
reference to harm outside `silver_complaint.sql` (typing the fields), `silver_complaint_chunk.sql`
(the `any_harm` retrieval flag) and the search/agent tooling. The harm-weighting design was
written in the proposal, described in the architecture, and **never implemented**.

The measured headline — **16.0% vs 11.1%, 1.44×, p ≈ 0.009** — is therefore produced by
*pure volume anomaly*. The number is unaffected and remains correct. What was wrong is the
description of what produced it.

**Why it survived.** It is a claim about *absence*, and absence has no failing test. Every
check this project runs asks "does the thing that exists behave correctly?" Nothing asks
"does the thing the document describes exist at all?" The harm paragraph was plausible,
internally consistent, and adjacent to real measured facts about harm-field population — all
of which are true, and none of which are about the detector.

**Resolution.**
- §5 now states the detector is volume anomaly only, and records harm weighting as
  *designed but not built*, with a pointer to where it would go.
- `STATUS.md` corrected in both places it repeated the claim.
- `gold_emerging_signal` (the live detector, built the same day) carries `harm_share` as an
  explicitly **descriptive** column with a table comment saying it is not an input to
  detection — so the next person to read it cannot make the same inference.

**Lesson.** A living spec drifts in a direction unit tests cannot see: it accumulates
*intentions* that read as *descriptions*. When a doc says the system does X, the check is
`grep` for X in the code, not "does that sound right". This is the third documentation claim
in this project falsified by looking (see the proposal's own contradiction table) and the
first found in the document that was supposed to be the corrective.

---

### I-050 — The deployed agent reported "no vehicles affected" for a 25-vehicle recall — **SILENT**
*Date:* 2026-09-02 · *Status:* **resolved and verified on the live endpoint**

**Symptom.** The freshly deployed agent endpoint was asked *"Which fleet vehicles does recall
17V629000 affect?"* and answered, confidently and in well-formed prose, that **no fleet
vehicles matched in either tier**. The ground truth, verified directly against
`gold_fleet_exposure` the same minute, is **25 vehicles across 22 depots, all EXACT**.

This is the worst output this system can produce. A recall assistant that says "you are not
affected" when you are is more dangerous than one that is simply offline, because the
operator acts on it and stops looking.

**How it was caught.** Not by the build. The notebook smoke test asserted only that
`propose_service_campaign` returned `PROPOSED_AWAITING_HUMAN_APPROVAL`, never that the count
was non-zero, so the whole log → validate → register → deploy chain passed green. It was
caught by querying the live endpoint by hand afterwards and disbelieving the answer.

**First hypothesis, falsified.** Cold-warehouse timeout: `wait_timeout="30s"` expires, the
statement is still `PENDING`, `stmt.result` is `None`, the tool returns `[]`. Testable — so
it was tested. The warehouse was warmed by a direct query and the endpoint was asked again:
**same empty answer**. Not a timeout.

**Root cause.** Two independent faults, and it took both:

1. **Undeclared resource.** `mlflow.pyfunc.log_model(resources=[...])` declared the LLM
   endpoint, the vector index and the SQL **warehouse** — but not the **table**. Automatic
   authentication passthrough grants the endpoint's credential exactly what is declared. The
   warehouse is the engine; `gold_fleet_exposure` is the data; they are *separate grants*.
   The agent could start a query it was not allowed to read.
2. **A status nobody checked.** `w.statement_execution.execute_statement()` **does not raise
   on failure.** It returns a response whose `status.state` is `FAILED` and whose `result` is
   `None`. The tool read `(stmt.result.data_array or []) if stmt.result else []` — turning a
   permission denial into an empty list, and an empty list into "no vehicles are affected".

Asked to reproduce the raw tool payload verbatim, the agent returned
`{"campaign_id": "17V629000", "by_match_tier": {}, ...}` with **no error field** — confirming
the failure never reached the model at all. The model was not hallucinating; it was
faithfully reporting a lie it had been handed.

**Resolution.**
- `_run_sql()` polls to a terminal state and **raises** on anything other than `SUCCEEDED`,
  carrying the warehouse's own error message. The tool loop turns that into an error the
  model can see and report honestly.
- `DatabricksTable(table_name=...)` added to `resources`.
- The smoke test now asserts the **ground-truth numbers** — 25 vehicles, 22 depots — not
  merely that the call returned. An assertion that only checks for absence of exception
  cannot catch a wrong answer.
- `by_match_tier == {}` now carries an explicit `no_vehicles_matched` flag, which is only
  meaningful *because* failure raises: an empty result is now a measured absence rather than
  an unnoticed error.

**Lesson.** This is the same shape as I-043 (a watcher exiting `0` while reporting
`ready=False`) and I-021 (`_rescued_data` of 0 not proving a clean parse): **an SDK call that
returns instead of raising will convert an infrastructure failure into a plausible business
answer.** Any tool an LLM can call must distinguish "I looked and found nothing" from "I
could not look" — the model has no way to tell them apart, and prose will paper over the
difference perfectly.

Corollary for the demo: every agent assertion must pin a number. "It ran" is not a test.

**Verification (version 2, live endpoint, 100% traffic).** The same question now returns
"**25 vehicles** across **22 depots** … all **EXACT** matches", states the tier unprompted,
declines to describe the remedy because no tool supplied it, and says it cannot launch the
campaign. A deliberately nonexistent campaign (`99V999000`) returns a *distinguishable*
answer: "I can confirm the lookup ran and returned zero matched vehicles" — an absence the
agent can now vouch for, which is the whole point of the fix.

---

### I-049 — **NEGATIVE RESULT.** Semantic subdivision does not improve lead-time detection
*Date:* 2026-09-01 · *Status:* resolved (hypothesis falsified, result published as-is)

The central Phase 9 hypothesis — that grouping complaints by *what they describe* rather
than by NHTSA's component code would surface defect ramps earlier — is **false on this data
with this detector**. Recorded here because it is the project's most consequential finding,
not despite being negative.

**The claim under test.** A component code like `SERVICE BRAKES, HYDRAULIC` is a broad
bucket; a single defect ramp is a fraction of its volume, so the rise is damped by
everything else in the code. A semantic sub-grouping shrinks the denominator, so the same
z-score detector should fire *earlier*. A signal-to-noise argument.

**Result** (`gold_lead_time_v3_summary`, both groupings recomputed on the identical
37-month working set):

| grouping | arm | detected | rate | median lead |
|---|---|---|---|---|
| v2 component | REAL | 103/777 | **13.3%** | 240 d |
| v2 component | PLACEBO | 65/606 | 10.7% | 360 d |
| v3 semantic | REAL | 87/777 | **11.2%** | 202 d |
| v3 semantic | PLACEBO | 54/606 | 8.9% | 273 d |

Lift **1.24× → 1.26×** — unchanged. Detection **fell** 13.3% → 11.2%.

**The paired view is what settles it:**

| arm | both | v2-only | v3-only | median lead gain (shared) |
|---|---|---|---|---|
| REAL | 70 | 33 | 17 | **0.0 days** |
| PLACEBO | 39 | 26 | 15 | 0.0 days |

Subdivision lost 33 detections and gained 17, and on the 70 investigations **both** arms
detect it produced **zero** additional lead time.

**That zero is the finding.** Had the mechanism worked and simply been outweighed by
fragmentation, the shared detections would still show a positive lead gain — smaller
denominator, earlier crossing. They show none. The mechanism did not operate at all. This
is not "needs tuning": subdivision fired on *fewer* things, not on the same things
*earlier*.

**Why the v2 row reads 13.3% and not the published 16.0%.** v3 runs on the 37-month
embedded working set, not all of `silver_complaint`, so v2 is **recomputed on that same
set** as the like-for-like comparator. Comparing v3 against the published number would have
credited the grouping change with a data-extent difference. The published 16.0% / 11.1% /
1.44× stands as the headline result — it is measured on the full silver corpus.

**What this costs and what it does not.** Phase 3's index is *not* wasted: hybrid retrieval
is verified and load-bearing for the agent's search tool (§4.3), which is a different use
of embeddings from clustering. What is retired is the claim that *semantic clustering
improves early detection*. The proposal must not assert it.

**Published position:** the differentiator is the **measured 16.0% vs 11.1% volume-anomaly
result with a control arm (1.44×, p≈0.009)** — modest, real, and falsifiable. §6 already
committed to publishing the floor honestly; this is that commitment being kept. A defended
1.44× with a placebo arm is worth more than an unfalsifiable larger claim.

**Cost of the negative result:** ~$5 of embeddings, one 85-minute HDBSCAN run, and roughly
half a session. Cheap for retiring the project's central open question 24 days before the
demo rather than discovering it during.

### I-048 — HDBSCAN labelled 85% of complaint embeddings as noise; hypothesis for it was wrong
*Date:* 2026-09-01 · *Status:* resolved (approach changed)

Global HDBSCAN over the 205,219 narrative embeddings returned **85.4% noise** — 175,310
complaints unclustered, 29,909 in 286 clusters. The semantic arm would have been detecting
on a 15% subset, and would still have produced a plausible-looking lead-time number. The
pre-committed diagnostics (noise rate, arm asymmetry, fragmentation) are the only reason
that number was never computed.

**My stated cause was wrong.** I attributed it to clustering *unnormalised* `gte-large-en`
vectors under `metric="euclidean"`, reasoning that magnitude tracks narrative length and
swamps semantic direction. A controlled sweep (`ops_hdbscan_sweep`, 8 configs on a 30k
sample, ~1 min each) falsified it:

| normalised | pca_dims | noise% | clusters |
|---|---|---|---|
| **false** | 50 | 85.8% | 64 |
| **true** | 50 | **85.4%** | 62 |

L2-normalisation changed noise by 0.4 points. Across the whole sweep noise never fell below
**75%**, and the configurations that reached it collapsed to 9–17 clusters with a
5,506-member blob — low noise bought by having no discriminating power.

**Actual conclusion, and it is about the data:** complaint-narrative embeddings do not form
well-separated density peaks. Complaints about one component are a *continuum* of phrasings,
not islands. No HDBSCAN parameterisation fixes that.

**Approach changed — the tool was wrong for the question.** HDBSCAN answers "where are the
natural density clusters, and what is noise?" The actual requirement was a **grouping key
finer than the component code**, i.e. a partition. HDBSCAN's noise label is not neutral
here: it discards 85% of the evidence, which is strictly worse than the v2 grouping it was
meant to improve on. Replaced with k-means **subdivision within each existing series**
(`08_semantic_subdivision.py`), which assigns every complaint and produces a strict
refinement of v2 — every v3 series sits inside exactly one v2 series.

**The measured constraint that shaped the replacement:** v2 has 2,763 series with a
**median of 10 complaints** over 24 months (mean 74.3 — heavily skewed) and **11,493**
months reaching `MIN_COUNT = 5`. Subdividing a 10-complaint series guarantees it can never
fire again, so subdivision applies only to series with ≥100 complaints. That is not
cherry-picking: such series never fired under v2 either, and the rule is a pure function of
**complaint volume, never of arm**, so it cannot advantage the real arm over its control.

**Process lessons:**
1. **Do not tune at 85 minutes per attempt.** The first full run cost 85 min; the sweep
   answered the same question in ~1 min per configuration on a 30k sample. Sample-first,
   full-run-once.
2. **Include the failing baseline in the sweep.** Keeping the unnormalised config in the
   grid is what falsified the hypothesis; without it, normalising *and* re-tuning together
   would have produced a change with no attributable cause.
3. Sample-size sensitivity is real — HDBSCAN density estimates shift with `n`, so a sweep
   chooses a *region*, not a prediction.

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

> **➤ Superseded in part by [I-049].** The paragraph above was a *prediction*, and the
> harness it describes went on to falsify it: the semantic arm lowered detection and added
> zero lead time. This entry is left unedited — it is an append-only record of what was
> concluded on 2026-08-31, and the prediction being wrong is precisely what makes running
> the measurement worthwhile. **The 16.0% / 11.1% / 1.44× figures above remain current.**

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
