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
| 1 Ingestion + medallion | ✅ Done — `gold_emerging_cluster` **descoped** (cluster-grained, and there is no clustering); replaced by `gold_emerging_signal` |
| 2 Fleet registry | ✅ Done |
| 3 Chunking + AI Search | ✅ Done — verified at full corpus |
| 4 Model B + golden set | ✅ Done — precision 83.7% / recall 96.3%, real numbers on the evidence page |
| 5 Lakebase + CDF | ✅ Done — loaded and latency-measured |
| 6 OAuth wiring | ⬜ Not started |
| 7 Agent tools + write path | ✅ Done — six tools; the write (`open_defect_signal`) executes in the app under the caller's identity, see §7.1 |
| 8 App + external surface | ⬜ Not started |
| 9 Model A + backtest | ✅ Done — **result is negative**, see §6 |
| 10 Governance | ✅ Visible slice — Postgres RLS on depot scoping, proved live |
| 11 Hardening | ⬜ Not started |
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

**Model A — emerging defect detector.** **Volume anomaly only**, against each series' own
trailing 12-month history, at the grain `(make, model, comp_top)`. The firing rule is
`n >= 5 AND base_months >= 6 AND base_sd > 0 AND z >= 3.0`, and a detection is a **sustained
run** of ≥2 consecutive firing months, dated at the run *nearest* the investigation open date.

> Taking the *earliest* run in the window instead produced a 409-day median that was pure
> artefact — as many detections at the window edge as near the open date. The nearest-run
> rule is load-bearing, not a detail.

**Harm weighting was designed but never built (I-051).** This section previously described a
smoothed severity multiplier with shrinkage toward the component base rate. No such term
exists in the code, and the measured 16.0% / 11.1% result comes from pure volume anomaly.
The rationale for the design still holds — 96.0% of complaints report zero injuries, so an
unsmoothed weight would degenerate into a fatality lookup — and if it is built it belongs as
a *ranking* multiplier on an already-fired run, after which the backtest must be re-run
before the headline can be re-quoted.

**Model A does not use clustering.** See §6.

**Live signals.** `gold_emerging_signal` applies the same rule, with the same thresholds, to
the current corpus and keeps runs still firing at the corpus edge. It is what
`gold_emerging_cluster` was for, at the grain the detector actually uses. `harm_share` on that
table is **descriptive triage only** and is not an input to firing.

**Model B — recall-to-fleet matcher.** Scores the `MODEL_VARIANT` residual that exact matching
misses (725,356 of 989,042 exposure rows, I-030) — ranks ambiguity, never performs the match.
Gradient-boosted, isotonic-calibrated, threshold tuned for recall (target 0.90).

**Golden set is text-derived, not human-labelled.** 765 (campaign, make, model) pairs, labels
from whether the vehicle model appears in NHTSA's own `defect_description` — real regulatory
text, not synthetic (E-08 forbids synthetic labels here specifically). 621 positive, 144
negative, 69 excluded as genuinely ambiguous rather than force-labelled.

**I-060 — the first run leaked and was caught before being reported.** Precision=1.000 at
threshold=1.000 traced to a feature (`model_is_substring_of_recall`) that was 0% by
*construction* of the negative-label rule, not by anything learned. Fixed, retrained.
**Reported: precision 83.7%, recall 96.3%, ROC-AUC 0.925** on a 230-row held-out split —
published on the evidence page (`GET /api/evidence` → `model_b`), per the proposal's own
requirement that precision/recall appear on the application's own page.

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
| The agent can open a defect signal, but **never** reaches `fleetguard_work_order` | Build assertion on `propose_service_campaign`; dispatch is behind `FLEETGUARD_APPROVERS` |
| An agent write is attributed to a **real human**, never a service principal | `agent_actions.execute` refuses (403) a token carrying no identity |
| Fleet counts joining NHTSA and vPIC model strings state their **match tier** | `ActionResult.match_basis`; I-075 |

---

### 7.1 The agent's write path

The agent has six tools; five read, and one — `open_defect_signal` — writes a business
record that appears in the operator's **Emerging** tab beside the batch detector's rows.

**The model does not perform the write, and cannot.** Its serving endpoint has no Postgres
path, and giving it one is closed on this account (all three routes — the `DatabricksLakebase`
resource, a secret-backed service principal, and OBO for Model Serving — were checked and
none are available; see `docs/ISSUES.md`). So the tool returns a deterministic *action
envelope*, the FastAPI app validates it against a Pydantic model, and **the app** executes the
insert through the existing `connect(principal)` path — under the caller's own OBO token.

This is a stronger property than it looks like a workaround for: the write lands as the
signed-in human, so `opened_by` is a genuine identity and Postgres RLS applies to the agent's
write exactly as it does to a click in the UI. Nothing an LLM emits can widen its own reach.
**The Emerging tab shows that identity on the row itself** ("opened by …", `source='AGENT'`
only) — until 2026-09-08 it was recorded correctly and visible only in the Audit log, which
made the property real but unevidenced at the point where it is claimed.

Three consequences the code enforces rather than assumes:

- **The envelope is constructed in Python from the tool's return value, never formatted by
  the model**, and the sentinel must *start* the output item. Prose that merely mentions the
  sentinel is not an envelope.
- **Facts are recomputed, not accepted.** `fleet_vehicles` is counted from real rows; the
  model's own estimate is never persisted. It orders the Emerging tab, so a hallucinated
  count would reorder the operator's page.
- **The model is prompted to say it *requested* a signal, never that it saved one.** The
  committed row is reported by a separate UI element — the one place entitled to claim a
  signal exists. Verified live: the agent said *"I can't confirm it's saved or tracked yet,
  and no signal ID has been assigned to me."*

**The agent can read the fleet's own vocabulary before it writes.** `lookup_fleet_models`
returns the makes and models the fleet actually operates, in the registry's spelling, from
`gold_fleet_vehicle` — the same table Lakebase's `fleetguard_vehicle` is loaded from, so the
strings it hands back are exactly the ones the write path matches on. Without it, every read
tool was keyed by `campaign_id` and the model was asked to name a scope in a vocabulary it
could not inspect: live on 2026-09-08 it offered to widen a RAM 2500 signal to the "Dodge
2500/3500 cluster", and this fleet holds no Dodge at all. That closes the NHTSA-vs-vPIC gap
(I-030) at the source; `ActionResult.match_basis` (I-075) remains the backstop that repairs a
count afterwards and states which tier produced it.

Verified end to end 2026-09-08: user question → complaint retrieval → agent decision →
envelope → app executes under OBO → Lakebase insert (+ audit row + `fleetguard_agent_action`,
one transaction) → CDF → `bootcamp_cdc.lb_fleetguard_defect_signal_history` → Emerging tab.

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

## 8a. Hosting and the auth seam

Two surfaces, one codebase, decided 2026-09-01 (E-12/E-13). **Direction changed 2026-09-08:
the Databricks App is now the primary target and is built; Render is kept but is no longer
the plan of record.**

| Surface | Auth mode | Data source | State |
|---|---|---|---|
| **Databricks App** `fleetguard-console` in `abhi` — *the operator console, primary* | `databricks-apps` — `x-forwarded-access-token` (OBO) | live Lakebase | **Built and verified 2026-09-08**, kept `STOPPED` between sessions (`apps start`, ~2 min). Verified under the **owner's identity only** — see **I-084**, the open risk |
| Local dev — near-term verification surface | `static-dev` — developer's own token | live Lakebase | `scripts/run_local_static_dev.sh`; screenshotted end to end |
| **Render** (free tier) — public evidence + signed-in console | `render-u2m` (PKCE) or `app-login` + snapshot | live Lakebase / snapshot | **Deliberately kept, not deleted.** Zero cost to keep, non-zero to re-add. `app-login` + snapshot needs no Databricks identity at all, making it the fallback if I-084 bites |

**Render ran on a snapshot until 2026-09-03, and no longer does.** The original constraint was
real and enumerated, not a preference: measured 2026-09-02, every machine-credential route was
closed on this account — `service-principals create` → *"only accessible by admins"*;
`tokens list` → *"User does not have permission to use tokens"*; Lakebase roles all
`LAKEBASE_OAUTH_V1` (no password auth, credentials minted from a Databricks token, one-hour
life). The account-level OAuth app that U2M needs required an account admin, which arrived on
2026-09-03 (E-14's blocker), so `render.yaml` now sets `FLEETGUARD_AUTH_MODE=render-u2m` and
`FLEETGUARD_DATA_MODE=lakebase`: the host holds real per-user Databricks credentials rather
than none at all.

`app-login` remains the one provider whose `Principal` carries **no** Databricks token, and
that invariant is still load-bearing wherever it is selected: `build_token_provider` **refuses
to start** unless `FLEETGUARD_DATA_MODE=snapshot`. The two settings cannot drift apart.

**Snapshot mode is dormant, not removed — and is kept tested for that reason.** Nothing in
production selects it today, but it is the fallback for any surface that cannot hold a
credential, so it stays. Verified working end to end 2026-09-07: all eleven read endpoints
return 200 with no Databricks credential present, and `/healthz` reports `data_mode:
snapshot` with the capture date the console labels the data from.

The committed `snapshot.json` covers only **queue, campaign detail and signals** — everything
added later (work orders, service campaigns, cost breakdown, depot risk, recall trend, audit
log, technicians) returns an empty list in snapshot mode. That is the truthful answer for a
read-only surface where nothing has been approved, not a placeholder, and
`tests/test_snapshot_mode.py` pins it so that fabricating plausible data there would have to
break a test first. That file also enumerates every read endpoint explicitly: the failure it
exists to catch is a *new* endpoint omitting its `if snapshot.is_snapshot()` branch, which
would call `connect()` with no credential and 500 the public deployment — confirmed by
deleting one guard and watching it fail.

**Authorization on Render is two-tier.** Signing in grants *read*. Approving requires
membership of `FLEETGUARD_APPROVERS` — signing in proves who you are, not that you may
dispatch work orders against a fleet. That gate is checked unconditionally on
`principal.source`, so it applies to a real-Databricks-token principal exactly as it does to
an app-only one; it used to be skipped for the former, which mattered once the workspace
turned out to be shared with the judging cohort (fixed 2026-09-03,
`tests/test_approval_gate.py`). Where snapshot mode *is* selected, the approval endpoint
returns **501** rather than simulating a write: a plausible service-campaign id for a campaign
that was never created would be a lie told by the safety-critical path.

§5.1's "identity determines both rows and columns, and the frontend cannot bypass it" is a
statement about the **Databricks App** surface, where UC and Postgres enforce it. **Since the
2026-09-03 flip to `render-u2m` + live Lakebase, Render is materially closer to that than the
paragraph here used to claim:** the caller's own Databricks token mints the Lakebase
credential, so Postgres evaluates RLS under their identity, and the write path is genuinely
live rather than disabled over immutable data. What remains app-enforced rather than
database-enforced is `FLEETGUARD_APPROVERS` (an env-var allowlist, not a UC grant) and
`scoping.py`'s depot predicate — the latter backed by real RLS on `fleetguard_vehicle`, though
with nobody currently enrolled in `fleetguard_depot_assignment` every caller is on its
fail-open path in practice (see §10 and I-070).

**The Postgres half of that claim is now real, not aspirational (Phase 10's visible slice,
2026-09-02).** `fleetguard_vehicle` has RLS **enabled and forced** — forced specifically so
the table owner's own connection cannot bypass the policy either, which plain `ENABLE` would
have allowed by default. The policy is additive and fail-open through
`fleetguard_depot_assignment`: no assignment row means unrestricted, exactly as before this
existed; an assignment restricts to that depot, enforced below the application. Proved under
real toggled states — including the join through `fleetguard_vehicle_exposure` the console
actually reads — not trusted on configuration alone (`src/lakebase/15_enable_depot_rls.py`).
**Nobody is currently enrolled**, so every live caller is on the fail-open path in practice;
the mechanism is real, the enrollment is the remaining work, and both halves of that
sentence are said on purpose.

**Free-edition Databricks Apps are not viable.** Free edition supports Apps, but it is a
separate workspace *and* account, and app **resource bindings are workspace-local** — it
cannot bind abhi's Lakebase, warehouse, or serving endpoint. Worse, §5.1's guarantee
requires the signed-in user to *be* an abhi identity so UC evaluates ABAC under their token.
A free-edition user is not, so every query would run as one service principal and the row
filters and column masks would be decorative.

**U2M (Path D) is retired (E-14).** It required a custom OAuth app registered in the
Databricks *account* console; measured 2026-09-02, this account's groups are `['users']`
(not `admins`) and the account API returns `Not Found`, so it cannot be registered. It is
also unnecessary: U2M and OBO both end with the app holding the user's token, and Apps
ingress performs the login for free.

**U2M is revived and live, 2026-09-03, for a window where Databricks Apps is not being
used.** The registration blocker is resolved — an account-admin registered the custom OAuth
app integration U2M needs — closing E-14's stated reason for retiring this path. The code
side of that "retired" decision had undersold how little was actually blocked: the auth
seam (E-13) already had `SessionTokenProvider` built and tested for exactly this shape, so
only the browser-facing half was missing. `auth/databricks_oauth.py` (PKCE, token exchange,
refresh) and `routers/databricks_auth_routes.py` (`/auth/databricks/login` + `/callback`)
now exist, and `render.yaml` on `main` runs `FLEETGUARD_AUTH_MODE=render-u2m` +
`FLEETGUARD_DATA_MODE=lakebase`. They coexist with `app-login`'s GitHub flow rather than
replacing it in code — `/auth/status` reports whichever one the deployment's mode selects
(`routers/auth_routes.py::_active_provider`) — but only one is active per deployment, and
this one now is. **Not yet confirmed:** a real browser completing the login round-trip
against the live deploy (`docs/STATUS.md`'s "Next" list carries the checklist).

**The judges have Databricks identities in this same shared workspace** (confirmed
2026-09-03) — which makes `render-u2m` (and eventually `databricks-apps` OBO) the actually
strong path for them to check the system: they sign in with their own account, and Unity
Catalog / Postgres evaluate access under their genuine identity — §5.1's claim demonstrated,
not simulated. Read access being open to anyone in the shared workspace is therefore the
intended shape, not a leak.

Write access is a different question, and was a real gap until this same session:
`approval.py`'s `FLEETGUARD_APPROVERS` allowlist used to be checked only when
`auth_routes.enabled()` (GitHub/`app-login`) was true, so any principal carrying a real
Databricks token — `render-u2m`, and `databricks-apps` OBO once deployed — skipped it
entirely. That was defensible when "has a Databricks identity here" implied "is a trusted
operator"; it stopped being defensible the moment the workspace turned out to include the
judges and cohort too, since every one of them could then have launched real service
campaigns, not just viewed them. **Fixed 2026-09-03:** the gate now applies unconditionally
— `if not auth_routes.may_approve(approver)`, regardless of `principal.source` — so sign-in
stays open to any workspace identity while approval stays restricted to whoever
`FLEETGUARD_APPROVERS` names. Unset means nobody can approve, on any surface, which is the
same "no unset value silently picks a trust model" rule the auth modes already follow.
Covered by `tests/test_approval_gate.py`, parametrized across all four principal sources.

**One active service campaign per recall, enforced in Postgres (added 2026-09-07, I-063).**
Approving the same recall twice used to create a second campaign and a second work order per
exposed vehicle — observed for real in the 2026-09-04 test-data cleanup, which found six
campaigns for one recall. The second approval now returns **409**, naming the campaign that
already exists so the operator can go look at it.

The enforcement point is a partial unique index — `ux_fg_service_campaign_active ON
fleetguard_service_campaign (campaign_id) WHERE status = 'LAUNCHED'`
(`src/lakebase/19_add_service_campaign_uniqueness.py`) — **not** the handler's `SELECT`. That
distinction is the whole design: the realistic trigger is a double-click, two requests
milliseconds apart, and a check-then-insert in application code is a TOCTOU race that both
requests win under READ COMMITTED. The handler check exists for the error message; the index
is what serialises them, and `approve_campaign` catches the resulting `UniqueViolation` and
returns the same 409. Verified with five concurrent approvals: exactly one 201, four 409s,
one campaign.

Scoped to `LAUNCHED` rather than all rows so a cancelled campaign does not block relaunching
the same recall — a real workflow. `campaign_id` is nullable (a campaign can be raised from a
`signal_id`), and Postgres permits repeated NULLs in a unique index, so signal-driven
campaigns are correctly unaffected.

`UniqueViolation` is re-exported from `db.py` rather than imported from `psycopg` in the
router. A bare `import psycopg` in a router sorts into the third-party block *above* the
`from ..db import ...` line, so it would execute before `db.py`'s `_select_psycopg_impl()` and
silently defeat the I-045 FIPS workaround on Databricks serverless.

**Work orders don't end at `OPEN` (added 2026-09-04).** `approve_campaign` always created one
`fleetguard_work_order` row per exposed vehicle, but until this session nothing ever read or
updated them again — the console had no way to show whether a vehicle was actually fixed.
`routers/work_orders.py` adds `GET /api/work-orders` (filterable by campaign/depot/status) and
`PATCH /api/work-orders/{id}`, gated by the same `FLEETGUARD_APPROVERS` allowlist as approval
for the same reason: marking a safety recall "completed" when it wasn't is a compliance risk,
not casual data entry. `fleetguard_work_order.status` gained a real `CHECK` constraint the same
day (previously bare `TEXT`, only `'OPEN'` ever written) — `src/lakebase/
16_add_work_order_status_check.py`, proven against a live rejected write, not just configured.

**Assignment is a real roster, not free text.** `fleetguard_technician` (`src/lakebase/
17_create_technician_roster.py`, 120 rows seeded against the actual live depot list, ~2 per
depot) backs the `PATCH` endpoint's optional `assigned_to` field. The handler validates that
the chosen technician belongs to the *same depot* as the work order — server-side, not a UI
filter, matching the rule everywhere else in this codebase that the frontend never enforces
anything the backend can enforce itself. `assigned_to: null` is a deliberate unassign, distinct
from omitting the field entirely (leave unchanged); `WorkOrderUpdate.model_fields_set` is what
tells the two apart, since a Pydantic default and an explicit `null` are otherwise
indistinguishable. Both status changes and (re)assignments get their own `fleetguard_audit_log`
row (`STATUS_CHANGE` / `ASSIGNED`) with real `before_state`/`after_state` — the first live use
of `before_state`, which existed in the schema since Phase 7 but had never been populated.

**Launched campaigns have a persistent home (added 2026-09-04).** `GET /api/service-campaigns`
(`routers/approval.py`) existed since the approval gate itself but had no consumer — the only
way to see what had been launched was to re-query Lakebase by hand. It now returns a typed
`ServiceCampaignOut` per row (was a bare `list[dict]`) with a per-status work-order breakdown
(`open_count`/`in_progress_count`/`completed_count`/`cancelled_count`) computed via `FILTER`
clauses over the same join `list_work_orders` uses, so a launched-but-untouched campaign and a
fully-closed-out one read differently at a glance — that distinction did not exist before
work-order status tracking landed. The new `ServiceCampaigns.tsx` view (nav tab "Launched") is
its first consumer; clicking a row opens `WorkOrders.tsx` pre-filtered to that
`service_campaign_id` via a new optional route segment (`#/work-orders/<id>`), with a "Clear
filter" affordance to return to the unfiltered list. No new gate — this is a read endpoint
under the same `CurrentPrincipal` requirement as every other fleet-data read, not a write.

**Cost tracking is logged and summed, not assumed (added 2026-09-04, revised same day).** A
first version of this feature multiplied one editable "$ assumed cost per vehicle" input by the
completed-work-order count — flagged in review as still wrong even with the assumption made
visible: different repairs cost different amounts (a steering-rack repair on a Class 8 tractor
and a brake job on a pickup are not the same cost), and a single blended multiplier can't
represent that regardless of how honestly it's labeled. It was replaced same-day with real
per-work-order cost capture:

- `fleetguard_work_order.actual_cost` (`NUMERIC(10,2)`, nullable, `CHECK (actual_cost >= 0)`) —
  `src/lakebase/18_add_work_order_actual_cost.py`. Nullable because cost is logged manually and
  will lag completion; every aggregate below reports `costed_count` alongside a total specifically
  so a partial-coverage total is never presented as if it were complete.
- `PATCH /api/work-orders/{id}` accepts `actual_cost` as a third independent field alongside
  `status`/`assigned_to`, same `model_fields_set` omitted-vs-explicit-null handling, same
  `FLEETGUARD_APPROVERS` gate, its own `COST_LOGGED` audit-log action. A negative value is
  rejected with a clean 400 before it can reach the live CHECK constraint.
- `GET /api/service-campaigns` gained `total_actual_cost`/`costed_count` per campaign (summed
  via the same `LEFT JOIN ... GROUP BY` the status breakdown already used).
- New `GET /api/cost-breakdown` groups logged cost two ways — by `component` (joining
  `work_order → service_campaign → recall_campaign`) and by `depot_id` — specifically so a
  reader can see "STEERING repairs cost more than BRAKES" or "this depot runs above the fleet
  average for the same repair" instead of one number that erases both distinctions.
- `ServiceCampaigns.tsx`'s panel states the measured lead-time context (still using the Evidence
  page's own `real.median_lead_days`, still careful to say "before an investigation would open,"
  not before a recall or an incident — conflating those was an earlier mistake in this project's
  proposal draft, see the three-intervals warning in CLAUDE.md) alongside the real logged-cost
  total and its coverage, with the by-component/by-depot tables beneath it. No dollar figure
  anywhere in this feature is now assumed — every one is a sum of what someone actually entered.

**Audit log has a consumer (added 2026-09-04).** `fleetguard_audit_log` has recorded every
campaign launch, work-order status change, (re)assignment, and cost log since Phase 7, but
nothing ever exposed it — the same gap `list_service_campaigns` had before `ServiceCampaigns.tsx`
existed. `routers/audit_log.py` adds `GET /api/audit-log` (filterable by `entity_type`/
`entity_id`/`action`) and `GET /api/audit-log/export.csv` (same filters, streamed as a
downloadable file with `Content-Disposition: attachment`). Both are read-only, so — unlike
approval and work-order writes — they carry no `FLEETGUARD_APPROVERS` gate: the allowlist exists
to stop someone *writing* a plausible-looking action into the log, not to stop someone *reading*
what already happened, and every other fleet-data read in this app follows the same asymmetry.
`before_state`/`after_state` are stored as `JSONB` and confirmed (checked live against
`bootcamp_students.fleetguard_audit_log`) to come back from psycopg3 as native Python `dict`
already — no manual `json.loads` needed on the read path, only on the way into the CSV cell.
`AuditLog.tsx` (nav tab "Audit log") renders each row's before/after as a single human-readable
"key: before → after" line instead of two raw JSON blobs, plus an "Export CSV" link that is a
plain `<a href>` rather than a fetch-and-blob dance — the browser already carries the session
cookie (or, on Databricks Apps, the platform-injected header) on a same-origin navigation, so no
extra client code is needed to authenticate the download.

**Depot risk has a home (added 2026-09-04).** `fleetguard_depot` (60 rows, Phase 2) had nothing
reading it beyond `resolve_scope`'s depot-narrowing predicate — no view showed which depots
actually carry the most exposure. `GET /api/depot-risk` (`routers/depots.py`) joins three
independent aggregates per depot — fleet size (`fleetguard_vehicle`), exposure and urgency
(`fleetguard_vehicle_exposure` joined to `fleetguard_recall_campaign`, split on
`park_it OR do_not_drive`), and work-order backlog (`fleetguard_work_order`, outstanding vs.
overdue) — merged in Python rather than one large multi-join `GROUP BY`, to avoid join fan-out
across three independently-cardinal relationships. **Deliberately no single blended "risk
score":** a composite index with hidden weights is the same mistake I-069 already caught once
(a flat cost-per-vehicle multiplier that looked data-driven but wasn't) — this returns the real
component numbers and lets `DepotRisk.tsx` sort/filter by whichever one matters, the same
pattern as every other table added this session. The one visual shortcut taken is a heatmap
tint on the "Urgent" and "Overdue" cells, tiered by `urgent_vehicles_exposed ÷ fleet_size` (a
plain ratio, not a formula) so a depot with a small fleet and a few urgent vehicles isn't
ranked the same as a large depot with the same raw count.

**Recall trend — this app's first chart (added 2026-09-04).** `fleetguard_recall_campaign.
issued_at` is the real NHTSA filing date (not a demo timestamp), and joined to the fleet's own
exposure match it turns out to hold 13 years of real history (2014–2026) already sitting in
Lakebase — no SQL Warehouse call needed. `GET /api/recall-trend` (`routers/trends.py`) groups
by year; `Trends.tsx` renders it as two small bar charts (campaigns per year, with a red
sub-segment for the Park It/Do Not Drive portion; vehicles exposed per year, kept separate
because the two don't always move together). The chart itself is hand-rolled inline SVG
(`lib/BarChart.tsx`) rather than a charting dependency — same precedent as `markdown.tsx`
(hand-rolled instead of `react-markdown`) and the hand-rolled SVG icons already in `App.tsx`.
Years with zero fleet-relevant campaigns are simply absent from the response rather than
zero-filled, so the endpoint never asserts a count for a year it didn't actually find data for.

**The "no shared identity on Render" rule stands, and has been satisfied rather than
waived.** Its stated reason was that the URL is public and the API has a write path, so one
shared identity would let anyone approve service campaigns. Both halves are now addressed:
there *is* a sign-in, and the write path is disabled on that surface entirely. No Databricks
credential exists there to share — see the table above.

**The agent chat panel ships to Render but stays inert there.** `POST /api/chat` invokes the
serving endpoint with the caller's *Databricks* token — which `app-login` does not issue — so
every call returns 401 and the panel renders an explanation rather than an error. Signing in
with GitHub proves identity to the app; it grants nothing on Databricks. That is the seam
behaving correctly, not a defect: the panel is functional the moment it runs somewhere that
supplies an identity, which is verified locally (`static-dev`) and is what Databricks Apps
supplies via OBO. Giving Render a service-principal identity so the public panel "works"
would be an **amendment to the decision above, not an exception to it** — the stated reason
there is the write path, and the chat route has none, but it would still expose
workspace-billed LLM inference and complaint-narrative retrieval to anyone with the URL.
Decide it explicitly if it comes up.

**The public landing page is Evidence, not a login screen.** Google Safe Browsing flagged
the deployment as a *"Dangerous site"* while its entire anonymous surface was a "Continue with
GitHub" prompt on a zero-reputation shared subdomain — a textbook phishing signature (I-057).
The served bundle was byte-compared against the local build to rule out compromise before
concluding it was a false positive. Anonymous visitors now land on the measured result, with
sign-in as a header action; an explicit link such as `#/queue` is still honoured, because a
shared link must go where it says.

**The auth seam.** The two environments differ in exactly one way — how the user's token
arrives. Everything downstream (SQL, Lakebase, agent invocation, ABAC) is identical.
Therefore the backend resolves the caller's token through **a single swappable provider**
selected by configuration; **no route handler reads a header or session directly**. This is
built in MVP, not retrofitted: with the seam the migration is an afternoon, without it a
rewrite in the final week, on the code path carrying every authorisation guarantee in §5.

**The seam's claim was tested on 2026-09-08 and held.** Deploying to Databricks Apps needed an
`app.yaml`, one CLI scope grant, and **one line of code** (`auth_type="pat"`, I-082) — no route
handler changed, because none of them reads a header.

---

## 8b. Every authentication path, in one place

Five layers, five different identity models. They are easy to conflate and the differences are
load-bearing, so they are tabulated here rather than left spread across §7.1, the auth-seam
docstrings and four issue entries.

### Layer 1 — caller → console

One seam, four providers, chosen by `FLEETGUARD_AUTH_MODE`. **Read explicitly, never inferred**
— inference would let a misconfiguration silently select a weaker trust model.

| mode | provider | identity arrives via | status |
|---|---|---|---|
| `databricks-apps` | `ForwardedHeaderTokenProvider` | `x-forwarded-access-token`, injected by the Apps ingress | verified live 2026-09-08 — **owner identity only, see I-084** |
| `render-u2m` | `SessionTokenProvider` | U2M OAuth (PKCE); token held in a signed session cookie | built and flipped live; browser round-trip unconfirmed |
| `app-login` | `AppLoginTokenProvider` | GitHub OAuth — **carries no Databricks credential**; startup refuses unless `FLEETGUARD_DATA_MODE=snapshot` | verified |
| `static-dev` | `StaticTokenProvider` | `databricks auth token --profile abhi`, held statically (1 h life, I-053-adjacent) | used daily |

### Layer 2 — console → Lakebase

Exactly one method, and it is the property the whole design rests on:

```
WorkspaceClient(host, token=<caller's token>, auth_type="pat")
  -> postgres.generate_database_credential(endpoint=...)
  -> psycopg connects AS THE CALLER'S OWN POSTGRES ROLE
```

Credentials are cached per principal with a staleness check. `auth_type="pat"` is mandatory
inside Apps (I-082): the platform injects `DATABRICKS_CLIENT_ID`/`SECRET` for the app's own
service principal, and without pinning the strategy the SDK refuses to choose — the dangerous
"fix" being a silent fallback to the app's identity.

**Deliberately unused:** the static connection URL in the `lakebase-db` secret. It works, and
it collapses every user onto one fixed role — keeping `opened_by` real while making per-user
RLS inapplicable. Documented fallback, not a path.

### Layer 3 — console → agent

`POST /serving-endpoints/<agent>/invocations` with `Bearer {principal.token}` — the caller
again. Requires the `model-serving` scope under Apps.

### Layer 4 — deployed agent → data (a different identity entirely)

Inside the serving endpoint, `WorkspaceClient()` **with no arguments** resolves to the
endpoint's auto-provisioned service principal, via **automatic authentication passthrough**,
granted only what `resources=[...]` declared at log time.

This is the one service-principal identity in the system, and it is deliberately the most
constrained: **engine and data are separate grants**, which is exactly how I-050 happened —
the warehouse was declared, the table was not, and a missing grant surfaced as an empty result
rather than an error. The agent has **no** Lakebase path at all; all three routes were checked
and closed (§7.1), which is why the write is an action envelope the console executes.

### Layer 5 — notebooks and jobs → Lakehouse

Jobs run as their owner against Unity Catalog via `spark.sql`. Local CLI work uses the `abhi`
profile's OAuth — U2M through Databricks' own **first-party CLI client**, which is why it needs
no custom app registration and sidesteps the `all-apis` blocker below.

### Evaluated and closed

| path | why not |
|---|---|
| M2M `client_credentials` service principal on Render | §8a — no PAT or SP on a public host |
| U2M with `all-apis` | account admin declined; the assignable set is only `all-apis`/`sql`/`offline_access`/`openid`/`profile`/`email`, so no narrower combination covers Lakebase **and** Model Serving |
| Lakebase static URL from a secret | works; costs per-user identity (above) |
| `DatabricksLakebase` MLflow resource | addresses a database *instance*; this project's Lakebase is the autoscaling project/endpoint flavour |
| A service principal for the agent | `service-principals create` is admin-only on this workspace |
| Personal access tokens | `tokens list` → *"User does not have permission to use tokens"* |

**The through-line:** every user-facing read and write executes as the human. The only
service-principal identity in the system belongs to the agent, and it cannot reach the
database.

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
| Unit | 276 tests, no Databricks | `pytest` |
| Pipeline expectations | In-pipeline, `_dq_failures` quarantine split | With the pipeline |
| Data quality + live scoping | 21 tests against the live workspace | `pytest -m integration --run-integration` |

Logic that has been wrong once lives in `src/fleetguard/` (`vin.py`, `chunking.py`,
`naming.py`) so it is testable off-platform, with each past bug encoded as a named
regression. The data-quality tests deliberately **do not trust `_rescued_data`** — they
assert measured cardinalities instead.

**Every router is covered as of 2026-09-07.** Six had none — `queue`, `depots`, `trends`,
`audit_log`, `technicians`, `signals` — including `queue.py`, the operator's primary surface,
which had never had a unit test. `tests/fakes.py` provides a shared fake cursor keyed by SQL
substring, so a test states what the database contains rather than replaying a call order.

These deliberately test the **Python around the SQL**, not the SQL: WHERE-clause assembly,
merging several result sets, defaulting a depot that returned no rows, VIN masking, CSV
serialisation, the snapshot short-circuit. Re-asserting query text through a fake would only
prove the fake matches the string; the statements themselves are exercised for real by the
integration tests and by live verification.

Two habits this round established, both after a test passed when it should not have:

- **Assert the message, not just the status code**, wherever several paths return the same
  one. `approve_campaign` has two unrelated 409s, and a duplicate-approval test that checked
  only the code passed with the duplicate check deleted (I-063).
- **Verify a regression test fails against the unfixed code** before trusting it. Every fix in
  the 2026-09-07 review batch (I-063, I-071, I-072) was confirmed this way; it is what caught
  the above, and the `list()`-snapshot race in `prune_sessions`.
