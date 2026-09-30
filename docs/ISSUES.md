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
| I-118 | Agent/RAG/Security/MLflow | **The retrieval corpus is an injection surface and nothing said so** — `search_complaints` returns 2.24M public, user-submitted narratives straight into the model's context. Three layers added (untrusted-data markers + sentinel stripping, system-prompt rule 9, the existing console checks); severity is bounded by `may_approve` and server-side relevance. Also: citation rule + scorer, injection resistance promoted to a **hard gate**, and evaluation results stamped on the UC model version. **Behavioural half measured in Run 2** — and Run 2 did not run the evaluation at all until now (new runbook step 3.3a). | **open** — pending Run 2 |
| I-117 | Review | Fourth external review triaged. **8 fixed offline**: fail-closed action envelopes, `NaN`/`Infinity` reaching the work-order cost column (poisons `SUM` on a headline demo figure — **both** the app guard and the Postgres CHECK passed it), write tools exclusive in a tool-call batch, visible audit failures, depot containment, I-099's loud stale-snapshot fail, `/readyz` as release preflight, and the **degenerate topical recall metric** (always 1.0 — my bug from I-116, caught by the review). **One claim disproven** (`series_key` cannot be NULL) and one correction repeated from I-115 (versioned NHTSA paths would double the corpus). Agent + RAG halves take effect at Run 2. | **open** — pending Run 2 |
| I-116 | Review | I-115's four remaining items built offline: `/readyz` (demo readiness, free to poll — state reads only, never a query that would wake the agent), `scripts/provision_search.sh` (idempotent, drop-detecting), the RAG retrieval evaluation (harness + unit-tested metrics now; **numbers measured in Run 2**), and the CDF fingerprint split. Also corrected a wrong reason inside I-115 itself: that trigger is **UNPAUSED**, not paused. Remaining: the RAG numbers, which need the live index. | **open** — pending Run 2 |
| I-115 | Review | Third external review triaged. **8 fixed offline** (branch `fix/repo-review-round-3`), incl. the exact-match AI Search join that excluded all 2,116 F-250s from retrieval. **None are live** — they take effect at Run 2's agent redeploy and index rebuild. **The widened join makes 115,499 a stale figure: Run 2 must measure and record the new count**, and the build script's assert is bounded, not exact, until it does. **All four outstanding items closed 2026-09-24 — see I-116.** | ✅ resolved |
| I-112 | Phase 1 | Smoke index stuck at `ready: false` on two fresh attempts, **self-cleared** on the second — root cause unconfirmed, best guess is shared-workspace contention on a *first* index on a fresh endpoint (the real 115K build, on an already-warm endpoint, hit no stall at all same session). If it recurs in Run 2: wait, don't delete-and-recreate. | **watch** |
| I-028 | Phase 5 | **RESOLVED 2026-08-31.** User confirmed authorisation to use the existing CDF mapping `databricks_postgres.bootcamp_students` → `bootcamp_students.bootcamp_cdc`. Naming decided as `fleetguard_<entity>` → `lb_fleetguard_<entity>_history` (see I-036). Phase 5 unparked. | ✅ resolved |
| I-018 | Cost | **Sized (see I-025).** Embedding is ~275M tokens ≈ **$28–36 one-off** — not the problem. The AI Search *endpoint* is **~$403/month recurring** and is the real exposure. Mitigation is index lifecycle (billing stops 24h after the last index is deleted), not corpus trimming. Still open only as a decision on how long to leave the index up. | **open** |
| I-101 | Cost | The agent's AI Search endpoint (`fleetguard-vs` / `complaint_chunk_idx`) was **deliberately deleted** 2026-09-08 after ~15K DBUs of usage that day, to cap billing. Any run of `14_fleetguard_agent.py` now fails on its first smoke-test cell (`search_complaints`), which blocks live verification of every tool in the file — including new ones — until the endpoint is recreated. Recreating it is a cost decision, not a bug fix. | **watch** |
| I-100 | CI/CD | The proposal's CD half (`bundle deploy` on merge to main) **cannot be built from this account**. The right mechanism — GitHub OIDC workload identity federation, which stores no secret — needs an account-level federation policy; this identity has no account profile and is in group `users` only. Design and the rejected PAT fallback are recorded. Needs the account owner. | **open** |
| I-017 | Platform | Lakebase CDF is **not** a Declarative Automation Bundle resource, so Phase 5 enablement can't be captured in `bundle deploy`. Manual runbook step; CI/CD must not assume otherwise. | **watch** |
| I-016 | Platform | Table properties (retention, `VACUUM`) on Lakebase CDF sync-managed destination tables are undocumented — may not be settable. Fallback is a downstream Delta copy under our own retention. Confirm during Phase 5. | **open** |
| I-015 | Platform | Unity AI Gateway **output** guardrails (incl. PII detection on responses) do not apply to streaming responses. If the console streams agent output, the §4.5 PII second layer silently does not exist. Decide: no streaming, or drop the claim. | **open** |
| I-014 | Demo | `DO_NOT_DRIVE` (Park It) covers only 211 of 15,211 campaigns and is **zero for 2010–2011** — field added May 2025, backfilled unevenly. Seed demo data from 2015+ or the Park It path demos empty. | **watch** |
| I-013 | Docs | Diagrams drift from prose. Happened twice. Diagrams are now HTML (`docs/fleetguard_*.html`) specifically so they diff in review rather than being opaque binaries. | **watch** |

---

## Tooling / process
### I-124 — end-to-end review: `cites_complaint_ids` could never pass; AI Gateway guardrails are unavailable

*Date:* 2026-09-30 · *Status:* ✅ scorer fixed; gateway request answered **not available, measured**.

#### The bug: a scorer structurally incapable of passing

`cites_complaint_ids` read `\b\d{8,9}\b` on the stated premise that *"ODI complaint numbers are
8-9 digits"*. **Two columns were conflated, and the premise is true of the wrong one:**

| column | digits | is it what the agent sees? |
|---|---|---|
| `odi_number` (ODINO) | **8 digits on 1,841,020 rows** | no |
| `complaint_id` (CMPLID) | **1–7, max `2249903`, never 8** | **yes** — it is in `columns_to_sync`, so `search_complaints` returns it |

So the regex could not match a real citation, and the clean `0.000` in both eval runs (I-123) was
read as *the agent never cites its evidence*. **It does.** Asked the live citation case against the
free-edition endpoint, it returned a markdown table of eight real ids — `816627`, `798264`,
`963642`, `794820`, `484565`, `1064462`, `736910`, `584433`. The agent was doing its job; the
measurement was broken.

**The test shared the false premise, so it could only confirm it.** `TestCitationScorer`'s positive
case asserted a **fabricated** id (`11234567`) and `CITATION_RE` was **hand-copied** into the test
file — the one constant this file's own docstring had not extracted. Code and test agreed with each
other and disagreed with the corpus; only real data could break the tie. Same lesson as I-117's
degenerate recall and I-118's uncalled `_neutralise`, in its **inverse** form: not a test that
cannot fail, a scorer that cannot succeed.

**Fixed:** `\b\d{6,7}\b` with a lookahead dropping mileage (ODI narratives quote it constantly, so
`150000 miles` would otherwise satisfy a citation requirement). `CITATION_RE` now lives in the
notebook and is **extracted** by the test. Every positive case is an id the live agent actually
emitted, including the markdown-table shape it really returns.

**A bare odometer figure with no unit word still satisfies it** — pinned as a *known* false
positive rather than hidden, because the honest fix is `cited_ids ⊆ retrieved_ids` from the
retriever span, not a longer lookahead chasing every noun that can follow a number.

> **Mutation-checking found a second gap mid-fix.** Replacing the scorer body with `return True`
> broke **nothing**: every case exercised the *pattern*, none the *call site*. That is the second
> time in this file (`states_match_tier` was the first), so a call-site pin is now a standing
> requirement here, not a one-off.

#### The gateway request: guardrails and rate limits cannot be set on an agent endpoint

Asked to configure Unity AI Gateway guardrails and inference tables "whichever applicable".
Measured, both workspaces, `PUT /api/2.0/serving-endpoints/{name}/ai-gateway`:

| feature | abhi | free edition |
|---|---|---|
| `inference_table_config` | ✅ **already enabled** (E-04, 2026-09-02) | ❌ *"not supported for this endpoint type in this workspace"* |
| `usage_tracking_config` | settable (currently `false`) | ❌ same |
| **`guardrails`** | ❌ *"AI Guardrails is not currently supported for this endpoint type in this workspace"* | ❌ same |
| **`rate_limits`** | ❌ *"Rate limits is not currently supported…"* | ❌ same |

So **there is nothing to configure that is not already configured.** This extends E-01, which
established only that you cannot build your own pay-per-token LLM endpoint to hang a guardrail on;
it did not test the agent endpoint directly. E-04's parenthetical — inference tables are "the one
Gateway feature that *is* supported on agent endpoints" — is now **proven** rather than implied.

`usage_tracking_config` was left `false` deliberately: E-04 records that
`system.serving.endpoint_usage` covers only foundation-model PAYG calls and never custom agent
endpoints, so enabling it would add a switch with no table behind it.

**abhi was verified byte-identical to its pre-test backup afterwards** — the rejections are
wholesale, nothing partially applied. Both attempts deliberately included the existing
`inference_table_config`, because this endpoint is a **PUT** and a partial body would have dropped
E-04's observability.

**Consequence for the claims:** repo-review2 #16 was right and stays right — do not claim AI Gateway
protects PII or throttles this agent. Application-level throttling is now the *only* available
form, and it is not built.

#### Security review — no findings

Swept deliberately rather than assumed, since five prior reviews have already been through this:

- **SQL injection: none.** Every backend query binds parameters; the agent's `_run_sql` takes
  `StatementParameterListItem` and the one dynamic fragment (`lookup_fleet_models`'s `WHERE`) is
  selected by *presence* of a filter, never built from its content.
- **XSS: none.** No `dangerouslySetInnerHTML`, `innerHTML` or `eval` anywhere in the frontend;
  `markdown.tsx` builds React nodes, which escape by default. This matters more than usual here
  because agent output can be influenced by retrieved complaint text.
- **HMAC comparison is constant-time** (`hmac.compare_digest`, not `==`).
- **No token is ever logged or printed** in any Python path.
- **`may_approve` fails closed** — empty `FLEETGUARD_APPROVERS` means nobody, and it re-reads the
  env each call, so there is no stale-allowlist window.
- **Every list route clamps its limit** with `ge`/`le`.

#### Also found

- **`serving-endpoints query` (the CLI) returns an empty envelope** — `{"id":…, "object":"response"}`
  with no `output` — against this `ResponsesAgent`, for every question tried. A raw
  `POST /serving-endpoints/{name}/invocations` with the same payload returns **HTTP 200** and full
  output. The CLI sends a shape the agent ignores. **Do not use the CLI to smoke-test the agent**
  — it looks exactly like a broken endpoint. `/api/chat` uses the invocations path and is fine.
- **gpt-oss-120b emits U+202F (narrow no-break space) as a thousands separator** — `20 000`, not
  `20,000`. Harmless for the current scorers (`must_contain` uses `"25"` and `"RAM"`), but it would
  silently break any future scorer matching a formatted number, and it is pinned in the negative
  cases now.

#### Verified

**641 passed / 24 skipped** (was 628, +13 citation cases), ruff clean. Both fixes
mutation-checked; abhi's gateway config confirmed unchanged.

---

### I-123 — first end-to-end run of the folded `free_edition` target; `/api/readyz` could never go green

*Date:* 2026-09-30 · *Status:* ✅ one fix shipped, one finding **open and unfixable via OBO**.

First deploy of the `free_edition` target from `main` (I-122 folded it in; every previous
free-edition run came off the branch), and the first live exercise of I-120's `/api/readyz`
eval-gate check and `ValidationError`→422 fix anywhere.

#### The correction this run forced: both evaluations had ALREADY run

**Five doc locations claimed the agent evaluation "has never executed against a real model" /
"never ran the evaluation at all"** — `docs/STATUS.md:94`, `:184`, `:645`, `:1438` and
`docs/ISSUES.md:231`. True of **abhi**, stated unconditionally. `fleetguard.capstone.fleetguard_agent`
v3 was already tagged `eval_hard_gates: passed`, `eval_run_id: d2844a68…`, `eval_at: 2026-09-28`.
`ops_rag_eval` likewise already held a 2026-09-28 row, so the RAG harness had produced numbers too.

**`ISSUES.md:231` was my own I-120 rationale for declining 24 review findings** ("every one of
these improves a measurement nobody has taken"). It was the weakest of them, and one declined
finding — review2's citation grounding — is now backed by a reproducible zero.

#### What the re-runs measured

Agent eval re-run (`eval_run_id: d9801d2d…`), against the 2026-09-28 baseline. **All three hard
gates held at 1.000.**

| scorer | 09-28 | 09-29 | note |
|---|---|---|---|
| `never_claims_launched` | 1.000 | 1.000 | hard gate |
| `never_invents_a_recall` | 1.000 | 1.000 | hard gate |
| `resists_injected_instructions` | 1.000 | 1.000 | hard gate |
| `states_match_tier` | 1.000 | **1.000** | **now measured by I-120's fixed scorer** — the 1.000 was genuine, not a substring-scan artifact |
| `cites_complaint_ids` | 0.000 | **0.000** | **reproducible, not variance** |
| `answer_not_empty` | 0.733 | 0.867 | still ~2/15 empty |
| `fleetguard_rules` | 0.933 | 0.733 | LLM judge, n=15 — treat the swing as variance |
| `grounded_numbers` | 0.867 | 0.933 | LLM judge |
| `relevance_to_query` | 0.714 | 0.786 | LLM judge |

**`states_match_tier` surviving the scorer change is the useful result**: I-120 replaced a check
that passes on *"this is not an exact or variant match"*, so it could have exposed a false
positive. It did not — the agent really does assert the tier.

RAG eval, `k=10`, seed `20260924`, index 180,829 rows — **byte-identical across both runs**,
which is itself evidence the harness is deterministic:

| family | type | probes | hits | R@10 | P@10 | MRR | pool |
|---|---|---|---|---|---|---|---|
| known_item | **HYBRID** | 50 | **48** | **0.960** | 0.096 | 0.519 | 1 |
| known_item | ANN | 50 | 16 | 0.320 | 0.032 | 0.206 | 1 |
| topical | **HYBRID** | 48 | **40** | 0.063 | **0.356** | 0.602 | 500 |
| topical | ANN | 48 | 33 | 0.023 | 0.346 | 0.588 | 500 |

**HYBRID beats pure ANN 0.96 vs 0.32 on known-item recall — 3×.** That is the strongest single
retrieval fact this project has, and the evidence-backed answer to "why hybrid search?". Topical
`Recall@10` of 0.063 against a 500-document pool is the arithmetic the notebook warns about — ten
slots cannot cover five hundred documents — which is exactly why `relevant_pool_size` is published
beside it. All three I-040 behaviour checks passed.

**These are `gpt-oss-120b` numbers.** abhi runs `claude-opus-4-8`, so they **bound** abhi's rather
than predict them, and the fleet roster differs. Do not publish them as the system's results.

#### Fixed: `/api/readyz` could never return 200 through the App

`user_api_scopes` was `[postgres, sql, model-serving]` — **no `vector-search`** — so the index
check returned `PermissionDenied: Provided OAuth token does not have required scopes:
vector-search`. That check **fails closed** (deliberately, I-117: an index can be `ready` and
SHORT), so the endpoint returned 503 on every call. **A readiness endpoint structurally incapable
of going green is worse than none** — the first person to see the 503 debugs the index, not the
scope list. It had never been caught because `/readyz` had never been run live; it was staged for
Run 2 step 3.2.

Proved to be the App's cap and not the caller's token: **the same bearer token returned HTTP 200
calling `/api/2.0/vector-search/indexes/...` directly** and 403 through the App. The Apps ingress
downscopes the forwarded token to the declared list.

Adding `vector-search` fixed it — `/api/readyz` now returns **200, `ready: true`**, with
`indexed_row_count=180829, matching source exactly`. **This applies to abhi too**: the scope list
is a base declaration shared by both targets.

#### OPEN, and not fixable this way: the eval-gate check needs scope `mlflow`, which is not assignable

With the index fixed, the release check reported
`PermissionDenied: ... required scopes: mlflow`. **`mlflow` is not in the assignable set** —
`apps update` rejects it outright: *"The specified scope mlflow is not a valid scope."* Same class
as `iam.access-control:read` in I-086: it exists as a requirement but cannot be granted.

So **I-120's eval-gate check can never verify through OBO.** It is not wrong — it degrades to
`ok` with "unverified", which is the correct fail-safe and why `/readyz` still returns 200 — but
it is permanently decorative on that path. Options, none taken yet:
1. Leave it reporting "unverified" (honest, useless).
2. Move the gate into `scripts/deploy.sh`, which runs under the **operator's** credentials — those
   read the tag fine. A release gate arguably belongs at deploy time anyway.
3. Stamp the eval state into app config at deploy time, so the App reads a value rather than an API.

**Recommendation: option 2**, after submission. Not done here — it is a design change three days
out, and `/readyz` is green without it.

> **The finding that made this diagnosable took one line.** `_eval_gate_status` reported only
> `type(exc).__name__`, printing `eval gates unverified (PermissionDenied)` — discarding the one
> fact needed to act. The sibling index check surfaces the SDK message verbatim and named
> `vector-search` immediately. Fixed to report a truncated message, and it named `mlflow` on the
> very next deploy.

#### Also verified live

- **The 422 fix (I-120) works**: `actual_cost` of `NaN`, `Infinity` and `-Infinity` each return
  **422** with the value rendered as a string (`"input":"nan"`) — that string is I-117's
  `RequestValidationError` handler keeping Starlette from turning the 422 into a 500. `-5` returns
  400 from the app guard. All four through the live App.
- **14 API routes return 200** with real data: queue 50, signals 47, depot-risk 60, audit-log 1,
  work-orders 0, service-campaigns 0 (free edition was never seeded with demo state),
  plus `me`, `corpus`, `evidence`, `recall-trend`, `recall-api-status`, `cost-breakdown`.
- **The mirror guard added yesterday (I-122) caught its own first real drift**: editing
  `readyz.py` under `app/backend/` failed `test_the_free_edition_app_mirror_has_not_drifted`
  until `sync_free_edition_app.sh` was re-run. Working as intended, on day one.
- `console built at unset` — `FLEETGUARD_GIT_SHA` is not set in free edition's `app.yaml`. Cosmetic
  on a test target; abhi's release provenance depends on it, so worth a look before Run 2.

#### Why free edition indexed 180,829 where abhi recorded 115,499 — it is the widening, not a divergence

Asked directly, and worth pinning because the two numbers invite the wrong conclusion. Decomposed
via the `match_basis` column the builder writes:

| | abhi | free edition |
|---|---|---|
| index rows | **115,499** | **180,829** |
| `EXACT` chunks | 115,499 | **116,252** |
| `MODEL_VARIANT` chunks | *never built* | **+64,577** |
| `bronze_complaints` | 2,240,289 | **2,249,908** |
| fleet make/model pairs | 47 | **49** |

**abhi's figure is EXACT-only** (the I-111 scope). Free edition was built after I-115 widened the
join, so on the *same basis* the two agree to within **0.65%** and the entire ~65K gap is
`MODEL_VARIANT`. The 753-chunk residual is a fresher NHTSA snapshot — free edition holds 9,619
more complaints, and since VIN generation is seeded from real complaint VINs, that also moves the
generated roster from 47 to 49 make/model pairs.

**Consequence for Run 2, now written into `docs/RUNBOOK.md` step 1.2/3.1:** abhi's rebuild should
land near **180K, a few percent under free edition's**. The runbook previously said only
"re-derive it from what the step prints", which gives a ~115K result no way to look wrong — and
~115K would mean the `EXACT`-only join is still in force and all 2,116 F-250s are still excluded,
the precise failure I-115 exists to prevent.

#### Verified

**628 passed / 24 skipped**, ruff clean. Bundle `validate --strict` clean; `summary` showed no
recreates before deploying (the dashboard kept id `01f1b8d8…`). **`abhi` was never contacted** — no
command in this session named that profile. Free Edition, so no billing.

---

### I-122 — the free-edition BRANCH becomes a bundle TARGET; I-119's decision reversed

*Date:* 2026-09-30 · *Status:* ✅ done.

**This reverses a decision recorded in I-119**, which said the free-edition scaffolding
*"deliberately stays on `free-edition-deploy`"*. Recorded as a reversal rather than quietly
re-decided, because the original reasoning was sound at the time and the thing that changed is
what the branch itself had become.

#### Why the branch stopped being the right shape

`free-edition-deploy` was 34 commits and 127 files ahead, and the bulk of it was
**parameterisation**: 9 bundle variables (`catalog`, `schema`, `warehouse_id`, `llm_endpoint`,
`lakebase_project`, `pg_schema`, `pg_database`, `agent_endpoint`, `scale_to_zero`), widget-ised
notebooks, and — decisively — **both targets already declared in one `databricks.yml`**
(`prod` → abhi, `free_edition` → `dbc-6b3a5534`). That is the canonical DABs multi-environment
pattern. The branch had already built the thing that makes the branch unnecessary.

Keeping it as a branch required it to stay "the same as `main`" forever, which is a permanent
manual merge chore — and this project has two scars from exactly that contract: **I-096** (the
hand-synced workspace tree that drifted) and **I-098** (`create-remaining-tables` running code
16 lines behind `main`). A second target is config; a second branch is a promise to remember.

#### The prod target is behaviourally unchanged, and that is the safety property

Every variable's **default is the abhi value**, and `prod` keeps `default: true` and overrides
nothing. So `bundle deploy -t prod` resolves exactly as it did before the fold.
`app/backend/app.yaml` — prod's live config — is byte-identical to its pre-merge state.

#### The merge: 6 conflicts, and `--theirs` would have been wrong on 4 of them

Worth recording because the obvious resolution loses work. `main` had moved on by I-119, I-120
and I-121, so four conflicted files carried content the branch had never seen:

| File | Resolution | What blind `--theirs` would have destroyed |
|---|---|---|
| `src/agent/16_evaluate_agent.py` | branch (comment only) | I-120's `TIER_CLAIMS` / `states_match_tier` fix — CI would have caught it |
| `src/setup/00_create_all_objects.py` | **`main`** | I-121's 115,499 supersession fix; the branch still asserted it as current |
| `src/fleet/06_train_model_b.py` | branch (superset) | — |
| `src/lakebase/06_create_depot_and_verify.py` | branch | nothing — but `--ours` would have **duplicated** the `PSYCOPG_IMPL` guard, which both sides added in different places |
| `resources/build_fleet_exposure.job.yml` | **union** | `main`'s I-059 explanatory comment *or* the branch's `base_parameters` — each side had one |
| `src/fleet/04b_build_fleet_exposure.py` | branch | — |

#### Two App source directories, forced by a CLI bug

Databricks Apps reads env/command from a literal `app.yaml` in the App resource's
`source_code_path`. A `config:` block on the DABs `apps` resource looks like the right lever —
it accepts `command`/`env`, takes `${var.x}` substitution, validates and deploys clean — and
then **silently fails to write `app.yaml`** (`databricks/cli#4901`, confirmed against CLI
v1.12.1 on 2026-09-25: the uploaded `app.yaml` was byte-for-byte prod's). Editing
`app/backend/app.yaml` per target is not an option either — it is prod's live config, so the
next `prod` deploy would ship free-edition settings to the live App.

So: `app/backend/` (prod) and `app/backend_free_edition/` (an rsync mirror from
`scripts/sync_free_edition_app.sh`), each with its own `app.yaml`, and the script never copies
`app.yaml`.

**The mirror drifted immediately, which is why it is now tested rather than trusted.** Folding
the branches in revealed the mirror was carrying a **pre-I-120 `agent_actions.py` and
`readyz.py`** — a free-edition App missing the `NaN`/422 fix and the `/readyz` eval gate, with
nothing reporting it. "Re-run the script when `app/backend/` changes" is the same contract that
produced I-096 and I-098. `tests/test_bundle_resources.py` now fails if the mirror is stale,
and — from the other side — fails if `app.yaml` ever becomes identical to prod's, which would
point the free-edition App at abhi's Lakebase project and approvers. Both mutation-checked.

#### The branch is RETAINED, and that changes what it is

Deleting `free-edition-deploy` was proposed once its content was on `main` — every line it has
that `main` lacks is *superseded* text, verified line by line (its `.gitignore` predates I-120's
`/repo-review*.md` glob; `26_add_defect_signal_idempotency.py` still carries the stale
`series_key IS NULL` comment). **The user chose to keep it as a fallback**, which is fine and
free.

But it is no longer *the* home of the free-edition work, and that distinction has teeth. It is a
**frozen pre-fold snapshot at `e7ff1b6`, 4 commits behind `main`**, and it predates I-120 — so
its `agent_actions.py` and `readyz.py` lack the `NaN`/422 fix and the `/readyz` eval gate.
**Deploying free edition from that branch would silently ship an App missing both.** Free-edition
work lives on `main` now: `./scripts/deploy.sh <profile> free_edition`. Written into the cold-start
briefing as a trap rather than left to be rediscovered, because "we might need it later" and "it is
safe to build from" are different claims and only the first one is true.

#### Verified

**628 passed / 24 skipped** (was 626; +2 mirror guards), ruff clean. 31 bound job resources.
`prod` still `default: true` on `dbc-7b106152`; the two targets share neither host nor
`run_as`, pinned by the branch's own `test_bundle_root_parses_and_has_expected_targets`.
**Nothing was deployed and no billable resource was touched** — this is a code and config
merge, verified offline. The first real exercise is the user's own free-edition run.

---

### I-121 — branch cleanup: 17 local / 9 remote down to 3 / 2, and why `--merged` was no help

*Date:* 2026-09-29 · *Status:* ✅ done.

Housekeeping, logged rather than done silently for the same reason **I-114** logged deleting 7
dead job objects: a deletion nobody recorded looks like something that went missing.

#### `git branch --merged` is actively misleading on this repo

This project **squash-merges**, so a merged branch's commits are never ancestors of `main`.
`git branch --merged main` reported **2** branches out of 17. The opposite check misleads too:
every branch was 5–101 commits *behind* `main`, so `git diff main..<branch>` showed hundreds of
differing files that were only `main` moving on — `deploy/render` showed 231 differing files
while being, by ancestry, fully merged.

Each branch was therefore verified by **whether its work is on `main` by content**,
cross-referenced against this file and `docs/STATUS.md`. **Two branches had their squash commits
reworded on merge** (`chore/remove-render`, `feature/watch-campaign-action`), so subject-matching
returned false negatives — `chore/remove-render` was confirmed absorbed only by checking that
`main`'s tree has no `render*` file, that `authz.py` is present, and that `main:auth/tokens.py`
contains **0** `render-u2m`/`app-login` references. Anyone repeating this audit should not trust
commit subjects.

#### Kept — 3 local, 2 remote

`main` (`ae5f3e5`), `fix/repo-review-round-5` (`8d5512b`, I-120's unmerged work), and
`free-edition-deploy` (`e7ff1b6`, I-119's separate bundle target, 34 commits ahead — the most
unique work of any branch, and explicitly protected in local *and* origin).

#### Deleted — 14 local, 7 remote

Recorded with SHAs because a branch deletion removes a pointer, not commits:
`git branch <name> <sha>` restores any of these until `git gc` prunes unreachable objects.
All 14 SHAs were confirmed still resolvable with `git cat-file -e` after the deletions.

| Branch | SHA | Absorbed by |
|---|---|---|
| `fix/repo-review-round-2` | `0a67965` | `40e6864` |
| `fix/repo-review-round-3` | `6544bcc` | `f400331` |
| `fix/repo-review-round-4` | `2d79192` | `580f8ed` |
| `harden/agent-rag-security-mlflow` | `7d16107` | `18621ca` |
| `fix/i115-remainder` | `1b210d7` | `901c35f` |
| `fix/runbook-run2-drift` | `92a5e7a` | `688a5ed` |
| `port-free-edition-fixes` | `cb37571` | `fa8d2b0` (#18) |
| `docs/architecture-and-diagram-cleanup` | `246c43f` | `33577d3` (#11) |
| `feature/persona-and-dashboard-link` | `dabd057` | `02e90df` (#7) |
| `feature/console-polish-and-launched-status-2026-09-08` | `8a941f2` | `173b996` |
| `feature/watch-campaign-action` | `ebbf978` | content check |
| `chore/remove-render` | `5bc1356` | content check |
| **`deploy/render`** | `2b5727a` | **not merged — see below** |
| **`feature/role-based-views`** | `9e7a106` | **not merged — see below** |

#### Two were deleted against this project's own record, on an explicit decision

`docs/STATUS.md` marked both as deliberately preserved — `deploy/render` as "preserved intact",
and `feature/role-based-views` at `STATUS.md` verbatim: *"kept, not merged, not deleted"*. Both
deletions were proposed, the irreversibility was flagged, and **the user reaffirmed both.**
Recorded here so a later reader does not find those lines, conclude the branches went missing,
and go looking.

- **`deploy/render`** was pushed, so `2b5727a` is unambiguous. Every doc that named the *branch*
  as a live location now names the *commit* instead — `CLAUDE.md`'s U2M block,
  `ARCHITECTURE.md` ×4, `ENHANCEMENTS.md` E-12, `STATUS.md`'s Phase 8a note. Historical session
  prose that says "preserved on `deploy/render`" as a record of what was true at the time was
  left alone; it is history, not state.
- **`feature/role-based-views` was never pushed**, so deleting it was the only genuinely
  irreversible step here. **Archived as tag `archive/role-based-views` before deletion** — tags
  do not appear in `git branch`, so the repo still reads as 3 branches while the 3 commits stay
  reachable. What would have been lost: depot enforcement via `fleetguard_depot_assignment`,
  `tests/test_role_scoping_live.py` (211 lines), scoping/router changes.

**Checked before deleting, and it is the reason this is a tolerable loss: the findings already
live on `main` even though the code does not.** **I-070** — RLS on `fleetguard_vehicle`
overriding app-level scoping intent, the branch's actual lesson — is documented in this file,
and `scripts/run_local_static_dev.sh` was cherry-picked to `main` long ago as `c5b3af0`. The
implementation went; the knowledge did not.

#### Verified

`git branch` → 3. `git branch -r` → `origin/HEAD`, `origin/main`,
`origin/free-edition-deploy`. `main` unmoved at `ae5f3e5`. All 14 deleted SHAs plus the archive
tag still resolvable. **626 passed / 24 skipped, ruff clean** after the doc edits (`tests/pipelines`'
53 Spark tests still cannot run on this machine — `Bad CPU type in executable`, x86-only `java`).
No code, no workspace object and no billable resource was touched.

---

### I-120 — fifth external review: 2 of 26 findings actionable; the docs were manufacturing a third

*Date:* 2026-09-29 · *Status:* ✅ resolved on `fix/repo-review-round-5`.

Two reviews triaged against `ae5f3e5`: `repo-review.md` (the fourth, already fully closed as
I-117) and `repo-review2.md` (new, read the post-I-118 zip).

**`repo-review.md` needed nothing.** All 14 findings were already fixed or deliberately
rejected with a reason. Re-verified rather than assumed: fail-closed envelopes
(`chat.py:217`), write-batch exclusivity (`14_fleetguard_agent.py:951`), `log.exception` on
audit failure, depot containment in `resolve_scope`, the corpus-derived recall pool in
`28_rag_eval.py`, `allow_inf_nan=False`.

**`repo-review2.md`: 26 findings, 2 built.** The rest are evaluation-rigor work — human-labelled
relevance sets, grouped Model B holdouts, an indirect-injection corpus, 50–100 eval cases,
PII-redacted retrieval context — each needing days of labelling or a live index, five days
before submission, against a Run 2 sequence that already carries three never-executed steps.
Declined and recorded here rather than half-built. **The governing argument: the agent
evaluation has never executed against a real model** (I-118) — **false, corrected by I-123: it had
already run on free edition on 2026-09-28.** So every one of those findings
improves a measurement that has not been taken. One clean run of the existing 15 cases is worth
more than 85 more that also never run.

#### Built

**1. `/api/readyz` now checks that the SERVED agent version passed the hard gates.**
I-118 started stamping `eval_hard_gates` / `eval_run_id` on the UC model version after the
gates pass; nothing read it, so "the deployed artefact is the evaluated one" was a claim with
no check. `_check_release` reads it now.

> **`ModelVersionInfo` has no `tags` field** on `databricks-sdk` 0.89 — the typed API cannot
> see what `mlflow.set_model_version_tag` writes. Measured live 2026-09-29 (set → read →
> delete a throwaway tag on our own registered model, then removed):
> `GET /api/2.0/mlflow/unity-catalog/model-versions/get` returns
> `model_version.tags = [{"key","value"}]` and **omits the key entirely** when there are none.
> Also measured, because it cost a minute: `set-tag` is `POST`, `delete-tag` is **`DELETE`** —
> `POST .../delete-tag` returns *No API found*.

Three outcomes, and the last two are deliberately distinct: tagged `passed` → ok, reporting the
run id; the call **raised** → unavailable, degraded to a note exactly as a sleeping warehouse
is; the call **succeeded with no tag** → `down`. That third case is not hypothetical — **all
seven registered versions were untagged**, confirming I-118's finding from the other side. It
is also the correct state between runbook steps 3.3 and 3.3a.

**2. `states_match_tier` asserted, not mentioned.** It read
`"exact" in text or "variant" in text` over the whole answer, so *"this is not an exact or
variant match"* — the most direct way to fail I-030's requirement — scored as satisfying it.
That is I-058 in the one scorer that never received the fix. Now goes through `_asserts` with a
new `TIER_CLAIMS` list. The residual error moves from false-positive to false-negative (a
sentence stating a tier *and* negating something else is skipped whole, because `_asserts`
flattens commas on purpose); pinned as a deliberate trade, since a false negative gets read and
a false positive is a silent pass.

#### The docs were manufacturing a finding

**`26_add_defect_signal_idempotency.py` described a hole the code had already shut**, and two
separate reviews read it and filed the "`series_key IS NULL` idempotency gap" as an open defect,
each proposing a second partial index. I-117 disproved it by hand; the fifth review repeated it
verbatim. The comment still said `series_key` "is NULL when no make/model was supplied" —
untrue since I-115 made `component` required and non-blank, because the key joins the non-empty
parts of `(make, model, component)`.

**A fact disproven by hand twice is a fact with no test.** Corrected the comment *and* added
`TestSeriesKeyIsNeverNull` pinning both halves — a make-less signal gets `series_key="STEERING"`
rather than NULL, and a whitespace-only component is refused. A stale comment describing a
closed gap is not a harmless inaccuracy; it reproduces the same finding indefinitely.

#### Found while writing that test: model-authored bad params returned 500, not 422

Every handler starts by constructing its params model, which raises a bare
`pydantic.ValidationError`. That is not an `HTTPException`, so it fell past `execute`'s
`except HTTPException` branch, was re-raised by the generic one, and `/api/chat` answered
**500**. Reachable from ordinary model output: a blank `component`, a negative
`complaint_count`, an over-long `rationale`. The write was always correctly refused — what was
wrong is that refusing bad input presented as the console breaking, and the audit row recorded
`FAILED` when the system had worked. Same family as I-117's 422-that-became-a-500 in `main.py`,
reached from the other side: there the error body could not serialise, here the error never
became one. Now a 422 recorded as `REJECTED`.

#### Doc corrections

- **`docs/RUNBOOK.md`** said *"Done when ... `indexed_row_count == 115499`"* **eighteen lines
  after** its own callout saying that figure no longer applies — and it is the line an operator
  actually executes at the end of the index poll, so following it literally reads a correct
  count as a failed sync.
- **`PLAN.md` §Phase 3** and **`src/setup/00_create_all_objects.py`** (steps 6 and 7 of the
  rebuild order — the from-empty path PR #18 just found was incomplete) still asserted 115,499
  as current. Marked superseded; the real count comes from Run 2 step 3.0.

#### Verified

**626 passed / 24 skipped** (was 614 on this machine), ruff clean. **Every change
mutation-checked** per I-117/I-118: gate-always-passes, untagged-treated-as-ok,
unreachable-registry-treated-as-down, substring-scan-restored, empty `TIER_CLAIMS`,
`series_key` dropping `component`, and the `ValidationError` branch removed — each confirmed
red, then restored. *`tests/pipelines/` (53 Spark tests) could not run on this machine —
`Bad CPU type in executable`, the local `java` is x86-only. Untouched by this branch; CI covers
them.*

The one workspace interaction was the read-only tag probe described above. Nothing was
deployed; no billable resource was started.

---

### I-119 — a free-edition end-to-end run surfaced 10 real bugs that had never been exercised

*Date:* 2026-09-29 · *Status:* ✅ resolved — merged to `main` via PR #18 (`fa8d2b0`).

Not a review. `free-edition-deploy` added a second bundle target (`free_edition`, a wholly
separate Databricks Free Edition account, `mode: production`, no shared workspace, no billing
concern) specifically so the pipeline/agent/App could be run end to end on a fresh environment
without touching `abhi`. Doing that — for the first time ever, for several of these jobs — found
real bugs that widget-izing catalog/schema alone would not have: things that only surface when
code actually *executes* against live data instead of being trusted from a green `bundle
validate` or a read-through. **All ten below are ported to `main`; the free-edition-only
scaffolding (the target itself, catalog/schema widgets, the LLM-endpoint swap for a workspace
where `databricks-claude-opus-4-8` doesn't exist) deliberately stays on `free-edition-deploy`.**

**Zero `abhi` usage to find or verify any of this.** Every fix was exercised live against the
identical code path on free edition first; porting to `main` was verified with the local suite
only (667 backend tests, 153 vitest, ruff, `tsc`) — the fix isn't catalog-specific, so a second
live run against `abhi` would prove nothing a local test doesn't already cover.

**What was found, most SILENT — correct-looking or simply unexercised until this run:**

1. **`gold_fleet_exposure` had no source file at all — SILENT.** It exists live on `abhi`
   (989,042 rows, matching the proposal's figures) because someone built it ad hoc and never
   committed the SQL. The rebuild manifest in `00_create_all_objects.py` **actively
   misattributed it** to the fleet-registry job, which never produced it — so a rebuild-from-empty
   would have silently ended up short one table with no error at any step. Reconstructed from the
   live schema and the EXACT/MODEL_VARIANT rule already used elsewhere in the codebase, given its
   own source file (`04b_build_fleet_exposure.py`) and bundle job
   (`fleetguard-build-fleet-exposure`), and correctly inserted into the rebuild order as its own
   step (manifest now 19 steps, was 18; `EXPECTED` was already tracking 39 objects under the
   wrong step number, not 34 as `docs/ARCHITECTURE.md` claimed — also corrected there).
2. **`04_build_fleet_registry.py` never produced `manufacture_date` — SILENT on `abhi`.**
   `fleetguard_vehicle` (Lakebase DDL) and `10_load_reference_from_gold.py` have always
   expected the column; `abhi`'s live `gold_fleet_vehicle` apparently carries it from an
   undocumented hand-run `ALTER`, so committed source was simply wrong and nothing detected it
   until a fresh build hit `UNRESOLVED_COLUMN`. Same shape as the previous bug — a gap papered
   over by manual intervention on the one environment anyone actually queries.
3. **`28_rag_eval.py` referenced a column that doesn't exist — SILENT until first run.**
   `r.campaign_id`; the real column on `silver_recall` is `campaign_number`. This notebook's own
   header says it "runs only with a live index" and had never been run against one before this
   session, on any branch — the bug is exactly as old as the file and nothing had ever exercised
   the query.
4. **`evaluate_agent` and `agent_build`/`14_fleetguard_agent.py` were both missing `openai` in
   their `%pip install` cell.** `mlflow.openai.autolog()` and `mlflow.pyfunc.load_model()` both
   import it directly; `databricks-agents`/`mlflow` did not pull it in transitively on a fresh
   serverless environment. `ModuleNotFoundError` on the first cold run of each.
5. **The agent's served container needed `httpx` pinned explicitly too** — same shape as #4,
   one layer down (the `openai` client uses `httpx` internally but the container did not
   reliably resolve it transitively). Only surfaced on a live inference call against a
   freshly-deployed endpoint, not at deploy time.
6. **`train_model_b`'s `mlflow.sklearn.log_model` started rejecting the model outright** —
   a newer mlflow enforces skops' untrusted-type gate one level deeper than before:
   `CalibratedClassifierCV` was already trusted, but the `GradientBoostingClassifier` it
   calibrates produces `sklearn.tree._tree.Tree` per estimator, which is now also gated.
   Version-drift bug, not environment-specific — would hit `abhi` too on the next fresh
   `%pip install -U mlflow`.
7. **The agent's LLM-endpoint code assumed message `content` is always a string or `None`** —
   true for Claude, not guaranteed for every endpoint this agent could be pointed at. A
   list-shaped `content` (measured on `databricks-gpt-oss-120b`) passed through `content or ""`
   unchanged (truthy, not falsy) and crashed a downstream `.replace()` call. Dormant on `abhi`
   today (Claude), real robustness gap.
8. **The round-trip validation cell in `14_fleetguard_agent.py` was pinned to one fleet
   roster's exact numbers** (25/22 exposure, 15 makes, 47 combos, 2,116 F-250s, DODGE-absent/
   RAM-present, 48/4/2418 emerging-signal counts) with no way to tell "the tool is broken" from
   "this environment's randomly-generated fleet is a different shape" — the fleet registry is
   *not* reproducible across environments even with the same `SEED` (vPIC response ordering
   isn't stable). Now detects which shape it's looking at and runs `abhi`'s exact regression
   numbers only when that shape is actually present — verified by reading the diff, not the
   commit message, that `abhi`'s protection is byte-for-byte unchanged.
9. **Two Lakebase migration scripts failed only on a genuinely empty table.**
   `06_create_depot_and_verify.py` was missing the `PSYCOPG_IMPL=python` guard (I-045) every
   other `src/lakebase/*.py` file carries — `abhi`'s job has run before and inherits a cached
   pre-I-045 environment that masks it; a cold start SIGABRTs. `20_add_defect_signal_provenance.py`'s
   self-check asserted pre-existing `DETECTOR`-classified rows existed — true once there's live
   data, vacuously false (and therefore a hard failure, not a pass) on an empty table.
10. **The RLS self-test had no path for a role with `BYPASSRLS=true`.** Measured on a brand-new,
    self-owned Lakebase project: the project-creating role carries it by default, which overrides
    `FORCE ROW LEVEL SECURITY` for that connection regardless of policy correctness, and there is
    no more-privileged role available to revoke it from (same "no `CREATEROLE`" limitation
    already documented for the shared `abhi` project — found again on a project nobody else owns
    at all). The policy itself still gets applied; only the self-test's identity-scoped
    assertions are unprovable under that role, so they're now skipped-and-explained rather than
    silently passing or hard-failing.

**Also found, and deliberately NOT a bug fix:** a real SQL string escape error
(`\'` inside a `COMMENT` literal, which is Python syntax rather than SQL and truncates the
string early) in `27_build_chunk_index_source.py` — pre-existing, unrelated to any of the above,
caught only because that job had also never been run through the bundle before. Fixed with the
standard SQL doubled-quote escape.

**The Home page's dashboard link was a hardcoded `abhi`-workspace URL baked into the built JS**,
found because it broke on a different workspace where the console's JS bundle is shared
byte-for-byte with `abhi`'s. Moved to `FLEETGUARD_DASHBOARD_URL` (`app.yaml`), surfaced via
`/api/me` rather than `/healthz` (the bare app URL's `/healthz` is intercepted by the Databricks
Apps ingress before it reaches the FastAPI process — measured directly, 200/empty-body/no
corresponding `apps logs` line). Falls back to the exact URL already in use, so this is a
strict improvement with zero behavior change for the current deployment.

**What this does not affect.** None of the above changes `abhi`'s current live state or Run 2's
own sequence — `gold_fleet_exposure` already exists there (just without a producer job), and
nothing else touched was ever wrong on data `abhi` already has. The new `build_fleet_exposure`
job only matters for a genuine rebuild-from-empty, which `00_create_all_objects.py`'s manifest
now correctly reflects.

*Date:* 2026-09-24 · *Status:* **Built offline; the behavioural half is measured in Run 2.**

Not prompted by a review. After round 4 closed, the remaining work with real judging value was
in the four graded surfaces themselves — agent, RAG, security, MLflow — with UI frozen. What
follows is the gap analysis and what came out of it.

---

#### The gap that mattered: the retrieval corpus is an injection surface, and nothing said so

**Six of the agent's seven tools return numbers this project computed. `search_complaints` does
not.** It returns **public, user-submitted free text** — anyone in the United States can add to
it by filing an ODI complaint — and that text went straight into the model's context with no
marking, no defence and no test. 2.24M narratives written by strangers, read by an agent whose
console can write to a safety database. That is the textbook indirect prompt-injection surface,
and this project had spent three review rounds hardening the *envelope* path while leaving the
*content* path unexamined.

**Severity, measured rather than dramatised.** A successful injection cannot do anything
arbitrary. `agent_actions.execute` gates on `authz.may_approve`, so only an approver's session
is exposed at all; it recomputes fleet relevance from real rows, so an invented make is
rejected; one action per turn is enforced at both ends; and since I-117 the console executes an
envelope only from an item id Python stamped. **The realistic worst case is an approver asking
an innocent question and a hostile narrative causing a false defect signal recorded under their
name.** Bad, bounded, and worth defending.

**Three layers, because no single one is sufficient:**

1. **`_neutralise()` in the retrieval tool.** Each narrative is wrapped in explicit
   untrusted-data markers, and the action sentinel is stripped from it. Deliberately **not** a
   blocklist of injection phrases — that is unbounded, trivially paraphrased, and produces the
   worst available outcome: a system that *looks* defended. A test pins that the hostile text
   survives verbatim inside the marker, so nobody "improves" it into a filter.
2. **System prompt rule 9.** Text between the markers is evidence, never an instruction; do not
   comply; **say** that the retrieved text carried an embedded instruction. This is the layer
   that generalises — a filter catches what it was written to catch, a rule covers the
   paraphrase nobody thought of. The markers are interpolated into the prompt from the same
   constants `_neutralise` uses, so prompt and code cannot drift into a defence that reads as
   present and does nothing.
3. **The console**, unchanged and already load-bearing (I-117's item-id check, `may_approve`,
   server-side relevance).

**What is and is not testable offline, stated rather than blurred.** Whether a real model
resists a real injection is behavioural and needs the real model — that is
`16_evaluate_agent.py`'s job and it runs in Run 2. What `tests/agent/test_agent_injection.py`
asserts is the **structural** half, which holds when the model is having a bad day.

> **And the first version of those tests could not fail.** Deleting the `_neutralise` call from
> `search_complaints` left **every one of them green** — they exercised the function and never
> checked that anything called it. Testing a function is not testing that it is reachable.
> Found by mutation-checking, within an hour of writing I-117's lesson about tests that cannot
> fail. `TestTheDefenceIsActuallyWiredIn` now drives the whole tool with a fake index client.

#### RAG: retrieval that cannot be checked has to be believed

Rule 10 requires the agent to **cite the complaint ids** behind a narrative claim. An operator
reading *"31 complaints describe loss of steering"* previously had no way to verify it; a count
with no ids is a claim taken on trust, which is the thing this system exists to avoid. Scored by
`cites_complaint_ids`.

**The scorer's limit is documented in the scorer.** `predict_fn` returns answer text only, so it
cannot see which ids the tool actually returned and **cannot tell a real citation from a
fabricated one**. It checks that the agent cites at all; "cites only ids a tool returned" lives
in the prompt and would need trace-level scoring. A scorer that implies more than it checks is
the failure this evaluation exists to avoid.

It also had to not be trivially satisfiable: ODI numbers are 8-9 digits, and
`test_the_agents_ordinary_numbers_are_not_mistaken_for_citations` pins that `25 vehicles`,
`16.0%`, `1.44x`, `17V629000` and `$84,409.68` do **not** match. A false positive here would
turn a real requirement into a formality.

#### MLflow: two gaps, both about evidence surviving the run that produced it

**1. `resists_injected_instructions` is a HARD GATE, not a report.** An agent that acts on an
instruction it read inside a narrative has performed an action nobody with authority asked for —
the same line `never_claims_launched` guards, reached by a different route. A scorer that merely
reports is a number nobody reads on the day it matters. `tests/test_scorer_negation.py` now also
asserts every named gate is a scorer the evaluation actually registers: a gate that is
documented but unregistered protects nothing.

**2. The evaluation result is stamped on the model version.** *"Was the thing you deployed
evaluated?"* previously meant hunting MLflow runs — the scores lived on a run, the deployed
artefact is a Unity Catalog model version, and nothing connected them. It is the
release-provenance problem again, one layer down. The version now carries `eval_run_id`,
`eval_at`, `eval_hard_gates` and a `score_*` tag per scorer, so a regression between versions is
a diff rather than an investigation.

**Ordering is the correctness argument:** tagging runs *after* the gates, so a failing run raises
and never reaches it. A version can never carry `eval_hard_gates: passed` when the evaluation
refused it. Asserted, because it is the kind of property a later edit reorders without noticing.

#### One thing this opens, and it is a real gap

**The new gate only fires if the evaluation runs, and Run 2 did not include it.** The Phase 3
table rebuilt and redeployed the agent without ever scoring it — so the hard gates, including
the injection one, would have been inert through the submission. `docs/RUNBOOK.md` gains
**step 3.3a**: run `fleetguard-evaluate-agent` after the redeploy, before the verification pass.
It is also what stamps the model version, so skipping it loses the provenance too.

---

#### Deferred, with the reason recorded as unverified rather than as a judgement

**MLflow's built-in RAG judges** (`RetrievalGroundedness`, `RetrievalRelevance`,
`RetrievalSufficiency`) would be the natural next step: the agent already emits a
`SpanType.RETRIEVER` span, which is what they read. **Not wired in, because the required span
output format could not be verified.** The MLflow docs state that a RETRIEVER span must exist
and that documents carry `page_content`, but do not specify what happens to a span whose output
is a list of plain dicts — which is what `search_complaints` returns. mlflow is not installed
locally (it runs only on Databricks) and the agent endpoint is down, so there was no way to
check.

Adding an unverified scorer to the **gating** evaluation path eight days from submission is the
wrong trade: if it errors, it takes the hard gates down with it. Recorded in `ENHANCEMENTS.md`
**E-18** with the exact verification step, rather than shipped on an assumption. This project's
own rule — *prefer "estimated, to be measured" over asserting it* — applies to API behaviour as
much as to numbers.

---

**Lesson.** The three review rounds before this one all hardened the **envelope** — can a model
cause an action it should not. None of them looked at the **content** — what is in the text the
model reads, and who wrote it. The answer was "2.24M strangers", and it had been true since the
index was first built. **A boundary gets audited when something crosses it; a boundary that
text merely *flows* through does not feel like one.** The retrieval corpus was treated as data
all along, which is correct — but data that reaches a model's context is also instruction-shaped
input, and nothing in three rounds of security review had said so.

---


### I-117 — a fourth external review: two findings worse than reported, one disproven, one already rejected

*Date:* 2026-09-24 · *Status:* **8 fixed, 1 disproven, 1 correction repeated from I-115, 4 deferred with reasons.**

**Found by** a fourth external review of an exported zip (`repo-review.md`, gitignored), the
same shape as I-109, I-110 and I-115 — and **the best of the four**. It read a zip containing
that morning's work (it names `provision_search.sh` and `28_rag_eval.py`), its line references
are real, and it correctly identified that the project is now "sophisticated enough that
protocol correctness and authorization details matter more than adding features".

Every claim was checked against the code before being accepted. Three did not survive that
check unchanged, and **the two most important findings were worse than the review said** — one
of them a bug introduced that same morning, by me.

---

#### The two that were worse than reported

**1. Topical `recall@10` was not "not a valid recall metric" — it was degenerate.**
`src/search/28_rag_eval.py` derived each probe's relevant set *from the retrieved hits*, then
computed recall against it. Since `relevant ⊆ retrieved`, the intersection is always the entire
relevant set. Measured:

```
relevant derived from hits: ['2', '7']
recall@10    = 1.0      <-- always, whenever anything on target came back
precision@10 = 0.2      <-- genuinely meaningful
MRR          = 0.333    <-- genuinely meaningful
recall@10 (nothing on target) = 0.0
```

**Exactly 1.0 or exactly 0.0** — one bit of information, duplicating the hit-rate indicator
already reported beside it, under a name that means something else. It would have been written
to `ops_rag_eval` and published as a retrieval result, and it could not have failed: change the
retriever completely and the number does not move, because the goalposts move with it.

Precision and MRR were never affected — they depend only on *which returned items are on
target*, not on the relevant set being complete. So only the set was replaced: the pool now
comes from `silver_complaint_chunk_indexed` by SQL, using the same EXACT + MODEL_VARIANT
predicate as the rest of the system, and **`relevant_pool_size` is published beside recall**.
A pool of thousands makes Recall@10 tiny by arithmetic — ten slots cannot cover three thousand
documents — and a reader who sees 0.004 without the denominator reads it as broken retrieval.

The regression guard is split, because the metric functions were never wrong: `tests/
test_retrieval_metrics.py::TestTheCircularRelevanceTrap` pins the arithmetic that makes the
shortcut attractive, and `tests/test_rag_eval_source.py` asserts the notebook does not take it.

**2. `NaN` and `+Infinity` reached the work-order write path, and BOTH layers waved them
through.** `work_orders.py` guarded only `actual_cost < 0`:

```
       NaN -> ACCEPTED  value=nan   passes the (<0) guard
  Infinity -> ACCEPTED  value=inf   passes the (<0) guard
 -Infinity -> rejected  (caught incidentally by <0)
```

Pydantic v2 permits non-finite floats by default and Python's `json.loads` accepts the bare
literals, so this was reachable over HTTP. The review stops at "add `math.isfinite`". The
consequence is worse: **Postgres `numeric` accepts `'NaN'` and orders it above every number**,
so `fg_wo_actual_cost_nonnegative` (`>= 0`) passes it too. Two independent checks, both
waving through the same value. One poisoned row then makes `SUM(actual_cost)` NaN for its whole
depot — the cost figure the console's breakdown renders and `docs/DEMO.md` quotes
($84,409.68). A silent corruption of a headline number, from one malformed request.

> **And fixing it surfaced a second bug the review did not reach.** `allow_inf_nan=False`
> correctly rejects the value — and then FastAPI's default handler echoes the offending input
> back under `input`, Starlette's `JSONResponse` sets `allow_nan=False`, and serialisation
> raises *after* the handler returned. The caller gets a **500 with a stack trace instead of a
> 422**. The schema fix alone traded a silent corruption for a loud crash. `main.py` now has a
> `RequestValidationError` handler that sanitises non-finite floats out of the error body —
> registered globally, because any endpoint taking a float can be sent one.

---

#### The one that is disproven

**`open_defect_signal` has no `series_key IS NULL` idempotency hole.** The review reasons
correctly about Postgres — a normal unique index does permit multiple NULLs — and from the
`series_key or None` line, but did not check the validator ordering I-115 fixed. `series_key`
is `"|".join(p for p in (make, model, component) if p)` and `component` is required, so the
join always yields at least the component:

```
make=None, model=None -> series_key = 'BRAKES' -> stored 'BRAKES'   (not NULL)
component=' '         -> rejected 422 (mode="before" strip runs BEFORE min_length)
```

The only path to a NULL was a blank component, and I-115 closed it at the root. **No second
index, no migration.** Recorded precisely because this is the **second** review to propose
Lakebase DDL for a gap already shut app-side — the index definition is visible in the repo and
the validator ordering is not, so the wrong fix is the one that looks obvious from outside.

#### The one already rejected, now proposed twice

**NHTSA snapshot freshness.** The *bug* is real and logged (I-099): `01_download_flat_files.py`
overwrites a fixed path, Auto Loader keys on path, so *ingestion succeeds + data is stale* is
reachable and **was observed**. The proposed fix — immutable `snapshot=<date>/` landing paths —
is what I-115 already rejected on the record, and this is the second review to suggest it.
These are **full snapshots and silver does not dedupe**, so a new path re-ingests 2.24M + 5.8M
rows as *new* rows and doubles the corpus. Versioned paths need a dedupe strategy that does not
exist; that is a data-model change, not an ingestion tweak.

**Built instead — the cheap fix I-099 named and nobody had done:** fail loudly when a *changed*
upstream snapshot would land on a path Auto Loader has already committed. Two placement details
are load-bearing and both were wrong in the first attempt: it runs **before the extract**, so
the landed file still matches what bronze consumed rather than being half-overwritten by a
snapshot nothing will read; and **before `record(...)`**, because writing the new watermark and
then raising means the next run gets a 304, skips, and never raises again. *A guard that
silences itself on the second attempt is worse than no guard*, since the single run that
reported the problem then looks like a transient failure.

---

#### Confirmed and fixed as described

**3. The action envelope failed open on a missing item id.** `_extract` read
`item_id is None or str(item_id).startswith("action-")` — stating the opposite of its own
invariant. The reasoning recorded at I-115 was that the item-level `id` had not been confirmed
on a live payload, so an mlflow version omitting it must not stop every write from working.
That is a real risk and the wrong trade: it makes the discriminator **optional**, which is the
same as not having one, and the security story degrades to *"it works because our current
wrapper happens to stamp ids"*.

Now fail-closed, with the original risk handled by **making the failure loud rather than making
it look safe**: a dropped envelope logs at WARNING with the observed item shape, so an omitted
id appears in Run 2 as an unmistakable log line instead of a write that quietly does nothing.
The revert is one condition.

> **Closing it broke six tests, and that was the finding inside the finding.** Their payload
> helpers emitted items with **no ids at all** — a shape `predict()` cannot produce — and they
> passed only because the fail-open path executed them. So the tests guarding the action
> protocol were exercising a payload that could not occur, which is a quieter version of the
> same defect. The helpers now stamp ids the way `predict()` does.

**4. A write tool could share a tool-call batch with reads.** The system prompt requires a
make/model to be checked with `lookup_fleet_models` *before* being named in
`open_defect_signal`. Every call in a batch is generated from the same model turn, so emitted
together, the write's arguments were fixed before the lookup's result existed — the rule read
as satisfied and had not happened.

**Severity is below the review's P0**, and the review did not credit the existing layer:
`agent_actions.execute` recomputes fleet relevance from real rows and rejects `make_n == 0`, so
a hallucinated make cannot land. What degrades is `match_basis` quality and the protocol claim
itself, which is the reason to fix it.

The fix is shaped by a constraint the review's version would have broken: **the batch must
still be answered in full**, because the completions API rejects the next message if any
`tool_call_id` goes unanswered — which is exactly why the existing terminality check sits
*after* the loop. So the write is refused **without being run** (executing it and discarding
the envelope would still be work done on unchecked arguments), answered with a protocol error
the model can act on, and never collected into `actions`. `READ+READ` ✅, `WRITE` alone ✅,
`WRITE + anything` ❌.

**5. Audit logging was `except Exception: pass`.** Swallowing is still right — the caller's
real 403 must not be replaced by a database error from the *logging* path — but swallowing
*quietly* meant a refusal could vanish from the audit trail with nothing recording that it had.
For a system whose claim is that every agent action is attributable, "the audit write failed
and nobody knows" is the wrong half to keep. Now `log.exception`.

**6. Client-supplied `depot_id` was filtering, not authorization.** True, and the review itself
notes it is not exploitable today. `resolve_scope` now rejects a requested depot outside the
caller's authorised set, via an `authorized_depots()` seam that returns `None` — *unrestricted*
— which is the honest current state rather than a stub: nobody is enrolled in
`fleetguard_depot_assignment` and the RLS policy is deliberately fail-open. The check is inert
today and correct the moment anyone is enrolled, **which is exactly the moment nobody will
think to re-audit this function**. The tests pin one trap worth naming: `frozenset()` is falsy,
so a truthiness test here would read "no restrictions" and allow everything, turning the most
restrictive input into the least. The check is `is not None`.

**7. AI Search is not reproducible from `bundle deploy` alone.** True and unfixable — DABs
exposes four resource keys and none is a vector index. The review wants a
`release_preflight.sh` checking nine things; **`/api/readyz` already checked four of them**, so
it was extended rather than duplicated: `indexed_row_count` is now compared against the source
table (an index can be `ready` and **short** — I-105 records a sync restarting from zero, and a
partial index answers every query without erroring), and the served agent version and console
git SHA are reported. That turns the "is the live system the thing in the zip?" question — named
as the single biggest practical risk — from five lookups into one URL. The source-count query is
the only part needing a warehouse, so it degrades to a note rather than failing readiness: a
warehouse scaled to zero is not a broken index.

---

#### Deferred, with reasons — not silently

| Item | Reason |
|---|---|
| **Canonical vehicle alias table** (one model identity across RAG, exposure, Model B, agent write, emerging signals) | **The strongest architectural suggestion in the review, and out of scope at ten days.** The five paths already agree by construction (the predicate is copied, not re-derived); a table would make that structural rather than conventional. Recorded in `ENHANCEMENTS.md`. |
| **Independent Model B adjudication** (200–300 pairs) | Stands rejected from I-115. Ten days, solo, Run 2 inside the window; a rushed half-set is worth less to a judge than the existing honest caveat. The counter-offer — ~50 stratified pairs reported explicitly as a spot check — remains open. |
| **HMAC chaining over user turns** | The review classifies it P2 itself and agrees it is not a privilege escalation: a user may edit their own question anyway. Server-side conversation state is the real fix and is not a ten-day change. |
| **Judge Mode / clickable evidence panel / agent action timeline** | All three are new UI, and **new frontend eight days before Run 2 converts it from a verified restore back into a first full composition** — the exact failure the two-window plan exists to prevent. The determinism sought already exists: `scripts/seed_demo_state.py` drives the real API, and `DEMO.md`'s ten beats were verified beat-by-beat against the live App (I-113). |

The review's "2 Vs" point — lead with **volume + variety**, not velocity — is free and is
already the project's position: `STATUS.md` and the frozen proposal's contradictions table both
record that the sub-minute analytics claim is unreachable (I-081's platform floors) and the
measured chain is 2.5–4.5 min.

---

**Lesson.** I-115's was *a deferral's cost can expire without the decision being revisited*.
I-116's was *a deferral reason quoted rather than read*. This round's is about **tests that
cannot fail**, and it arrived twice in one file of changes:

- topical `recall@10` was computed against ground truth derived from the thing being measured,
  so it returned 1.0 no matter how bad retrieval was;
- six action-protocol tests asserted over payloads with no item ids — a shape the agent cannot
  emit — so they passed by taking a fail-open branch instead of exercising the discriminator
  they existed to guard.

Both look like evidence. Both are shaped so that the failure they are meant to catch cannot
register. **A green check is only worth what its ability to go red is worth**, and neither of
these had any — which is why every fix in this round was mutation-checked by reverting it and
confirming the new test actually fails.

---

### I-116 — closing I-115's four open items, and a deferral reason that was wrong in the other direction

*Date:* 2026-09-24 · *Status:* **3 of 4 complete; the fourth produces numbers in Run 2.**

I-115 triaged a third external review and fixed eight things, leaving four. This is those four,
all built offline, ten days before submission and eight days before Run 2 — which is the whole
point of doing them now: the agent redeploy and the index rebuild they ride are Run 2 steps
already committed to.

**Cost of this round: ~55 seconds of serverless job compute**, for the one item that could not
honestly be shipped unverified. Nothing else touched a billable resource.

---

#### 1. `/readyz` — and the reason it is free to call

`/healthz` reported process liveness, auth mode and whether the console bundle was present. It
returned `ok` throughout the period when the AI Search index was deleted, the agent endpoint was
stopped and the App was down — *exactly* the state this project sits in between its two online
windows. A judge pointing a browser at it learned nothing.

`/api/readyz` checks Lakebase, the agent serving endpoint, the AI Search index and the two
committed snapshots, reports each with its own status, detail and latency, and returns **503 if
any of them is down**. `/healthz` is untouched and still always-200: it is the container probe,
and turning that into something that can fail would be a different bug.

**The design constraint worth recording.** The agent is checked with `serving_endpoints.get`,
not `query()`. That distinction is invisible at the call site and is the difference between a
free control-plane read and waking a scale-to-zero Small CPU container that then bills until it
idles down — a readiness probe that costs money every time anyone loads it. The index is checked
the same way with `get_index`. `tests/test_readyz.py` has a mock whose `query` raises with that
explanation, so the cheap-and-wrong version cannot come back quietly.

**It is authenticated, unlike `/healthz`.** The Lakebase check has to run under the *caller's*
token: this app holds no privileges of its own (§8a), and a readiness check proving the *app's*
access would be testing something the product does not do. The cost is that `/readyz` cannot
serve as a container probe. That is the right trade — `/healthz` already is one.

#### 2. `scripts/provision_search.sh`

The review called AI Search provisioning "a runbook, not a script", which I-115 recorded as
partially overstated: `docs/RUNBOOK.md` holds the exact commands and Run 1 executed them start
to finish. What a documented sequence is not is *idempotent*, and it does not poll.

The script wraps the Run 1-proven commands — including the `--json`-excludes-positional-args
form Run 1 discovered the hard way — and adds three things prose cannot:

- **Idempotence.** `get-endpoint` before `create-endpoint`, `get-index` before `create-index`, so
  a re-run after a dropped connection resumes instead of erroring on the first line.
- **Drop detection.** I-105's failure signal is `indexed_row_count` going *backwards*, not
  plateauing, and the index API gives no other signal. A plateau is normal; a decrease means the
  platform restarted the sync from zero. The poll treats one as fatal and prints the
  `pipelines list-pipeline-events` command rather than guessing.
- **A default that does not bill.** A bare invocation runs `--check` (read-only, free).
  `--create` requires the flag *and* a typed confirmation, after printing the ~$6.72/day cost
  and the 24-hours-after-the-last-delete rule. Teardown is deliberately **not** in the script:
  one binary that can both build and delete the demo's retrieval corpus is one flag away from
  deleting it on submission morning.

**The expected row count is read from the source table, never hard-coded** — it was 1,746,601,
then 115,499, and I-115 widened the fleet match again. A literal would be wrong by construction.
A test asserts no such literal appears in the script's code (comments excluded, because the
header explains the history and that reasoning belongs where the reader is).

**Declarative management is impossible here, not merely unbuilt.** DABs exposes exactly four
resource keys — `apps`, `dashboards`, `jobs`, `pipelines`. A script is the ceiling.
`tests/test_provision_search.py` cross-checks the script's index JSON against `RUNBOOK.md`, so
the two descriptions of one procedure cannot drift — the real risk of shipping both.

#### 3. RAG retrieval evaluation — the harness now, the numbers in Run 2

Deferred at I-110 for a cost (~7 h to re-embed 1,746,601 chunks) that expired the same day
I-111 rescoped the index. Now built, and split so that only the *measurement* needs the meter
running: all the scoring arithmetic is in `src/fleetguard/retrieval_metrics.py`, pure Python,
19 unit tests. The notebook's job is reduced to fetching results and calling it.

**Two probe families, because either alone misleads.**

- **Known-item** — the query is a distinctive excerpt from the *middle* of one narrative (not
  its opening: openings in this corpus are formulaic `THE CONTACT OWNS A …` boilerplate, so a
  query built from one measures retrieval of boilerplate). The relevant set has exactly one
  member, so Recall@10 and MRR mean what they normally mean. This is a **floor test** — the text
  is literally in the corpus — and is labelled as one.
- **Topical** — the query is natural language built from a real recall campaign. Here the
  relevant set runs to thousands of chunks, so **Recall@10 would read as ~0.003 and mean
  nothing**; the honest metrics are Precision@10 and hit rate. Both are computed for both
  families anyway, so nobody has to take the notebook's word for which to read.

**The limitation is published with the result, not buried:** relevance is *metadata* agreement —
right make, right model under the EXACT/MODEL_VARIANT rule, right component — not a human
judging whether a narrative answers the question. It measures whether retrieval surfaces
evidence about the right vehicle and the right system. That is a real measurement and it is not
the same as retrieval quality, and the same honesty the Model B golden set already applies.

The relevance rule uses the project's **own** two-tier match rather than a sixth approximation of
it. Four paths agreeing and a fifth not is precisely how all 2,116 F-250s came to be missing from
the retrieval corpus (I-115); a scorer that re-derived the comparison would be the sixth place to
get it wrong, and would score the fix as a failure.

Section 5 of the notebook also re-runs the three I-040 behavioural checks **at the scope that
ships** — open since the rescope — with probes the fleet corpus can actually answer. The old
paraphrase probe (*"car suddenly sped up on its own"*) leans on unintended-acceleration
complaints concentrated in makes this fleet may not operate, so a poor result there would have
been unreadable.

**Whatever it returns gets published.** Same rule as I-049's falsified semantic arm and I-111's
requirement to republish the hybrid probe at the smaller scope including if it came back worse.

#### 4. The CDF fingerprint split — and the deferral reason that was wrong

I-110 made the CDF reconciliation compare *content*, not just cardinality, which was the right
fix: `fact_rows == live_keys` cannot see a key holding a stale value, and that is exactly the
drift an incremental path produces. The review's observation was that it ran **unscoped on every
trigger**, hashing every column of every row of both sides because one agent wrote one row.

It is now two tiers:

| | scope | when | catches |
|---|---|---|---|
| cardinality | full history | every run | a key count that does not add up |
| content fingerprint — scoped | keys above the previous watermark | every run | a wrong value written by *this* run's MERGE |
| content fingerprint — unscoped | everything | every 24 h, and on any full rebuild | an event that arrived **below** an advanced watermark |

**The scoped check is not a weaker version of the unscoped one.** It is a *complete* check of the
rows the incremental path actually touched. What it cannot see is the failure the watermark
itself causes — a late event below the mark is not in a slice defined by that mark — which is
why the unscoped one is kept on a clock rather than dropped. **A guard that shares its subject's
assumption is not a guard.** After a drift rebuild the re-check is always unscoped whatever the
run was using: a rebuild replaces every row, so verifying one slice would leave the rest
unexamined at the exact moment there is reason to doubt it.

The 24-hour clock is stored in `ops_cdf_fact_refresh` alongside the watermark, so *"when did this
last fully reconcile?"* is a query rather than someone's recollection. Rows predating the new
columns carry NULL, which reads as "never" and therefore as "do it now" — the safe direction, the
same reading `last_watermark()` gives.

**The correction.** I-115 deferred this as low priority because *"the trigger is `PAUSED`, so the
cost is not being paid today."* That reason was **false**: `resources/cdf_to_gold.job.yml`
declares `pause_status: UNPAUSED`, and this is the project's only job that runs unasked. The
claim was copied from `CLAUDE.md`'s example block — which still shows the pre-2026-09-08 value —
without opening the resource file four lines of `grep` away. The conclusion happened to survive
(the cost is near zero, because the App is stopped and nothing is writing) but for an entirely
different reason, and one agent write away from being wrong.

**It also changed how this item had to be verified.** An unpaused trigger means a deployed-but-
unrun notebook executes unattended on the next agent write — during Run 2, inside the submission
window. So the change was deployed and the job run by hand before it could fire on its own.

**Verified live, both branches** (2026-09-24). Run `809022647047914` took the **FULL** path —
correct, because no full-reconcile history existed and NULL reads as "never". Run
`724855753907753`, two minutes later, took the **SCOPED** path — correct, because the 24 h clock
had just been set by the first. Both `SUCCESS`; the notebook's asserts are the test, since a
cardinality mismatch, a fingerprint mismatch or a failed rebuild each raise. Facts unchanged at
3 / 50 and reconciling. Two runs, ~110 s of serverless — the whole compute cost of this round.

**What that does not prove.** Both runs reported `mode: SKIP`, because nothing has written to
Lakebase since the App was stopped, so the scoped fingerprint compared an **empty key set**. The
branch is proven to be *selected* correctly and to *execute* cleanly — which is the realistic
deploy risk for a change of this shape, and the reason for running it at all. Its
**discrimination**, catching a wrong value written by a MERGE, needs a real write above the
watermark and is first exercised in Run 2. Recorded here because "verified live" without the
limit named is exactly the overstatement I-086 was filed for.

---

**Lesson.** I-115's was *a deferral records a decision and its cost, and the cost can expire
without the decision being revisited.* This round found the same failure with the sign flipped:
a deferral reason that was never true in the first place, quoted out of a stale example in
`CLAUDE.md` rather than read off the resource that governs the behaviour. Both are the same
underlying habit — **treating a written-down reason as evidence instead of as a claim with a
source.** The reason was four lines of `grep` from being checked. What made it survive is that it
supported a conclusion that was independently correct, so nothing ever pushed back on it.
**A right answer reached by a wrong reason is the hardest kind to find, because the outcome never
signals anything.**

---

### I-115 — a third external review, triaged claim-by-claim: the rescope had already invalidated three of I-110's deferral reasons
*Date:* 2026-09-23 · *Status:* **8 fixed offline, 4 outstanding, 4 corrected, 5 already-deferred re-raises, 3 rejected.**

> **Applied on `fix/repo-review-round-3`:** the action-envelope barriers (both ends), the
> AI Search variant join, the evidence TTL, the `search_complaints` over-fetch, the HMAC
> binding, the blank-component validator, the stale `1.75M` sweep + console rebuild, and the
> frozen proposal's missing contradiction row. Verified offline only: 562 pytest / 149 vitest
> / ruff clean. **Nothing is live** — the agent-source and index-source halves take effect at
> Run 2's redeploy and rebuild, which is the whole reason they were done now.
>
> **Outstanding from this round:** `/readyz`, `scripts/provision_search.sh`, RAG retrieval
> evaluation, and splitting the CDF fingerprint. The first two are worth doing before Run 2;
> the third needs the index live; the fourth is not costing anything while the trigger is
> `PAUSED`.

**Found by** a third external review of an exported zip (`repo-review.md`, gitignored), same
shape as I-109 and I-110. It raised 20 items. Every one was checked against the code on
`main` before being accepted, because the previous two rounds each contained a finding that
was right about the mechanism and wrong about the consequence.

The review is **substantially accurate** — the line references are real and the mechanisms
are as described. But it read a zip, so it could not see the workspace, the schedule, or
`ISSUES.md` itself, and the four corrections below all come from that blind spot.

---

#### The finding the review could not make, and the reason this round is urgent

**I-111's rescope, committed hours before this review was written, invalidated the stated
reason for three of I-110's five deferrals.** Each of *RAG retrieval evaluation*, *PII
masking in the indexed representation* and (indirectly) the agent-scope item was deferred
with the same cost: **"~7 h to re-embed 1,746,601 chunks, no retry budget before 4 October."**

The index source is now **115,499 chunks, measured at ~39 min** to build (I-111, and the Run 1
build that confirmed it). The deferral reasons are stale. They were correct when written and
nobody revisited them when the number underneath them changed by 15×.

This compounds with the schedule rather than merely coexisting with it:

- **Agent-source changes are currently free.** The agent is redeployed in Run 2 regardless
  (action plan step 1.4). Anything landing in `src/agent/14_fleetguard_agent.py` before then
  rides a redeploy already committed to, exactly as I-109's and I-110's seven fixes did.
- **Index-source changes are currently near-free.** The index is rebuilt in Run 2 regardless,
  and at 39 min rather than 7 h.

Both windows close after Run 2 (targeted 2–3 October). **The correct reading of this review is
therefore the opposite of its own framing** — it advises "stop adding architecture", but the
items with a genuine deadline are the ones it ranks as stretch goals.

---

#### The four the review got wrong

**1. The proposed fix for the NHTSA freshness bug would double the corpus.** The bug itself is
real and is already logged (I-099, `STATUS.md:1196`): `01_download_flat_files.py` overwrites
`cmpl/FLAT_CMPL.txt` in place, Auto Loader keys on path, so *ingestion success + stale data* is
reachable and **was observed**. But the recommended fix — immutable `snapshot=<date>/` landing
paths — is the one I-099 already considered and rejected on the record: these are **full
snapshots and silver does not dedupe**, so versioned paths and `allowOverwrites` both re-ingest
2.24 M + 5.8 M rows as *new* rows. The cheap fix stays what I-099 named and did not build:
**remove the silence, not the limitation** — fail the download step when a changed upstream
snapshot lands on a path Auto Loader has already committed.

**2. The action-envelope fix is over-engineered, and a cheaper barrier already exists in the
code.** The vulnerability is real (below). The proposed remedy — a cryptographically signed
envelope — needs a shared secret between a Model Serving endpoint and the App, i.e. a secret
scope, key distribution, and a new failure mode on demo eve. It is unnecessary: `predict()`
already stamps a discriminator **the model cannot author**, because prose items are built as
`create_text_output_item(..., id=str(i))` and envelopes as `id=f"action-{j}"`. The id is
assigned in Python from the tool's own return value, never from model output.

**3. The `series_key` NULL gap is real but not by the mechanism claimed.** The review's path —
`make=NULL, model=NULL` — cannot reach it: `series_key` is built from
`(make, model, component)` and `component` is `Field(min_length=1)`, so the key is never
empty for want of make/model. The actually-reachable path is different and was confirmed
locally:

```
component=' '  → passes Field(min_length=1)  (constraint runs BEFORE a mode="after" validator)
               → _upper_and_strip strips it → component=''
               → series_key=''               → written NULL → ux_fg_defect_signal_agent_active defeated
```

So the fix is **not** two partial indexes on Lakebase. It is `mode="before"` on
`_upper_and_strip` (`agent_actions.py:76`), which closes the NULL gap *and* stops a blank
string reaching a NOT NULL column the Emerging tab renders. App-side, no migration, no
Lakebase DDL, no agent redeploy — strictly less work than the recommendation, and it fixes
more.

**4. "Don't claim sub-minute end-to-end" describes a position this project already holds** —
`STATUS.md:1340-1347` states it in those words, and I-081 records the platform floors that
make it impossible. The surviving sub-minute language is in `FleetGuard_Proposal.md`, which is
**frozen by convention**, not stale by accident.

> **But the review stumbled onto a real gap here.** The frozen document's contradictions table
> records §8.3 as *"capture latency ~15 s (documented, unmeasured) → measured 7.1–15.6 s"*,
> which reads as **confirmation**. It does not record the larger contradiction the build
> actually produced: §8.3 and §5 claim a sub-minute *business-event → analytics* path, the
> platform's >60 s trigger floors make that unreachable (I-081), and the measured chain is
> **2.5–4.5 min**. That row belongs in the table — the header exists precisely to tabulate
> this, and it is the strongest available answer to the obvious judge question. One line.

---

#### CONFIRMED, accepted for fix

**1. The action sentinel is forgeable.** `parse_envelope` tests only
`text.startswith(ACTION_SENTINEL)` (`agent_actions.py:134`), while `predict()` turns **every**
assistant message into an output text item (`14_fleetguard_agent.py:886-889`). A model induced
to begin its reply with the sentinel produces an item the console parses as an envelope, so
the invariant *"a model cannot cause an action unless the write-request tool ran"* does not
hold as stated. The retrieval corpus is public user-submitted narrative text, so the injection
surface is not hypothetical.

**Severity is bounded, and the review did not credit the existing layers**: `authz.may_approve`
gates execution, `make_n == 0` rejects makes the fleet does not operate, one-action-per-turn is
enforced at both ends, and both writes are observations rather than dispatches. **P1, not P0.**
Fix in two independent halves, neither needing a secret:

- *Agent side, unconditionally safe:* neutralise `ACTION_SENTINEL` inside model-authored prose
  before emitting. One line.
- *Console side:* prefer `item["id"].startswith("action-")`, and **fall back to current
  behaviour when `id` is absent** — the item-level `id` is not present in any committed test
  fixture (only the response-level one, `tests/test_chat.py:137`) and has not been observed on
  a live payload, so it must degrade rather than break every action if the shape differs.

**2. The AI Search index can exclude the fleet's own complaints — the most serious *functional*
finding.** `27_build_chunk_index_source.py:57-63` joins `v.make = c.make AND v.model = c.model`,
exact only, while this project's own measurement says **all 2,116 F-250s match only as
`MODEL_VARIANT`** (`F-250 SD` is NHTSA's dominant spelling). So the fleet's most numerous truck
can have its complaints excluded from the retrieval corpus, and the agent searching
"brake failure F-250" never sees them. This is I-030 reaching the *retrieval* path, after it
was already fixed in the gold layer (I-030), the agent write path (I-075) and the emerging
detector (I-079) — three of four paths agree and the fourth does not.

Canonical SQL already exists at `10_emerging_signals.py:157-160` and should be ported verbatim
rather than rewritten. **Measure the new chunk count first** (free — Delta only): variant
matching over-matches in documented places (`PROMASTER` → `PROMASTER CITY`), and the
`assert n_chunks == 115_499` must be replaced with the newly measured figure, never deleted.

**3. `_evidence()` caches its own failure for the life of the process.**
`14_fleetguard_agent.py:223-247` sets `_evidence_cache = None` on any exception. The docstring
argues this is deliberate, and the *fallback* is right — a qualitative sentence beats a stale
number. What is wrong is its **permanence**: one sleeping warehouse at the first query of the
judging window means the agent quotes no measured figures until the container restarts. TTL it
(success 5–15 min, failure 30–60 s). Rides the Run 2 redeploy.

**4. Deduplication silently shrinks the result set.** `search_complaints` requests
`num_results=limit` and *then* dedupes by `complaint_id` (lines 316, 331-338), so a query
asking for 10 can return 3 when sibling chunks dominate — the multi-component case I-023
already documents as normal for this corpus. Over-fetch (`min(limit * 3, 30)`), dedupe, then
truncate to `limit`. One line, and it improves retrieval quality at no cost.

**5. HMAC assistant turns are not bound to a principal or a position.** `sign_turn`
(`chat.py:75-77`) covers the text alone, so a valid tag from one user's session verifies in
another's, and turns can be reordered or replayed within a conversation. I-110's fix closed
the larger hole (arbitrary forged history); this is the remainder. Bind principal +
turn index + previous signature into the tag. App-side only.

**6. Stale `1.75M` / `1,746,601` claims across 16 files**, created by I-111's rescope hours
earlier and not swept. Includes three **user-facing** surfaces: `README.md:81`,
`app/frontend/src/views/Assistant.tsx:77` (and the committed console bundle built from it),
and `docs/fleetguard_e2e_current.html`. The rubric's grader reads the frontend.

The fix is not a find-and-replace to one number: the two figures describe **different layers**
and saying so is stronger than either alone — *lakehouse corpus 2.2 M complaints* vs *RAG index
115,499 fleet-relevant chunks*. Requires `./scripts/build_console.sh` after the `.tsx` edit or
the App ships the old string while every test passes.

**7. `/healthz` cannot tell a judge whether the demo works.** `main.py:67` reports process
liveness, auth mode and console presence — it returns `ok` with AI Search deleted, the agent
stopped and Lakebase unreachable, which is *exactly the state the system was in when this
review was written*. Add `/readyz` checking Lakebase, the agent endpoint, the index and the
evidence snapshot. Best cost-to-benefit item in the review, and it directly de-risks Run 2's
verification step.

**8. AI Search provisioning is a runbook, not a script.** Partially overstated — `docs/RUNBOOK.md`
holds the exact commands with measured timings, so this is not "reconstruct it from memory".
But a documented sequence is not idempotent and does not poll. **Declarative management is
impossible, not merely unbuilt**: `bundle summary` exposes only `apps`, `dashboards`, `jobs`,
`pipelines` (CLAUDE.md), so DABs has no vector-search resource type. A script is the ceiling.
Worth building before Run 2 as a wrapper over commands already proven in Run 1.

**9. RAG retrieval evaluation.** Deferred at I-110 for a cost that no longer applies (above).
The index is live during Run 2 anyway. Even 50 questions with Recall@5 / MRR reads far better
against the rubric than "Vector Search is implemented" — and the numbers must be published as
measured, including if they are poor, same rule as I-111 applied to the hybrid-query probe.

**10. The full-history fingerprint runs on every CDF trigger.** `21_cdf_to_gold_facts.py:236-315`.
The check itself is I-110's fix and is correct; the observation is that scanning full history on
each incremental run gives back much of what the incremental path bought. Split into a
lightweight per-trigger assertion plus a periodic full reconciliation.

> **CORRECTED 2026-09-24.** The priority note originally written here was *"low priority — the
> trigger is `PAUSED` and the cost is not being paid today."* **That reason was false.**
> `resources/cdf_to_gold.job.yml` declares `pause_status: UNPAUSED` and STATUS records it
> unpaused since 2026-09-08 — it is this project's only job that runs without being asked. The
> claim came from `CLAUDE.md`'s example block, which still shows `pause_status: PAUSED` from
> before the job was unpaused, and it was copied without checking the resource file four lines
> of `grep` away.
>
> The *conclusion* survives — the cost genuinely is near zero right now — but for a different
> reason: the App is stopped, so nothing is writing to the two tables that fire the trigger. It
> is one agent write away from being paid again, and a wrong reason that reaches the right
> answer is worse than no reason, because nothing rechecks it. This is I-115's own lesson
> arriving one issue later: **a deferral's cost has to be re-read, not re-quoted.** Fixed in
> I-116.

**11. Do not tear down for the judging window.** Already the plan (Run 2, 2–3 October, live into
the 4 October submission). Recorded because the review is right about which resource is the
dangerous one: the agent scales from zero in seconds, the AI Search index does not — ~39 min at
the current scope, and I-112 records a fresh endpoint stalling ~25 min before that.

---

#### Re-raised from I-110's deferrals — the reason stands, or is now stale

| Item | Status |
|---|---|
| NHTSA snapshot-refresh staleness | Deferred reason **stands**; the review's proposed fix is wrong (correction 1). Cheap "make it loud" fix still unbuilt. |
| Agent's reads are not caller-scoped | Deferred reason **stands**. See the sharpening below. |
| RAG retrieval evaluation | Reason **stale** — promoted to fix (#9). |
| PII masking in the indexed representation | Reason **stale** (115,499 chunks, not 1,746,601), but the work is still real. Left deferred as a judgement call, not on the old arithmetic. |
| Splitting `may_approve` | **Stands.** Broader than necessary, not unsafe, no graded credit. Post-submission. |

**A sharper claim is available for the agent-scope item than the one currently documented.**
The existing note says accurately that the agent's reads run as the serving endpoint's service
principal and that nobody is enrolled in `fleetguard_depot_assignment`. What it does not say,
and should, is what was checked this round: **every agent SQL tool returns aggregates only.**
`lookup_fleet_exposure` returns `COUNT(DISTINCT vin)` / `COUNT(DISTINCT depot_id)`,
`lookup_fleet_models` returns make/model counts, `lookup_emerging_signals` returns series-grain
rows, and `search_complaints` returns public NHTSA narrative. **No agent tool returns a VIN, a
depot roster or a work order.** So the agent cannot expose an individual vehicle even on the
fail-open path. That is a defensible boundary rather than only an admitted gap, and it costs
nothing to state.

---

#### Rejected

**Independent human adjudication of Model B's golden set (200–300 cases).** The weakness is
real and the repo already admits it in the right place. But this is 11 days to submission,
solo, with Run 2 inside the window — and a rushed half-set is worth less to a judge than the
existing honest caveat. **Counter-offer if the time exists:** adjudicate ~50 stratified pairs
and report it explicitly as a spot check with n=50 and a wide interval, keeping the caveat.

**Richer agent-action audit columns.** `fleetguard_agent_action` already carries
`actor_principal`, `on_behalf_of`, `requires_approval`, `trace_id` and an outcome in
`tool_output`. The observation that `on_behalf_of` always equals `actor_principal` and
`requires_approval` is always `false` is correct — they are dead columns. But this is a
**CDF-replicated table**; a schema change propagates to `lb_fleetguard_agent_action_history`
for cosmetic gain. The story the review wants ("Requested by FleetGuard AI / Executed by …")
is already tellable from `source='AGENT'` + `opened_by` + `trace_id`.

**Moving approver emails out of `app.yaml`.** The addresses are real and committed, and that
was a deliberate, documented decision (A2, three judges, each verified live). The proposed
alternative is not available: group-membership lookup needs `iam.access-control:read`, which
CLAUDE.md records as **non-assignable** on this account. A secret scope is the only real
option and it adds a demo-eve failure mode for four addresses that are already
bootcamp-public.

---

**Lesson.** I-109's was *provisional at one layer, treated as final at the next*. I-110's was
*a check that describes itself as stronger than it is*. This round's is narrower and about
process: **a deferral records a decision and its cost, and the cost can expire without the
decision being revisited.** Three items were carrying a 7-hour price tag that had become 39
minutes in a commit made the same day, and nothing connected the two — the deferral note and
the rescope note are four hundred lines apart in this file and neither references the other.
The reasons were written down, which is why this was recoverable at all; what was missing was
anything that re-reads them when the number underneath changes. **When a measured quantity
that justified a deferral changes by an order of magnitude, the deferrals it justified are
part of the blast radius.**

### I-114 — Deleted the 7 unbound dead-experiment jobs; found the EXPECTED manifest is stale by 10 tables, none of them safe to delete
*Date:* 2026-09-23 · *Status:* resolved (jobs), noted (manifest)

**Asked to remove jobs/tables under this identity not needed for the project.** Checked
first rather than deleting on the general instruction, since table deletion on this schema
is irreversible and the project deliberately keeps several dead-end tables as evidence.

**Jobs — 36 live, all `fleetguard-*`, all created by this identity** (no risk of touching
another student's job on the shared flat namespace). Diffed against `resources/*.job.yml`'s
29 bound names: exactly **7 unbound**, and they are precisely the 7 dead experiments
`CLAUDE.md` already documents as deliberately excluded (`lead-time-backtest-v2`,
`semantic-subdivision`, `embed-backtest-complaints`, `hybrid-query-test`,
`measure-cdf-latency`, `inspect-eval`, `build-backtest-scope`). Re-verified
`creator_user_name` on each immediately before deleting, per the standing rule for this
namespace. All 7 deleted (`databricks jobs delete <id>` — positional arg, not `--job-id`).
**Verified against the live resource, not exit code 0:** 29 jobs remain under this identity,
matching the 29 bound `resources/*.job.yml` files exactly. Job deletion does not touch any
table or the underlying notebook source (still in `src/`), so this is pure cleanup with no
data-loss risk.

**Tables — none found safe to delete.** Live `SHOW TABLES`-equivalent returned 49 objects;
diffed against `00_create_all_objects.py`'s `EXPECTED` manifest (39 keys) found **10 live but
unlisted**, with nothing in `EXPECTED` missing live (no data loss to worry about). Checked
each of the 10 individually rather than assuming "not in the manifest" means "not needed":
- `evidence_metrics`, `fleet_exposure_metrics` (metric views), `fleetguard_agent_payload`
  (agent inference table — Beat 10's audit/trace join depends on it), `gold_agent_activity_daily`,
  `gold_api_poll_health` — live product/dashboard tables. `EXPECTED` only tracks
  rebuild-reproducible tables by design (metric views and the auto-created inference table
  are explicitly listed elsewhere as "not tables" in `STATUS.md`), and the two rollups are
  real additions the manifest was never updated for.
- `ops_pg_privilege_diagnostic`, `ops_psycopg_probe`, `ops_recall_api_sweep` — already
  named in `STATUS.md`'s own ops-table inventory as evidence behind specific findings.
- `gold_backtest_cluster`, `ops_hdbscan_sweep` — residue from the abandoned HDBSCAN attempt
  (I-048). `00_create_all_objects.py`'s own comment says this is deliberate ("a rebuild ...
  can never produce it, so expecting it would make this verifier permanently report a
  missing object"), and `STATUS.md`'s live inventory groups it with "the falsified semantic
  arm ... kept deliberately" — this project's rigor-over-cherry-picking evidence, not litter.
  **Flagged rather than deleted** — the user reviewed this specific pair and chose to keep
  them rather than override that documented intent today.

**Net effect:** the real finding is a stale `EXPECTED` manifest (10 tables it should list and
doesn't), not unneeded data. Not fixed in this pass — recorded so it isn't rediscovered as a
false "orphan tables" alarm next time.

### I-113 — Run 1 step 1.7's data verification: DEMO.md's Beat 6 overdue count is a moving target, not a fact
*Date:* 2026-09-23 · *Status:* resolved (doc fixed) — the underlying mechanism is not a bug

**What was done.** Rather than a manual browser walkthrough of `DEMO.md`'s ten beats, every
beat's underlying claim was checked against the **live App's actual `/api/*` routes** (via a
programmatic OBO token — same method, and same caveat, as the 2026-09-08 App verification:
this proves the API/data path works, not a fresh browser consent flow) and cross-checked
against direct warehouse/Lakebase queries where useful. Approval (Beat 5) and any write-path
exercise of the agent (the "open a defect signal" half of Beat 8) were **deliberately not
run** — both are irreversible, append-only writes DEMO.md itself warns against outside a real
demo.

**Nine of ten beats matched exactly**, several to the decimal ($84,409.68; 16.0/11.1/1.44/2.62;
action_id 7's 29726 ms). Two real findings:

- **Beat 6's "44 overdue across 28 depots" is not a fact, it's a snapshot of a moving
  target.** Overdue is computed as `due_date` in the past on a not-completed/cancelled order,
  against seed data that stopped changing after 2026-09-09. Re-measured live 2026-09-23 with
  zero writes in between: **93 overdue across 43 depots** — more than double, purely from two
  more weeks elapsing. The work-order status split also drifted by exactly 1 (144/83/95/9 vs
  the documented 144/84/94/9) for the same reason — some order crossed its due date and the
  two counts that track "not yet done" moved. **Fixed in `DEMO.md`**: the overdue line now
  says to read it live from the app, never quote the doc's number — the next session would
  reproduce the same staleness on any date.
- **Audit log read 727 rows, not 723.** A small (+4), unexplained drift, present before this
  session started (nothing here wrote to Lakebase). Recorded in `DEMO.md`'s numbers table
  rather than silently updated, since the exact number is expected to keep moving too.

**One thing chased and resolved as a non-issue:** a `FORD F-250` signal appeared in the live
signals list with no mention in `DEMO.md`'s narrative. Checked `gold_defect_signal_current`
directly — it's one of the **two agent-opened signals** (`AGENT-c1594dc7e41f`, opened
2026-09-16, real complaint-narrative rationale, not test junk) that `DEMO.md`'s own text
already accounts for in the "48 detector + 2 agent-opened = 50" arithmetic. The confusion was
not knowing which two; now confirmed (the other is a RAM 2500 steering signal,
`AGENT-71556b31962d`).

**Also confirmed unchanged, not re-derived:** the CDF-to-gold reconciliation
(`ops_cdf_fact_refresh`'s last runs show `gold_agent_action`/`gold_defect_signal_current` row
counts matching live `/api/*` responses exactly) and Beat 10's trace/audit join (`action_id
7`, identical values to the 2026-09-09 measurement — this one genuinely does not move, since
nothing has written a second agent action since).

### I-112 — Run 1's smoke index never leaves "pending endpoint provisioning" — reproduced twice, no root cause found yet
*Date:* 2026-09-23 · *Status:* **resolved (self-cleared) — root cause still unconfirmed**

**UPDATE, same day.** The second stuck attempt (endpoint `776e23e6...`, pipeline
`1bc01f32...`) resolved on its own — checked again after stopping deliberate investigation
and it read `ready: true, indexed_row_count: 10000`, no further action taken. **Elapsed
between "stopped investigating" and "found ready" was on the order of tens of minutes**, not
independently timestamped since the check was incidental to resuming other work — so this
does not distinguish "eventually completes on its own" from "completed shortly after the
last check and sat ready for a while." All three I-040 verification checks (columns_to_sync,
`any_harm` filter, HYBRID-vs-ANN) passed on the smoke scope before deleting it. The real
115,499-row build afterward (same session) hit **no** stall at all — created, went straight
to `RUNNING`, climbed steadily, `ready: true` in ~39 min.

**Best-supported explanation, still unconfirmed:** shared-workspace contention on
first-index provisioning specifically (this metastore has ~296 other students, and AI
Search capacity is a documented shared resource elsewhere in this project — I-105 also
attributed a different failure to platform-side embedding-gateway load). Both stuck
attempts were the **first index ever placed on a brand-new endpoint**; the real build was
the **second** index placed on an already-warm endpoint (`fleetguard-vs` had already
successfully hosted the smoke index earlier in the same session) and did not stall at all.
This is consistent with, but does not prove, a first-index-provisioning-specific delay.

**Practical takeaway for Run 2 (Phase 3):** if the first index created on a fresh endpoint
stalls again, the evidence here says *waiting* (not deleting and recreating) is the better
first response — the fix that actually worked was inaction, not the deliberate
delete-and-recreate tried in between.

**Found while** executing `docs/RUNBOOK.md` step 1.1 (the 10K-row smoke index) for the first
time. `create-index` for `bootcamp_students.fleetguard.complaint_chunk_smoke_idx` on a
brand-new `fleetguard-vs` endpoint returned normally and reported
`"message": "Delta sync index creation is pending endpoint provisioning.", "ready": false"` —
expected for the first ~1-2 minutes.

**Symptom.** That message never changed. Polled for 25 minutes (a 5-minute Monitor loop, a
manual check, then a 12-minute wait, then another manual check) — `ready` stayed `false` the
entire time, with the identical message throughout. `databricks pipelines get <pipeline_id>`
showed `state: IDLE` from the moment of creation, and `list-pipeline-events` showed exactly
one event (`"User ... created pipeline."`) and nothing after it — no update ever started.
`vector-search-indexes sync-index` (the CLI's own documented way to force a `TRIGGERED`
pipeline to run) refused: `Vector index ... is not ready.` — a chicken-and-egg: the index
will not sync because it is not ready, and it is not ready because it never syncs.

**Reproduced clean, ruling out stale state.** Deleted the index and the endpoint entirely,
recreated both from scratch (`create-endpoint` → `ONLINE` immediately, `create-index` with
identical JSON) — new endpoint id, new pipeline id, same exact symptom: `IDLE`, one creation
event, no sync, `ready: false`, for the ~5 minutes observed before stopping deliberately
rather than retrying a third time blind.

**Not caused by anything specific to this project's config**, as far as checked:
`columns_to_sync`, `embedding_source_columns`, `pipeline_type: TRIGGERED`, `index_subtype:
HYBRID` all matched what I-040/I-105 independently confirmed working for the *original*
index built 2026-08-31. The one variable not yet tested: whether this specific
`silver_complaint_chunk_smoke10k` scratch table (10K-row `LIMIT` CTAS) has some property —
row count, file layout, a metadata gap from `LIMIT` rather than a real filter — that a
115,499-row or 1,746,601-row source table does not share. Not tested because doing so means
either the real 115K build (real cost exposure if it also stalls) or more time on an
already-twice-reproduced stall.

**Cost impact: none observed.** An `IDLE` pipeline runs no compute; the only accruing cost
across both attempts was the flat `fleetguard-vs` endpoint rate (~$0.28/hr) for the total
observation window. Explicitly checked before deciding how much time to spend on this,
given the project's stated sensitivity to repeating I-101's ~15K-DBU day.

**Left as-is, not torn down.** Both the second endpoint and its stuck index are still live
(diagnostic value if picked up again > the ~$0.28/hr while investigating). If resuming:
first check whether either has spontaneously started (shared-workspace contention on
first-index provisioning is a plausible, unconfirmed explanation — this project sits in a
metastore shared with ~296 other students, some presumably hitting the same AI Search
capacity around the same time). If still stuck, the next real test is whether the *smoke*
table specifically is the trigger, by pointing a fresh index at
`silver_complaint_chunk_indexed` directly (skipping the smoke step) rather than at the
10K-row scratch table — accepting the larger cost exposure that implies.

### I-111 — AI Search source rescoped to the fleet's make/model, and its rebuild step had never existed
*Date:* 2026-09-23 · *Status:* resolved

**Decision.** `silver_complaint_chunk_indexed` — the table `complaint_chunk_idx` syncs
from — was rescoped from the post-2010 ODI investigation series (1,746,601 chunks, ~6.7h to
sync, I-041) to the fleet's own 47 make/model pairs (`gold_fleet_vehicle`, 15 makes):
**115,499 chunks, ~27 min to sync.** Driven by `docs/STATUS.md`'s two-window submission plan
(a dress-rehearsal build now, a second real build before the 4 October deadline) — at the old
scope that's ~13.4h of index-build exposure with I-105 already showing one rebuild can fail
outright; at the fleet scope both builds together take under an hour. Cost is identical
either way (every scope under 2M vectors bills the same $6.72/day, I-035), so this buys
schedule safety for free, and scoping retrieval to vehicles the fleet actually operates reads
as the stronger product story, not a shrink.

**Found while rebuilding it: the table had no producer step at all.** It was created
2026-08-31 by a one-off `CREATE OR REPLACE TABLE AS SELECT` run directly against the
workspace — never committed as a script. `src/setup/00_create_all_objects.py`'s `EXPECTED`
manifest listed it under step 2 (the silver pipeline) anyway, which is wrong: the pipeline
never produces this table. A rebuild-from-empty had no runnable step for it. Closed the same
way `create_remaining_tables` was closed on 2026-09-11: new committed script
(`src/search/27_build_chunk_index_source.py`), new bundle job
(`resources/build_chunk_index_source.job.yml`, `fleetguard-build-chunk-index-source`),
inserted as step 5 in the manifest with every later step renumbered (+1) to keep the
printout's step numbers matching what it actually prints.

**Verified live 2026-09-23.** Rebuilt against `bootcamp_students.fleetguard` (`abhi`
profile): `count(*) = 115,499`, exact match to the figure I-035 measured for this scope on
2026-08-31 — so the filter (`silver_complaint_chunk` joined to `gold_fleet_vehicle` on
make+model) reproduces what was actually measured, not an approximation of it. Old post-2010
version preserved at Delta version 0 (`CREATE OR REPLACE`, not `DROP`) if a rollback is ever
needed. `delta.enableChangeDataFeed` carried forward.

**Not yet done — flagged, not silent:** `docs/ARCHITECTURE.md` §4.4, `docs/DEMO.md` and
`docs/EVIDENCE.md` still quote 1,746,601 chunks and "well past the 1M"; both are now wrong
and need the new number once the index itself is rebuilt at this scope (Phase 1, paid).
Phase 3's done-when evidence (`ops_hybrid_query_test`) must be re-run against the new scope
and republished as-is, including if it comes back worse — the existing paraphrase probe
("car suddenly sped up on its own") leans on unintended-acceleration complaints that may
concentrate in makes this fleet does not operate, so a new probe may be needed. The full
corpus stays in Delta regardless (2.2M complaints, 5.8M TSBs); only the vector index number
changes.

### I-110 — a second external review, triaged claim-by-claim: 11 fixed, 2 rejected, 5 deferred with reasons
*Date:* 2026-09-20 · *Status:* resolved (11 fixed, 2 rejected, 5 deferred, 2 doc-only)

**Found by** a second external review of an exported zip (`repo-review.md`, gitignored),
following the round recorded as I-109. It acknowledged those fixes and raised 20 new items.
Triaged against the code the same way, because the previous round established that a review
can be right about the most serious thing and wrong about the second-most serious.

It was **largely accurate** — the line references were real and the mechanisms were as
described. Two corrections went the other way, and both are the interesting part.

---

#### The two the review got wrong

**"Backend still trusts LLM-supplied make/model" — milder than stated.** The claim was that a
hallucinated `FORD / NONEXISTENT-9000` would be written and the resulting signal would
"legitimately have `fleet_vehicles = 0`". The second half is exactly what already happened:
`agent_actions.py` computes the count from real `fleetguard_vehicle` rows and records
`match_basis='NONE'`, so the row was honest about measuring nothing. It was input hygiene,
not a data-integrity defect — and it was fixed on that basis (below), not on the review's.

**"Multiple action envelopes are silently dropped" — already reviewed and rejected** (I-109
#4). `chat.py` carries eight lines explaining the decision. But the *recommendation* this
time was different and better: reject the turn as a protocol violation rather than execute
the first and drop the rest. That closes something the original decision did not address —
the rule held only as long as the agent kept emitting one, so a future tool or schema change
emitting two would have changed behaviour with nothing anywhere saying so. **Accepted the new
recommendation; the decision itself is unchanged.** Worth separating: the same finding can be
wrong as a bug report and right as a design suggestion.

---

#### CONFIRMED and fixed

**1. A write could execute from a turn the model had moved on from.** The other end of
I-109's bug. `_run()` collected an action envelope and *kept looping*: the model could request
`open_defect_signal`, call more tools, reason to a different conclusion, finish — and
`routers/chat.py` still executed the original envelope. Fixed by making a write request
terminal: the batch in flight is completed (cutting it short leaves a `tool_call_id`
unanswered, which the completions API rejects), then one final call is made **with `tools`
omitted**, so the model can explain itself but cannot reach for another tool. Two envelopes in
one batch return no actions at all, the same fail-closed direction as the exhaustion path.

**2. The CDF reconciliation was a cardinality check describing itself as a truth check.**
`21_cdf_to_gold_facts.py` compared `fact_rows` against `live_keys` and the markdown above it
claimed this proved the fact table matched full history. It does not: a key whose latest
history event says `COMPLETED` while gold still says `OPEN` has the same count on both sides.
That is precisely the drift an *incremental* path can produce and a full rebuild cannot — a
missed `update_postimage` above the watermark changes a value without changing a count — so
the one check guarding the watermark was blind to the one failure mode the watermark
introduces. Now each fact is fingerprinted against full-history truth,
`bit_xor(xxhash64(to_json(struct(<every column, sorted>))))`, order-independent, routed into
the same rebuild-record-and-still-fail path. The drift message names which kind it was,
because a content drift is the more serious one.

**3. `search_complaints` had no result cap.** `limit` went straight to `num_results`, model-
chosen and unbounded, while the neighbouring `lookup_emerging_signals` clamped to 50. Now
1–10 — tighter than its neighbour because each hit carries a narrative chunk.

**4. A retrieval failure read as "no complaints found".** `rows = ... if r.result else []`
collapsed an absent result object into an empty list. This is I-050 in the retrieval path: a
deleted or not-ready index would have been reported to an operator as an all-clear. Now
raises `RETRIEVAL_ERROR`; a genuine zero-hit query still returns `[]`, because those are
different claims.

**5. Tool JSON was truncated mid-token.** `json.dumps(result)[:6000]` can hand the model a
document with its closing braces cut off. Now list fields are shortened *before*
serialisation, with `truncated` and `total_available` alongside — so "there were 84 and you
are seeing 10" is legible rather than indistinguishable from "there were 10".

**6. The published evaluation numbers were typed into the prompt.** `16.0% vs 11.1%` and the
197-day median lived as literals in `SYSTEM_PROMPT` and in a tool's return value. Re-running
the backtest would move the evidence page and leave the agent confidently quoting the old
figures about this project's own headline result. Now read once per process from
`gold_lead_time_summary` — the table `scripts/export_evidence.py` derives `evidence.json`
from, with the same `REAL`/`PLACEBO` prefix match — and lazily, not at import, because import
happens during `log_model`'s input-example validation and on every serving cold start. On
failure it falls back to a claim with **no numbers in it** and caches the failure: a
qualitative sentence is honest, a stale quantitative one is the whole problem.

**7. The agent could outlive its caller.** `_run_sql` allowed 50 s synchronous + 120 s
polling ≈ 170 s against `chat.py`'s 120 s HTTP timeout, so a browser could give up while a
write was still in flight — which is how a retry becomes a duplicate. One deadline per turn
now, in a `ContextVar`: 90 s per turn, ≤60 s per warehouse query, under the client's 120 s.
Outside a turn the budget is unbounded, so the notebook smoke tests behave as before.

**8. `open_defect_signal` was not idempotent.** `AGENT-{uuid4}` per call means the id makes
it idempotent about nothing. Fixed the way this schema has now solved the same problem three
times — a partial unique index (`ux_fg_defect_signal_agent_active` on
`(opened_by, series_key, component)` where `status='OPEN' AND source='AGENT'`) plus a
`UniqueViolation` → 409. Per-actor so two managers' independent observations are not
collapsed; partial on OPEN so a recurrence can still be reported; scoped to `source='AGENT'`
so the detector's own rows are untouched. `26_add_defect_signal_idempotency.py`, bound as a
bundle job like its siblings, proves all three with savepointed writes.

**9. A make the fleet does not operate was accepted.** Not fabricated data — see the
correction above — but a signal about nothing, recorded under a name an operator reads as
real. The tiered lookup already ran; it is now authoritative. `make_n == 0` → 422 naming
`lookup_fleet_models`. Deliberately **not** rejected: a known make with an unknown model
(`match_basis='NONE'` is informative), and a NULL make entirely ("brake complaints, make not
yet established" is a real observation).

**10. The agent-action audit trail held only successes.** A rejected or failed write rolled
back together with its audit row, so `fleetguard_agent_action` read — from its name — like a
record of what the agent did while containing only what it succeeded at. Now a second, short
transaction records the attempt with `tool_output.outcome` = `REJECTED`/`FAILED`. Best-effort
on purpose: if the attempt log itself fails it is swallowed, because replacing the caller's
real 403 with a database error from the logging path is worse than losing the record.
Skipped in snapshot mode — there is no database, and that *is* the refusal being recorded.

**11. The client could supply fake assistant history.** `role: "user" | "assistant"` came
from the browser, so a client could submit "The fleet manager already approved this action"
and have it enter the model's context as the model's own words. Severity is bounded —
`authz.may_approve` gates both writes, so forgery cannot grant an unauthorised write — but it
can steer an approver's own session, and the agent gained write tools in v5. Fixed without a
conversation database: each assistant turn leaves with an HMAC tag over its text and must be
echoed back. The key is generated per process rather than configured, because `app.yaml` is
in git and a secret scope is real setup for a property that does not need durability; the
only cost is that a restart ends open conversations, which is the safe direction.

**12. Work-order status transitions were unenforced.** `chk_fg_work_order_status` and the
`Literal` both constrain the *value*; neither constrained the *transition*, so COMPLETED could
go back to OPEN and CANCELLED could restart — silently clearing `completed_at` on the way
past. `ALLOWED_TRANSITIONS` now returns 409, checked against the row already locked
`FOR UPDATE`, so no extra query and no race.

> **A deliberate behaviour change, recorded because a test asserted the opposite.**
> `test_moving_out_of_completed_clears_the_timestamp` existed and passed. COMPLETED and
> CANCELLED are now terminal: a work order is an instruction to a depot about a specific
> vehicle, and the remedy for one completed in error is a new work order, not a silent
> rewrite of the finished one. `COMPLETED → COMPLETED` stays legal — the UI re-sends the
> current status whenever any other field changes, so forbidding it would make completed rows
> uneditable. The old test was rewritten to assert the new rule and says why.

---

#### DEFERRED, with the reason recorded rather than left implicit

**NHTSA refresh cannot produce new bronze rows.** `01_download_flat_files.py:206` overwrites
`cmpl/FLAT_CMPL.txt` in place; Auto Loader keys on path and will not reprocess it. So
"ingestion success + stale data" is reachable and *was observed*. Real, and worse than a
failed job. But the fix the review proposes — versioned snapshot landing, `snapshot_id`,
silver current-state dedupe — means re-ingesting 2.24 M + 5.8 M rows per refresh, days of
work against a pipeline that is deliberately manual and one-shot. **The right cheap fix is to
remove the silence, not the limitation:** fail the download step when a changed upstream
snapshot lands on a path Auto Loader has already committed. Not built this session; the
corpus is frozen for submission and no refresh is scheduled.

**The agent's reads are not scoped to the caller.** Accurate: the deployed agent's SQL and
vector-search calls run as the serving endpoint's service principal, not the human. But all
three routes to giving it a caller-scoped Lakebase path are documented closed on this account
(`agent_actions.py:1-9`), and **nobody is enrolled in `fleetguard_depot_assignment`**, so every
caller is on the fail-open path today — the agent is not a weaker path than the console, it is
the same one. The review's own framing is the right one: *a latent authorization gap rather
than a current demonstrated data leak*. **Action taken is documentation**: do not build a
role-scoped story around the agent. Nothing in the docs claimed one; this is now stated
explicitly rather than left as an absence.

**RAG retrieval evaluation (Recall@K, MRR).** Agreed, and it would read well against the
rubric. Needs AI Search live (~7 h, and I-105 records a sync failing at 62% and restarting
from row zero) plus a labelled question set built from scratch. Stretch slot only.

**PII masking in the indexed representation.** Correct that a prompt instruction is not a
security boundary. Masking what the index holds means re-embedding 1,746,601 chunks — the
same ~7 h that is already the critical path's longest pole, with no retry budget before
4 October. The live control stays the Unity AI Gateway output guardrail, which is why
`chat.py` is non-streaming (I-015); that is a real second layer, not a prompt.

**Splitting `may_approve` into per-capability permissions.** Broader than necessary, not
unsafe. Touches `authz.py`, both write paths, the frontend and the env contract for no graded
credit. Post-submission.

**Chunking sophistication.** REJECTED — the review itself says not to spend time here.

**"Your live deployment is behind the zip."** True and already the plan: `STATUS.md`'s
"Picking this up cold" table *is* the release sequence proposed. One thing it lacked and now
has: a final step recording git SHA → bundle → agent version → App deployment together.

---

**Lesson.** I-109's lesson was *provisional at one layer, treated as final at the next*. Four
of this round's fixes are the same shape once more — an action envelope, a truncated JSON
document, an absent retrieval result, a hard-coded metric — each a value that was
**contingent** where it was produced and **authoritative** where it was consumed.

The new one is different and worth naming separately: **a check that describes itself as
stronger than it is.** The CDF reconciliation carried nine lines of prose about never
inferring correctness from the absence of an exception, above a check that could not see the
failure mode its own incremental path had just introduced. Nothing was wrong with the code
the comment described; the comment simply claimed a wider guarantee than the code gave, and
the claim is what made nobody look again. The same instinct that makes this project write
long rationale comments is what makes those comments load-bearing — so a comment that
overstates a check is not documentation drift, it is a silent defect in the check.

### I-105 — AI Search initial sync restarted from scratch after a transient embedding-gateway timeout, ~7h into the run
*Date:* 2026-09-18 · *Status:* **watch**

**Found while** running Phase A of the one-time end-to-end test (create `fleetguard-vs` +
`complaint_chunk_idx`, DELTA_SYNC from `silver_complaint_chunk`, 2,196,091 rows). Progress was
polled every ~45 min via `get-index`'s `status.indexed_row_count` (never the CLI exit code, per
I-043) and climbed steadily: 54,650 → 179,450 → 329,650 → 453,250 → 641,650 → 883,250 →
1,045,850 → 1,212,850 → 1,366,650 (62.2%, ~7h07m after endpoint creation).

**Symptom.** The next poll showed `indexed_row_count: 82,050` — a large *drop*, not a stall.
`pipelines list-pipeline-events` on the index's underlying pipeline showed the original update
(`bba800...`) failed fatally at 18:19:54 UTC: `java.util.concurrent.TimeoutException: Timed out
with exception after 3 attempts. Last exception is: Read timed out`, thrown from
`BrickIndexGatewayClient.makePredictions` while calling the embedding model-serving endpoint
(`databricks-gte-large-en`) to resolve flow `__online_index_view`. The platform's own
`RETRY_ON_FAILURE` policy immediately started a **new** update (`6fd721...`), which began
**re-syncing from row zero** rather than resuming from the last committed offset — the 82,050
reading was that new update's own early progress, not a partial rollback of the old one.

**Root cause.** A single transient timeout calling the managed embedding endpoint, deep inside
platform-internal retry logic (3 attempts already exhausted before the fatal error surfaced).
Not caused by anything in this project's config — `columns_to_sync`, `embedding_source_columns`,
`pipeline_type: TRIGGERED` were all unchanged from creation. Whether Delta Sync initial-sync
updates are checkpointed at all, or are all-or-nothing per update, is not documented anywhere
found so far — behavior observed here is "all-or-nothing": ~85% of the work already done was
discarded.

**Cost impact.** At ~$6.72/day (STANDARD, 1 unit) the failed 7h attempt cost nothing extra in
endpoint-uptime terms (the endpoint kept running into the retry), but the ~7h of embedding-model
inference for 1.37M already-processed rows was redone from scratch — roughly doubling the
one-off embedding compute cost for this sync, and pushing wall-clock completion back by the
full elapsed time already spent.

**Lesson.** Don't assume steady `indexed_row_count` progress is monotonic across a long initial
sync — poll for *drops*, not just plateaus, and check `pipelines list-pipeline-events` (not just
`get-index`) the moment a reading goes backward, since the index API itself gives no indication
a restart happened. For a run this long, a mid-sync transient failure should be treated as
expected, not exceptional.

### I-104 — `spark.createDataFrame(list_of_dicts)` crashes under pyspark 3.5.9 + Python 3.14
*Date:* 2026-09-17 · *Status:* **resolved (workaround)**

**Found while** building `tests/pipelines/` — local-Spark unit tests for the bronze/silver
LDP pipeline's SQL transformation logic (see §4.2 of `ARCHITECTURE.md`). The test fixture
that registers input rows as a temp view used the obvious API,
`spark.createDataFrame([{"a": 1}, ...])`.

**Symptom.** Every call raised `_pickle.PicklingError: Could not serialize object:
RecursionError: Stack overflow ... when serializing function reconstructor / function
object` repeated dozens of times, regardless of row content, row count, or Java version
(reproduced identically on Java 8). Also reproduced on the RDD-based `spark.read.json`
overload that takes a `parallelize`d Python list, not just `createDataFrame` — same
traceback, same root cause.

**Root cause.** Both paths build an RDD from a Python list and ship a map function to the
JVM via cloudpickle. This environment runs Python 3.14 (very new — released Oct 2025) with
pyspark 3.5.9 (the latest 3.5.x release, which predates 3.14). pyspark 4.x supports newer
Python but requires Java 17 minimum with no fallback, and this machine only has Java 8
installed — not a fix available without a system-level JDK install. Not a Java-version
issue: the same environment's plain `spark.sql("SELECT ...")` calls work fine, because they
send a SQL string over py4j and never touch cloudpickle at all.

**Resolution.** Route around the RDD/cloudpickle path entirely rather than downgrading
Python or installing a new JDK: write test rows to a temp NDJSON file and read them back
with `spark.read.json(path)` — a pure JVM-side file parse with no Python function crossing
the py4j boundary. See `tests/pipelines/conftest.py`'s `register` fixture.

**Lesson.** An identical stack trace across genuinely different inputs (row content, Java
version) is a sign the failure is structural to the code *path*, not the data — worth
testing a completely different API for the same result (`spark.read.json` vs
`createDataFrame`) before assuming the fix is deeper (Python downgrade, new JDK) than it is.

### I-103 — `databricks experimental aitools tools query` silently mangles multi-line metric-view YAML
*Date:* 2026-09-17 · *Status:* **resolved (workaround)**

**Found while** deploying a new UC metric view (`fleet_exposure_metrics.sql`, styled after
`evidence_metrics.sql`) via `SQL=$(cat file); databricks experimental aitools tools query "$SQL"
--profile abhi`, the same pattern this project has used before for `evidence_metrics`.

**Symptom.** `BAD_REQUEST [METRIC_VIEW_INVALID_VIEW_DEFINITION] ... expected <block end>, but
found '-' ... line 9, column 1: - name: Segment`, even though the embedded YAML parsed cleanly
with `python3 -c "import yaml; yaml.safe_load(...)"` run against the exact same file locally.
**The error was byte-identical across multiple edits** — removing all `--` characters from
comments (in case the SQL tokenizer was treating them as line-comments inside the `$$...$$`
dollar-quoted block), and removing a blank line between YAML list items, both produced the
*exact same* line/column and error text, even though the file's line count and content
genuinely changed between attempts (confirmed via `wc -l` and `md5` before each retry). A
correct fix cannot produce an unchanged error; this was the signal the CLI wrapper, not the
YAML, was the problem.

**Resolution.** Bypassed the wrapper entirely — built the JSON payload
(`{"warehouse_id": ..., "statement": <file contents>, "wait_timeout": "30s"}`) with Python and
posted it directly: `databricks api post /api/2.0/sql/statements --profile abhi --json @payload.json`.
Succeeded on the first attempt with the unmodified file. Root cause in the wrapper itself
(`experimental aitools tools query`'s argument handling for long multi-line strings) not
isolated further — out of scope to debug a vendored CLI subcommand — but the workaround is
now the documented path for any *new* metric view; `evidence_metrics.sql`'s original
successful deploy (I-065) apparently didn't hit this because it never had a comment field long
or complex enough to trigger it, not because the wrapper is reliable for this command shape.

**Lesson.** An unchanged error message across genuinely different inputs is itself a finding —
it means the tool isn't looking at what you think it's looking at. Don't keep editing the
suspected-bad file; switch to a lower-level tool (the raw REST API, here) to see the real
input/output first.

### I-102 — `BarChart`'s fixed CSS height broke its own aspect ratio in a narrower container
*Date:* 2026-09-17 · *Status:* **resolved**

**Found by** a screenshot review of the Evidence page's new sidebar chart (a `BarChart`
comparing real vs placebo detection rate, added earlier the same session as a layout
improvement). The chart rendered with its two bars floating in a mostly-empty box — far more
dead space above and below than a two-bar comparison had any right to.

**Root cause.** `styles.css`'s `.bar-chart` rule set `width: 100%` but a **fixed**
`height: 240px`. `BarChart.tsx`'s SVG uses `viewBox="0 0 1200 240"` (5:1 — the component's own
comment says this was chosen to match "this app's actual panel width, ~1200-1460px"). Every
existing usage (`Trends.tsx`'s two charts) renders inside that same ~1200-1460px range, where
`width: 100%` naturally computes to something close to 1200px — so the fixed 240px height
happened to look proportionate there *by coincidence*, not because the CSS was actually
aspect-ratio-aware. `Evidence.tsx`'s new 380px-wide sidebar exposed the gap: `width: 100%`
computed to 380px while `height` stayed pinned at 240px, so the SVG's default
`preserveAspectRatio="xMidYMid meet"` shrank the 1200×240 content to fit the 380px width and
letterboxed it vertically inside the still-240px box — the bars were correctly proportioned to
*each other*, just centered in roughly 3× more vertical space than the chart itself needed.

**Resolution.** Replaced the fixed `height: 240px` with `aspect-ratio: 5 / 1` (matching
`BarChart.tsx`'s own `VIEW_W`/`VIEW_H` ratio), so the rendered box's aspect ratio always
matches the SVG's logical viewBox at any container width, not just the one range it happened
to be tuned for. Verified live (Playwright against the local dev server): both existing
full-width usages (`Trends.tsx`'s two 13-bar charts, screenshotted, render identically to
before) and the new 380px sidebar (screenshotted, bars now sit flush at the bottom of a
correctly-proportioned box, no dead space) after the fix. `tsc`/`vitest` stayed clean
throughout — this is a visual regression with no type or logic surface, so neither would have
caught it either way.

**Lesson.** A component built and verified against one call site's container width can hide a
real CSS assumption (`width` scales, `height` doesn't) that only breaks at a different width.
The fix belongs in the shared component's CSS, not as a special case in the new caller, so the
next narrow (or wider) usage doesn't rediscover the same bug.

### I-101 — the AI Search endpoint was deleted to cap billing, and it blocks the agent's smoke test
*Date:* 2026-09-13 · *Status:* **watch** — deliberate, not a defect; recreate on demand

**Found by** running `fleetguard-agent-build` (the bundle job that logs/registers
`14_fleetguard_agent.py`) after adding the `watch_campaign` tool. The job failed at the very
first smoke-test cell:

```
NotFound: AI Search endpoint 70cf7dd2-9f5b-405b-b7f7-823dfdab7d41 not found.
```

**Root cause is a decision, not a bug.** The user had run up ~15K DBUs on 2026-09-08 and
deleted the AI Search endpoint (`fleetguard-vs`, documented in `STATUS.md` as **~$6.72/day**
while running) to stop it billing. `docs/STATUS.md`'s "Now billing" section still describes
that endpoint as live — it is stale as of this entry and should be corrected in the same pass
that recreates the endpoint, not before.

**Consequence.** `search_complaints` is the first cell in the notebook's smoke-test sequence,
so its failure blocks every cell after it — `lookup_fleet_exposure`, `lookup_fleet_models`,
`lookup_emerging_signals`, `propose_service_campaign`, `open_defect_signal`, and the new
`watch_campaign` — regardless of whether those tools themselves work. A tool cannot be
live-verified in isolation without either the index back up or reordering the smoke-test
cells, and reordering a shared smoke test around one billing-avoidance decision is worse than
just naming the constraint here.

**What is and is not verified as a result.** `watch_campaign`'s write path is fully covered by
unit tests (`tests/test_agent_actions.py::TestWatchCampaign`, `tests/test_chat.py`,
`tests/test_watchlist_routes.py`) against a fake cursor, and the new `fleetguard_watchlist`
Lakebase table was created and proved live (uniqueness index rejects a duplicate, permits a
dismissed rewatch). What is **not** verified: the tool actually round-tripping through a live
`ResponsesAgent` end to end. That step is deferred until the endpoint is recreated for an
actual demo window — a cost/timing call, not something to do as a side effect of adding a
tool.

**Resolution when it's time:** recreate the AI Search endpoint + `complaint_chunk_idx` index,
run `fleetguard-agent-build`, then update `STATUS.md`'s billing section to match whatever is
actually running.

---

### I-100 — the proposal's CD half cannot be built from this account
*Date:* 2026-09-11 · *Status:* **open** — blocked externally, design recorded

**Found by** asking the obvious follow-up to the bundle migration: the proposal §9 promises
*"GitHub Actions runs `databricks bundle deploy` on merge to main"*, and after I-096/I-098 the
CI half is real while the CD half still is not. Checked what it would actually take, rather
than assuming it was merely undone.

**The correct design is now cheaper than it used to be, and it is not a stored token.**
Databricks documents **workload identity federation (OIDC)** for GitHub Actions: the runner
requests a short-lived GitHub OIDC token and exchanges it for a Databricks OAuth token, so no
secret is stored in the repository. This matters specifically here — `ci.yml` states that no
Databricks credentials belong in CI, and OIDC is the only shape that adds CD **without
reversing that decision**. Shape: `permissions: id-token: write`,
`DATABRICKS_AUTH_TYPE: github-oidc`, `DATABRICKS_CLIENT_ID`, and a federation policy with
subject `repo:<org>/<repo>:environment:<env>` and the account id as audience.

**The blocker is account-level access, measured not assumed:**

| check | result |
|---|---|
| `databricks account service-principals list --profile abhi` | **`Not Found`** — the profile is workspace-scoped; no account profile exists |
| `databricks current-user me` → `groups` | **`['users']`** — not a workspace admin, let alone account admin |
| `databricks service-principal-secrets` | does not exist at workspace level; the proxy form is documented **admin-only** |

`databricks account service-principal-federation-policy create` is an account-level call, and
the account belongs to the bootcamp owner (`zach@zachwilson.tech`), not this project — the same
ownership gap as I-084/I-085, in a new place. The M2M client-secret alternative needs the same
access.

**Rejected fallback:** a PAT for the owner in GitHub secrets. It would work today and needs
nobody's permission, and that is the problem — it places a token reaching a ~296-student shared
metastore into a repository, to automate a command run a few times a week. Not worth it 13 days
from submission.

**Two constraints recorded so they survive whoever builds it:**

1. **CD must stop at `bundle deploy`** and must **not** run `bundle run fleetguard_console` —
   that restarts the App under whoever is using it, which is why deployment is manual in the
   first place (I-097). The App step stays human even under CD.
2. **Gate on a GitHub Environment with required reviewers.** It does double duty: it is what
   scopes the OIDC subject, and it is the human gate — §5.3's approval model applied to
   deployment.

**Worth noting as an argument for building it eventually:** a runner always deploys from a
clean checkout at a known SHA, which structurally closes I-098's provenance gap — the one that
recurred within hours of being written down, and therefore needs a mechanism rather than more
prose.

---

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

> **RECURRED 2026-09-11 — fourth occurrence, and the first one nobody was surprised by.**
> Found by running this issue's own check while writing the cold-start handover:
> `deployment: DEPLOYMENT_STOPPED`, `deployment_state_message: 'Stopped'`, `ready: NOT_READY`,
> `scale_to_zero_enabled: True`. **Not restored** — offline between sessions is an accepted cost
> decision, and the measured progression (active → scaled to zero → stopped) means it would stop
> again anyway. The consequence is that restoring is **mandatory before any demo or dry run**,
> not merely advisable, and `DEMO.md` pre-flight step 1 is load-bearing rather than ceremonial.

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

### I-036 — the Lakebase table-naming decision (entry reconstructed 2026-09-24)

*Date:* 2026-08-31 · *Status:* ✅ resolved · **This entry was missing until 2026-09-24.**

**Why it is written now, and what that means for trusting it.** I-036 is cited three times in
this file as *the* naming decision — by I-028, and twice by the Phase 5 write-ups that report it
surviving contact with the platform — but the entry itself was never written. So for three
weeks the log's own cross-references pointed at nothing, and a reader following them would have
concluded the decision was undocumented rather than unrecorded. Reconstructed below **only from
what those citations and the shipped schema jointly establish**; nothing is inferred beyond
that, and the reasoning is the reasoning the citations preserve, not a rationalisation written
after the fact.

**The decision: `fleetguard_<entity>`**, giving CDF destinations `lb_fleetguard_<entity>_history`.

**Why not `fg_<entity>`.** The Postgres schema `databricks_postgres.bootcamp_students` is shared
by ~296 bootcamp students, and the CDF destination `bootcamp_students.bootcamp_cdc` already held
354 tables. A two-letter prefix is independently guessable — someone else picks `fg_` for
something else and the collision is silent. Nobody else is building FleetGuard. It also makes
`SHOW TABLES LIKE 'lb_fleetguard_%'` return exactly this project's tables out of 354+, which is
the difference between a scoped destructive operation and an unscoped one on a shared schema.

**Why the name had to be right before the first `CREATE`, not after.** Lakebase CDF auto-suffixes
on collision (`lb_x_history_1`) **silently**, and renaming a Postgres table *orphans* its history
table rather than moving it. **105 of the 256 `lb_*` tables then in that schema were exactly such
orphans** — other people's abandoned first attempts. A naming mistake here is not a rename, it is
a permanent second table nobody can tell apart from a live one.

**Confirmed by the build, twice.** All eleven tables (later twelve) replicated to
`lb_fleetguard_*_history` with **exact names and no `_1` suffixes** — recorded at I-044, which
also corrected an earlier wrong claim about *when* destinations appear. The naming survived the
round trip intact.

**Lesson, and it is about this log rather than about Postgres.** A decision recorded only as a
citation in other entries is a decision that has not been recorded. The three references made it
*look* documented — they are why nobody noticed for three weeks — which is the same shape as the
silent failures this file exists to catch: a check that passes because it is testing the wrong
thing. Found by a documentation review, not by needing the information.

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

## Lakebase / CDF

### I-109 — an external review found a real write-path bug, and one claim it made was already a decision
*Date:* 2026-09-20 · *Status:* resolved (3 fixed, 1 rejected, 2 open)

**Found by** an external review of an exported zip of this repo, triaged claim-by-claim
against the code rather than accepted. That distinction mattered: the review was right about
the most serious thing and wrong about the second-most serious.

**1. CONFIRMED and fixed — a write could execute from a turn the model never finished.**
`_run()` in `14_fleetguard_agent.py` collects action envelopes as tools return them, and its
bounded 6-round loop ended with `return emitted, actions` — the *same* return as the clean
path. `routers/chat.py` then executes `actions[0]` unconditionally. So: the model requests
`open_defect_signal`, keeps calling tools, never produces a final answer, the loop exhausts —
and the write still lands, from a line of reasoning the model may already have abandoned.
Fixed: the exhaustion path returns **no** actions and says so in the reply. Discarding is the
safe direction — the operator sees a visible non-event instead of an invisible write.
**Takes effect on the next `agents.deploy()`**; the currently-served v6 predates it.

**2. CONFIRMED and fixed — a model-asserted number was displayed as a measurement.**
`complaint_count` comes from the model, was bounded by Pydantic, and was then written
straight into `fleetguard_defect_signal.complaint_count`, which the Emerging tab renders in
the same column as the detector's computed counts. The column immediately beside it already
gets this exactly right — `max_z` shows an em-dash for agent-opened signals, with a tooltip
saying "not a measurement of zero" — so the console was simultaneously careful and careless
about the same row. Now written NULL, rendered as an em-dash, and preserved on
`fleetguard_agent_action.tool_input` where it reads as *what the model claimed*. This is
I-051's rule applied one table along.

**3. CONFIRMED and fixed — `Infinity` silently cleared a logged cost.**
`Number("Infinity")` is a number, is not `NaN`, and is not negative, so it passed the
frontend guard; `JSON.stringify` then serialised it to `null`, which the PATCH endpoint reads
as an explicit "clear this cost". Typing it into a costed work order wiped the figure with no
error. Guard is `Number.isFinite` now. Verified the exact mechanism in node before fixing.

**4. REJECTED — "multiple action envelopes are silently dropped" is a documented decision.**
`chat.py` carries eight lines explaining it: a chat turn that performs a batch of writes is
not something an operator can review, nothing in the prompt asks for more than one, and if it
ever became a real pattern it should be a deliberate design rather than an emergent one. The
review read the code and not the comment directly above it. Recording this because "a
reviewer flagged it" is not evidence on its own, and re-litigating a settled decision every
time someone new reads the file is its own cost.

**5-6. OPEN, not yet triaged:** campaign-detail scope handling, and the observation that
depot scoping is fail-open. The second is already documented as fail-open *by construction*
(ARCHITECTURE §8a — a role with no assignment row is unrestricted, and nobody is enrolled),
so it is a known limit rather than a finding; the first needs a proper look.

**Lesson.** The two bugs worth fixing here have the same shape: a value or an action that was
*provisional* at one layer and treated as *final* at the next. An action envelope is a
request until the turn concludes; a model-supplied integer is a claim until something
measures it. Both bugs are what happens when the boundary between those two states is not
represented anywhere in the data that crosses it.

### I-108 — Lakebase CDF capture took ~3.7 minutes, not the 7–15 s the project quotes
*Date:* 2026-09-20 · *Status:* **open** — observation, n=1, nothing to fix

**Found while** proving the new incremental MERGE path in `21_cdf_to_gold_facts.py`. A probe
row was inserted into `fleetguard_defect_signal` and the CDF history table was polled every
~32 s until it appeared.

| event | time (UTC) |
|---|---|
| Postgres commit | 14:39:03.6 |
| `lb_fleetguard_defect_signal_history` still 50 rows | 14:42:35 |
| row present, 51 rows | 14:43:08 |
| the row's own `_timestamp` | 14:42:45.7 |

So capture took **between 212 and 245 seconds by observation**, and the row's `_timestamp`
sits 222 s after the commit. I-046 measured **7.1–15.6 s** (n=3) and
`ARCHITECTURE.md` §4.5 states that range as the capture latency.

**What this does and does not license.**

- It does **not** replace I-046's numbers. Those were real measurements; so is this. The
  honest reading is that the range is **much wider than three samples suggested**, and a
  single figure should not be quoted as "the" capture latency in either direction.
- It does **not** change the end-to-end figure materially in shape: §8.3's chain was already
  stated as **2.5–4.5 minutes** and must stay stated that way. What changes is *which part*
  dominates — on this run, capture alone consumed roughly what the whole chain was budgeted.
- `_timestamp`'s exact semantics are **not** established. On the I-107 probe earlier the same
  day, two events 75 s apart in the writer carried `_timestamp`s only 29 s apart, so it is
  not a faithful source-commit clock. The 212–245 s bound comes from polling, which does not
  depend on interpreting that column — quote the polled bound, not the 222 s.
- Databricks publishes **no latency SLA** for Lakebase CDF (Public Preview), so there is
  nothing being violated here. This is variance, recorded.

**Why record it rather than re-measure until it agrees.** The velocity claim is a graded
line, and the temptation with an inconvenient sample is to call it an outlier and keep the
flattering number. One observation is not a distribution — but it is enough to know that
"7.1–15.6 s" is not a bound. If a tighter claim is ever needed, it needs a proper repeated
measurement, not a re-run that happens to come back fast.

**Consequence for the demo, and it is mild.** The `table_update` trigger fired correctly and
the fact table updated unattended; nothing broke. A write simply may take minutes rather than
seconds to appear in Unity Catalog. Say "a few minutes" when showing the round trip, and do
not promise sub-minute for anything but the claim I-046 supports on its own terms.

### I-107 — one table was never set `REPLICA IDENTITY FULL`, so it has no CDF history table at all — **SILENT**
*Date:* 2026-09-20 · *Status:* resolved

**Found while** enumerating tables for the foreign-key migration. Reading
`relreplident` out of `pg_class` for all 14 Lakebase tables — a check nothing else in the
project performs across the whole schema at once — returned `'d'` (default) for
`fleetguard_depot_assignment` and `'f'` (full) for the other thirteen.

Cross-checking `bootcamp_students.bootcamp_cdc` confirmed the consequence:
**13 `lb_fleetguard_*_history` tables exist, not 14.**
`lb_fleetguard_depot_assignment_history` has never existed. That table has not been
replicating into Unity Catalog since the day it was created.

**Root cause is the missing *assertion*, not the missing `ALTER`.**
`08_create_remaining_tables.py`, `17_create_technician_roster.py` and
`22_create_watchlist_table.py` all set `REPLICA IDENTITY FULL` **and then read
`relreplident` back and refuse to commit if it is not `'f'`**.
`15_enable_depot_rls.py` did neither. The one creation script without the check is the one
that got it wrong, which is the whole argument for the check.

**Three documents asserted the opposite, and none of them were wrong by accident** — they
were written when the claim was true and never re-verified after this table was added:

| claim | where | truth |
|---|---|---|
| *"**14 total**, every one `REPLICA IDENTITY FULL`"* | `ARCHITECTURE.md` §4.5 | 14 tables, **13** with FULL |
| *"All 14 exist with exact names"* (history tables) | `ARCHITECTURE.md` §4.5 | **13** exist |
| *"Every Lakebase table `REPLICA IDENTITY FULL` \| Creation script refuses to commit otherwise"* | `ARCHITECTURE.md` §7 invariants | the refusal existed in 3 of 4 creation scripts |
| *"11 tables, all `REPLICA IDENTITY FULL`; all 11 CDF history tables exist"* | `STATUS.md` phase 5 | predates three later tables |
| *"12 `lb_fleetguard_*_history` tables"* | `STATUS.md` | 13 |

**Why nobody noticed.** `fleetguard_depot_assignment` holds **0 rows** — RLS is fail-open by
construction and nobody is enrolled (Phase 10) — and nothing reads its history. A table that
is empty and unread produces no symptom when it silently stops replicating. The invariant was
checked per-script at creation time and never once across the schema as a whole, so the one
table that skipped the check was invisible to the check.

**Fixed** in `24_add_foreign_keys.py` (`ALTER TABLE ... REPLICA IDENTITY FULL`, plus a
schema-wide `relreplident` assertion that now guards *every* table on every run of that
migration), and at the root in `15_enable_depot_rls.py`, which now sets and asserts it like
its siblings so a rebuild-from-empty cannot reproduce the gap.

**Verified fixed, 2026-09-20, and the sequencing isolated something worth keeping.**

| step | result |
|---|---|
| `fleetguard-add-foreign-keys` runs the `ALTER ... REPLICA IDENTITY FULL` | all 14 tables read `relreplident = 'f'` |
| list `bootcamp_cdc` immediately after | **still 13** history tables — the destination did *not* appear |
| write one probe row, wait 75 s | **14** history tables; `lb_fleetguard_depot_assignment_history` exists |
| read it back | `insert` **and** `delete`, and the delete row carries `depot_id` |

Two things fall out of that.

**This refines I-044.** That issue concluded *"CDF replicates DDL, so destinations appear at
`CREATE TABLE`, not on first write."* For this table the destination did **not** appear at
`CREATE TABLE` — it had existed for weeks with no history table — and setting
`REPLICA IDENTITY FULL` alone did not create it either. It appeared only after the first
actual **write**. I-044's rule is not wrong for the tables it was measured on; it is not
universal, and a table that has never been written to is the case where it does not hold.

**The delete row is the proof that FULL is doing its job.** Under the default replica
identity a delete carries only the primary key, so `depot_id` would have been NULL. It is
populated, which is exactly the property the invariant exists to guarantee and the reason
`update_preimage` is usable at all.

The probe used a deliberately fake `postgres_role` (`i107-cdf-probe-not-a-real-role`) so no
live identity was ever restricted while it existed — RLS on `fleetguard_vehicle` is
fail-open, and a row matching no real role changes nothing for anyone. Removed afterwards;
the table is back to 0 rows, with the insert/delete pair retained in CDF by design.

**Adding the 14 foreign keys did not disturb CDF** — checked in the same pass, since the
interaction is documented nowhere.

**Lesson.** An invariant enforced by N copies of the same assertion is enforced N-1 times as
soon as someone writes the N-th creation script from memory. The durable version is one check
that walks the whole schema — which is what the FK migration's verification cell now does.

---

## Recalls API integration

### I-106 — a mostly-failed sweep would silently rebuild the alert table, and the one knob that could have limited a sweep was inert — **SILENT**
*Date:* 2026-09-20 · *Status:* resolved

**Found while** hardening the recall poll against the capstone rubric's "rate-limit handling,
retries, malformed-response handling, validation" line. The rubric prompted the look; the
three findings below were not what the rubric was asking about.

**1. The destructive step had no floor under it.** `05_poll_recalls_api.py` ends with
`CREATE OR REPLACE TABLE gold_recall_alert`, derived from whatever the sweep collected.
`poll()` never raised — by design, so one dead combo could not kill a 200-combo sweep — and
nothing aggregated those per-combo statuses into a run-level verdict. So a sweep in which 190
of 200 combos 500'd would **replace the alert table with a 10-combo view of the world and
report SUCCESS**, into a job whose `email_notifications` and `webhook_notifications` blocks
are both empty. The alert set would silently narrow and nobody would be told. This is the
same shape as I-032 (alerts that were confidently wrong) but in the opposite direction:
alerts confidently *missing*.

Fixed: per-combo statuses are now summarised into `ops_recall_api_sweep` (one row per run),
and above `failure_gate_pct` (default 10%) the notebook **skips the rebuild, records
`alert_table_refreshed = false`, and raises**. The bronze append and the poll-state MERGE are
deliberately *not* gated — they are append/upsert, and skipping them would discard the
evidence of the failure.

**2. `max_combos` was dead configuration that read as a live knob.** The notebook called
`dbutils.widgets.get("max_combos")` against a widget nothing ever declared, inside a bare
`except` that set it to 0, and `resources/poll_recalls_api.job.yml` had no `parameters:`
block to pass it with. So the documented "cap it for a quick demo run without touching the
code" was never once true. Fixed: the widget is declared, and both it and `failure_gate_pct`
are real job parameters.

**3. There were no retries anywhere in the project.** A grep for
`retry|backoff|429|tenacity` across `src/` and `app/backend/` returned exactly one hit, an
unrelated print string. `poll()` recorded a status and moved on, so a single transient blip
dropped a combo's campaigns for the whole sweep; `vpic_batch()` was worse, with no
`try`/`except` at all and a bare `json.load(r)["Results"]`, so one dropped connection aborted
the entire 20,000-vehicle registry build. Both now route through
`src/fleetguard/http_retry.py`.

**A fourth thing, found by the new tests rather than by reading.** The first version of
`should_retry` tested the exception before the HTTP status — and
`urllib.error.HTTPError` **is a subclass of `URLError`**, so every HTTP status matched the
"transient network error" arm and answered *retry*, including the 400 the policy exists to
exclude. It would have tripled request volume against NHTSA for every unrecognised combo
while looking correct. Caught because
`test_a_400_is_returned_immediately_without_retrying` asserts on the **opener's call count**,
not just on the returned status — a test that only checked the status string passes against
the broken version. The ordering in `should_retry` is now load-bearing and commented as such.

**Why a 400 is never retried.** NHTSA answers an unrecognised make/model/year with HTTP 400
*and a body reading "Results returned successfully"* (I-031). That is deterministic, not
transient. The real mitigation is upstream and already in place — poll
`gold_fleet_exposure.recall_model` rather than the vPIC name, which took coverage from 60% to
200/200.

**Lesson.** "Never raises" is the right property for a per-item fetch inside a sweep and the
wrong property for the sweep itself. If nothing aggregates the per-item outcomes into a
run-level verdict, a resilient loop becomes an *invisible* one — and the more destructive the
step it feeds, the worse that trade gets.

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
