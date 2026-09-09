# FleetGuard — evaluated enhancement backlog

**Living document.** Created 2026-09-01 from the Day 4 production-practice material in
`~/PycharmProjects/ai-agents-2026/day4`, evaluated against FleetGuard's core flow.

Every item carries a **verdict**, and the reasoning is tied to what FleetGuard actually
does — not to whether the feature is impressive. Rejections are recorded with reasons so
they are not silently revisited.

---

## The test each item had to pass

FleetGuard's core flow is two loops:

**Reactive** — campaign posts → scope resolved against the VIN roster → exposure ranked by
depot and severity → service campaign launched **under human approval** → work orders.

**Proactive** — complaint accumulation → defect signal → operator sees it before the
regulator acts (**measured 16.0% at median 197 days vs 11.1% placebo**).

An enhancement earns its place if it makes one of those loops **more correct, more
observable, or more trustworthy**. "Demonstrates a platform feature" is not sufficient — the
project is already over-scoped for 24 days, and a half-built impressive thing scores worse
than a complete modest one.

**The crucial distinction below:** most Tier 1 items are not *extra* scope. Phases 4, 6 and 7
are unstarted, so building them this way costs roughly what building them badly would.
Genuinely additive work is confined to Tier 3.

---

## 🔴 Tier 0 — corrects a claim we have already made

### E-01 · AI Gateway belongs in front of the LLM endpoint, not the agent endpoint

**Status: ADOPT — highest priority. Blocks I-015.**

`day4/02_ai_gateway_dab/databricks.yml` records a real, documented rejection:

> *"External model, provisioned throughput, and pay-per-token endpoints are fully supported;
> **agent endpoints currently only support inference tables**."*

So `guardrails`, `rate_limits` and `usage_tracking_config` **cannot** be applied to an agent
endpoint deployed with `agents.deploy()`.

**Why this is a problem for us.** Proposal §4.5 claims AI Gateway PII guardrails on the
agent. Combined with **I-015** — output guardrails do not apply to streaming responses —
that claim is wrong twice: wrong endpoint type, and bypassed by streaming even where
supported.

**Why the fix genuinely improves FleetGuard, rather than just relocating a checkbox.**
Complaint narratives are consumer-authored free text containing names, addresses, plate and
phone numbers. The moment PII can leak is when retrieved narratives are placed **into a
prompt**. A guardrail on the *LLM* endpoint sits exactly at that boundary — it is a better
control point than the agent endpoint, not merely an available one.

**Shape:** create our own pay-per-token endpoint (`fleetguard-llm`, wrapping
`system.ai.<model>`), attach the `ai_gateway` block as bundle IaC, and point the agent at it.
Keeps `inference_table_config` on the agent endpoint, which *is* supported.

**Also settles I-015:** with the guardrail on the LLM call rather than the agent response,
the streaming question stops being load-bearing for the PII claim.

> ⚠️ **UNVERIFIED against our workspace.** The quote is from course material for this
> metastore, but per project convention (`CLAUDE.md`) no endpoint behaviour goes into the
> proposal until confirmed live. **Confirm with a real `PUT` before editing §4.5.**

---

## Tier 1 — adopt: this *is* Phases 4/6/7, built correctly

### E-02 · `ResponsesAgent` + models-from-code + `agents.deploy()`
**Status: ADOPT.** Not an enhancement so much as the correct shape for Phase 7. Our proposal
names "Mosaic AI Agent Framework" generically; this is the concrete interface — structured
tool calling, token usage, multi-turn, OpenAI compatibility, and a serving path that is one
call. Logging as a `.py` file rather than a pickle also means the agent is reviewable in
git, which matters for a project whose whole argument is auditability.

### E-03 · MLflow tracing with domain spans
**Status: ADOPT. Highest value-per-hour in the list.**

`autolog()` is free; the value is custom `@mlflow.trace` spans typed `RETRIEVER` / `LLM` over
**our** steps: exposure match, severity scoring, approval gate, work-order write.

**This closes a loop we already designed.** `fleetguard_agent_action` already has a
`trace_id` column — tracing was anticipated in §4.4 and never wired. Adopting it makes the
audit trail and the observability trail the *same* trail, which is a materially stronger
governance story than either alone.

### E-04 · Inference tables on the agent endpoint
**Status: BUILT — inference tables 2026-09-02, per-request token cost 2026-09-09.** The one
Gateway feature that *is* supported on agent endpoints. Feeds §8.6 observability and
per-request cost.

**The documented extraction path does not work as written.** E-04 records it as
`$.databricks_output.trace.info.trace_metadata['mlflow.trace.tokenUsage']`. Measured: the
**parent object resolves** (`get_json_object(... '$.databricks_output.trace.info.trace_metadata')`
is non-null), but the leaf **cannot be addressed** — the key name contains dots
(`mlflow.trace.tokenUsage`), which Spark's `get_json_object` JSONPath treats as nesting. The
value is also a **JSON string inside** the metadata object, so it needs a second parse. Both
facts have to be discovered by looking at the payload; neither is in the note.

**Second trap: the value is not unique.** A single response carries **3–7** `total_tokens`
occurrences — one per LLM call in the agent's tool loop — plus the one trace-level aggregate.
An unanchored `regexp_extract` returns whichever appears first. Checked across every 200-row:
the aggregate happens to come first and the two agree, **but nothing guarantees that ordering**,
so the shipped query anchors on `INSTR(response, 'mlflow.trace.tokenUsage')` and reads within
that block — correct by construction rather than by luck.

**Where it landed:** the dashboard's Operations page joins `gold_agent_action` to
`fleetguard_agent_payload` on E-03's `trace_id` and shows, in one row, **who authorised a write,
what the model saw, how long it took, and the tokens it consumed** (measured: action 7 —
14,727 in / 1,235 out / 15,962 total, 29,726 ms). The columns `gold_agent_action.input_tokens`
/ `output_tokens` stay **deliberately empty**: the inference table already holds this, and a
second copy invites the two to disagree.

Day 4 supplies the non-obvious extraction path — token usage is buried in the trace metadata,
not a column:
`$.databricks_output.trace.info.trace_metadata['mlflow.trace.tokenUsage']`
Also notes that `system.serving.endpoint_usage` tracks **only** foundation-model PAYG calls,
never custom agent endpoints. Both are the kind of detail that costs an afternoon to
rediscover.

### E-05 · `mlflow.genai.evaluate` with built-in and custom scorers
**Status: BUILT 2026-09-02** — `src/agent/16_evaluate_agent.py`, 10 adversarial cases against
registered version 3.

What the build added to the plan below, learned from I-050: **the built-in judges cannot
catch this project's actual failure mode.** `RelevanceToQuery` and `Safety` both pass a
fluent, on-topic, harmless answer that says "no fleet vehicles are affected" about a recall
touching 25 of them. So the load-bearing checks are deterministic `@scorer` functions
asserting *specific facts* — the same discipline as pinning numbers in a smoke test.

Two of them are **hard gates** that fail the job rather than lowering a score:
`never_claims_launched` (checked on every case, not only the two that ask — a spontaneous
claim elsewhere is worse) and `never_invents_a_recall`. The launch check is a literal phrase
list rather than an LLM judge on purpose: a check on whether the agent overstepped must not
itself be probabilistic.

The sharpest case asks *"is there a recall on RAM 2500 service brakes?"* — there **is** an
emerging signal and there is **no** recall, so it tests the three-state distinction directly.

Original assessment, unchanged and still correct:

Built-ins (`Correctness`, `Safety`, `RelevanceToQuery`) are table stakes. The item with real
FleetGuard value is **`Guidelines`**, because our domain rules are exactly the kind of thing
it encodes — and each of these has already been violated once in this project:

- *"Never state that a recall exists when only an investigation is open."* — the three-interval
  conflation, already shipped wrong once.
- *"Any recommendation on a `MODEL_VARIANT` match must state the match tier."* — variants
  outnumber exact matches 3:1 (I-030); §7's deterministic guarantee covers `EXACT` only.
- *"Never present a lead-time figure without its control-arm comparison."* — the discipline
  §6 commits to.

A custom `@scorer` can additionally assert the agent **called a retrieval tool at all**, which
is how "no unsourced claims" becomes machine-checkable rather than aspirational.

### E-06 · Engineering hygiene from the reference implementation
**Status: ADOPT — cheap, prevents known failure modes.**

- **`ModelConfig` injected at log time** rather than deriving schema from `w.current_user`.
  At serving runtime that resolves to a **service-principal UUID**, not a username, so the
  path silently breaks. Precisely our class of silent failure.
- **Latest-version resolution** (`max(search_model_versions)`) instead of a hardcoded
  version, so evaluation never scores a stale model.
- **Idempotent dataset build** — skip expensive generation when the table exists. Same
  discipline as our resumable embedding job.

---

## Tier 2 — adopt with scope discipline

### E-07 · Human feedback / labeling sessions
**Status: NOT BUILT — closed 2026-09-09.** Left as ADOPT with no implementation and no
reversal for a week, which is I-051's pattern happening in the backlog rather than in the spec:
an intention that reads as a description. Closing it explicitly instead.

**Why not:** the entry's own honest limit is the reason. A labeling session with one reviewer is
a *mechanism* demonstration, not evidence of quality, and the project already has the stronger
version of what this was for — **E-05's evaluation harness** (`src/agent/16_evaluate_agent.py`,
10 adversarial cases with real scorers) and a **765-pair golden set built from NHTSA's own recall
text**, which is external ground truth rather than self-labelling. Adding a one-reviewer labeling
session alongside those would add a weaker signal and invite it to be quoted as validation.

Revisit if a second reviewer ever exists; with n=1 the ceiling is a screenshot.

*Original rationale, kept:* This is the scratchpad's "closed feedback loop", and
`create_label_schema` + `create_labeling_session` + `log_feedback` is a genuine capability.

**Honest limit:** a labeling session with one reviewer is a *mechanism* demonstration, not
evidence of quality. Build the schemas and session, label a handful of traces, and **state
the n**. Claiming a validated feedback loop from a single self-labelling reviewer would be
exactly the kind of unfalsifiable claim this project has spent two weeks removing.

### E-08 · Synthetic evaluation data — for the agent only
**Status: ADOPT, NARROWLY.** `generate_evals_df` seeds an agent Q&A eval set quickly, which
matters at 24 days.

**Do not use it for Model B.** The recall-matching golden set must be **real** recall/fleet
pairs with known correct answers — that is a classification ground truth, not a Q&A pair, and
synthesising it would mean grading the model against questions derived from its own inputs.
Keep the two datasets separate and say why.

### E-09 · LangGraph as the agent's orchestration
**Status: SATISFIED WITHOUT IT — closed 2026-09-09.** Not rejected, and not skipped for
schedule: **the requirement it was adopted for is met.**

E-09 was adopted for exactly one reason — the write path must halt, surface a proposed action,
and resume on approval — and explicitly *not* because the course uses LangGraph. That
suspend-and-resume now exists as the **action-envelope pattern** (`agent_actions.py`,
ARCHITECTURE §7.1): the model emits an envelope and stops; the FastAPI app validates it and
performs the write under the *caller's own* OBO token. That is a stronger form of the same
control than a graph interrupt, because the suspension crosses a **process and identity
boundary** — the model has no database path at all, so it cannot resume itself even in
principle. A LangGraph interrupt would keep both halves inside one process under one identity.

Adopting LangGraph now would replace a working, verified mechanism with a differently-shaped one
that satisfies the same requirement less strictly, two weeks before submission.

*Original rationale, kept:* The reason is the **human
approval gate**: FleetGuard's write path must halt, surface a proposed action, and resume on
approval. That is a graph interrupt/resume, which LangGraph handles natively and which is
awkward in a plain tool loop. Adopting it because the course uses it would be poor
justification; adopting it because our core flow has a suspend-and-resume step is a good one.

---

### E-11 · Conversational surface and persona model — **decided 2026-09-01**

**Status: ADOPT the split below. Reject persona routing by agent.**

Question raised: should FleetGuard have a chatbot UI, with employees, managers and
consumers reaching it through a supervisor agent?

**Chat: yes — beside the workflow, not as the workflow.** The core value is a *workflow*:
campaign lands → 2,116 exposed F-250s ranked by depot and severity → approve → work orders.
That is a queue and a form. Conversing through a ranked exposure list is strictly worse, and
§7's deterministic guarantee is far harder to demonstrate in prose than in a list that either
contains the right VINs or does not. But §13 requires an action-taking agent, and one exists
in §4.5 with four read and four write tools. So: **agent chat panel alongside the queue**,
proposing actions that the existing approval gate executes. This also keeps the demo's
strongest moment — the deterministic recall→work-order path — free of LLM variance.

**Persona routing via a supervisor agent: rejected, on security grounds.** This supersedes
the weaker "nothing to supervise" reasoning in the rejection table below.

Persona differences in FleetGuard are an **authorisation problem, not an orchestration
problem**, and §5.1 already solves it:

> *"One template — identity determines both rows and columns, and the frontend cannot bypass
> it."*

A supervisor routing by persona would re-implement that in prompt space. **An LLM deciding
what a user may see is not a security boundary.** It converts a claim we can *prove* — UC
evaluates ABAC under the user's own token — into one we would have to hope holds. The
correct mechanism is already designed in §5.3: tools are UC Functions with per-principal
`EXECUTE` grants, so *"a tool the agent has not been granted cannot be invoked, regardless of
what the model attempts."* Same agent, different identity, different capability, enforced
**below** the model.

**"Consumer" is not one of our personas.** All five are internal (safety manager, depot
manager, reliability analyst, VP Ops, platform engineer). §5.2's public surface is an
unauthenticated, pre-aggregated, read-only evidence page whose principal holds `SELECT` on
`public_summary` alone. A chatbot there would be actively harmful: unauthenticated LLM
access is a token-burn and prompt-injection vector, and it is the one surface where a
hallucination is publicly visible with no operator to catch it. **Static page.**

**Resulting surface map:**

| Persona | Surface | NL access |
|---|---|---|
| Safety manager (primary) | Queue + approval + **agent chat panel** | Full agent; write tools gated by approval |
| Depot manager | Work-order queue | Agent, read tools only |
| Reliability analyst | Pattern explorer | Agent, VINs masked by ABAC |
| VP Ops | Dashboard + **Genie** | Genie only (analytics NL, read-only) |
| Public | Static evidence page | **None** |

Note §2 already assigns VP Ops a *Genie Agent* rather than a chatbot, and that split is
correct: **Genie answers questions over governed tables; the agent does things under
approval.** Two NL surfaces with distinct jobs is a stronger story than one chatbot
pretending to be both — and it is why E-10 below is now *upgraded* rather than deferred.

### E-12 · Hosting — Render for building, Databricks Apps for submitting — **decided 2026-09-01**

**Status: ADOPT. Confirms §8.7's phased rollout; the auth seam becomes an MVP requirement.**

Three options were considered.

**❌ Free-edition Databricks App — rejected.** Free edition *does* support Apps (three exist
there), but it is a **different workspace and a different account**:

| | free-edition | abhi |
|---|---|---|
| Workspace | `dbc-6b3a5534-db75` | `dbc-7b106152-caf3` |
| User | `abhibastia90@gmail.com` | `abhisek.bastia17@gmail.com` |

App **resource bindings are workspace-local**, so a free-edition app cannot bind abhi's
Lakebase, SQL warehouse, or model-serving endpoint. The only route would be M2M OAuth with a
stored secret calling abhi's REST endpoints.

That **destroys §5.1 Path A**. The claim is *"identity determines both rows and columns, and
the frontend cannot bypass it"* — which requires the signed-in user to **be** an abhi
identity so Unity Catalog evaluates ABAC under their own token. A free-edition user is not
one, so every query would execute as a single service principal and the row filters and
column masks become decorative. Rejected: it converts the project's strongest architectural
claim into a fiction. (All the data is in abhi anyway.)

**✅ Databricks Apps in `abhi` — required, but managed.** `databricks apps start` / `stop`
both exist, and **all ~40 student apps in that workspace currently sit `STOPPED`** — clearly
the intended pattern. Deploy once, keep stopped, start for testing and the submission window.
Not optional: §13 requires a *deployed* application and §5.1's OBO story is only genuine
inside an App. An identity architecture never deployed is an assertion.

> ⚠️ **Cost UNMEASURED.** `system.billing` is not readable from this account, so no figure is
> quoted here. Confirm with the workspace admin, or deploy once and observe for a day, before
> relying on the "stopped is free" assumption.

**✅ Render — right for building, risky for the live demo.** Free tier **spins down on
inactivity**; a 30–60 s cold start is fine for development and bad if a grader opens the link
cold or it happens live.

**Decision:**

| Window | Surface | Why |
|---|---|---|
| Now → 7 Sept (MVP) | **Render** | Free, fast iteration while code changes hourly |
| ~20 Sept | Deploy to **Databricks Apps**, keep **stopped** | Proves the deployment without burning compute |
| 25–30 Sept demo | **Databricks App** (started for the window) | The only place OBO is genuine; Render stays as fallback link |

### E-14 · U2M / Path D is RETIRED — **decided 2026-09-02**

**Status: REJECTED. Supersedes the Render half of E-12 and §8.7's Path D.**

U2M cannot be built, and on inspection is not wanted.

**It cannot be built.** U2M needs a *custom OAuth app integration* registered in the
Databricks **account** console. Measured 2026-09-02: the account is a shared bootcamp
metastore, `current-user me` reports groups `['users']` (not `admins`), and
`databricks account custom-app-integration list` returns **`Not Found`**. Registering one
would mean asking the cohort's owners to create an account-wide OAuth client for one
student's project.

**It is not wanted.** U2M and OBO end in the same place — the app holding a token that
represents the user. The only difference is who performs the login. Databricks Apps ingress
does it for free and injects `X-Forwarded-Access-Token`; U2M would have us implement the
redirect, PKCE and code exchange ourselves, hold a client secret, and register a client.
**OBO is the stronger identity story with strictly less setup.**

**Consequence — the two surfaces we actually need both work without it:**

| Surface | Auth | Needs OAuth app? |
|---|---|---|
| **Render** — public evidence page | **none** | No — live today |
| **Databricks Apps** — operator console | **OBO** | No — platform-injected |
| ~~Render operator console~~ | ~~U2M~~ | yes — blocked, and now unnecessary |

Render's role narrows to what §5.2 always described: an **unauthenticated, pre-aggregated,
read-only evidence surface**. That is not a downgrade — it is the role the architecture
assigned it before we tried to make it carry the console too.

> [!danger] Do not "solve" this with a PAT or a service principal
> The obvious workaround — put `DATABRICKS_TOKEN` or an SP secret in Render's env — makes
> **every request run as one identity**. Our Render URL is public and the API has a write
> path, so that would let anyone who finds the URL approve service campaigns. It would also
> contradict §5.1 and §1's "no long-lived credentials anywhere". Seen suggested in the
> cohort chat; it is wrong for a public surface with writes.

**The auth seam (E-13) survives intact** and is *more* justified, not less: it now spans
`static-dev` (local) and `databricks-apps` (deployed). `SessionTokenProvider` stays in the
codebase — it is tested, costs nothing, and is the implementation U2M would need if an
account admin ever registers a client.

### E-13 · The auth seam — **MVP requirement**

**Status: ADOPT — in MVP scope, and the reason E-12 is cheap.**

The two hosting environments differ in **exactly one** way:

| Environment | How the user token arrives |
|---|---|
| **Local dev** | `static-dev` — the developer's own token from `databricks auth token` |
| **Databricks Apps** | `X-Forwarded-Access-Token` header — the platform hands it over (OBO) |
| ~~Render console~~ | ~~U2M redirect (Path D)~~ — **retired, E-14.** Render serves the public evidence page with no auth. `SessionTokenProvider` remains implemented and tested against the day an account admin registers a client. |

Everything downstream is identical: the SQL, the Lakebase calls, the agent invocation, ABAC
evaluation. So the backend must resolve the caller's token through **one swappable
dependency** — a single `get_user_token()` provider selected by configuration — and never
read the header or the session directly inside a route handler.

**Why this is MVP scope and not a later refactor.** If the seam exists, the September
migration is an afternoon. If it does not, it is a rewrite in the week we can least afford
one — and it would land immediately before the demo, on the code path that carries every
authorisation guarantee in §5. The cheapest moment to build it is the first handler; the
most expensive is the twentieth.

**Testable now, off-platform:** the provider is plain Python, so both implementations get
unit tests alongside `vin.py` and `chunking.py` — no workspace required.

---

## Tier 3 — genuinely additive; only if Phases 6–8 land early

### E-15 · TSBs as a corroborating signal for Model A — **MEASURED AND REJECTED 2026-09-09**

**Status: REJECTED on evidence.** Not deferred, not descoped for schedule — tested against the
full backtest population and the signal is not there.

**Why it looked strong.** 5.8M TSB rows / 258,438 bulletins were already ingested, conformed and
quarantine-reconciled, and **consumed by nothing**. `silver_tsb.sql`'s own comment states the
intent: *"TSBs are Model A's corroborating signal: a manufacturer bulletin on the same component
raises confidence that a complaint cluster reflects a real defect rather than noise."* The table
carries `make`, `model`, `components` and `communication_date` on the same normalisation the
detector uses, and a manufacturer bulletin is a genuinely **independent observer** — the maker
documenting a defect, not more complaints. No new source, no embeddings, no new cost.

**Measured on the full 777 REAL / 606 PLACEBO population** (`gold_backtest_scope`, which spans
2010–2026 and is *not* the restricted 37-month embedded set):

| measurement | REAL | PLACEBO | reading |
|---|---:|---:|---|
| % with ≥1 prior-year TSB on the component | **48.5%** | **55.9%** | **opposite direction**, z −2.74, p 0.006 |
| all-time TSBs for that vehicle (median) | 669 | **1,737** | arms **not matched on TSB exposure** — 2.4× |
| share of prior-year TSBs on the component (median) | 3.19% | 3.14% | **no difference** |
| the same share (mean) | 12.25% | 9.0% | tail-driven, not a shift |
| % with <10 prior-year TSBs | **25.3%** | 19.7% | explains the mean gap — smaller denominators |

**The finding, in order.** The raw test came out *backwards*, and significantly so:
never-investigated series carry **more** prior-year TSBs. That is a confound, not a discovery —
the placebo arm was volume-matched on **complaints**, never on bulletins, and its vehicles carry
2.4× the TSB volume overall. Controlling for that by using each vehicle's own TSB share moved the
**mean** into the hypothesised direction, but the **medians are identical**, and the mean gap is
explained by REAL series having smaller denominators (a vehicle with 3 bulletins scores 33% on a
single match). Every apparent effect, in both directions, is TSB-volume mismatch.

**Consequence — and this is why the backtest came first.** The plan had been to ship a descriptive
"bulletins on this component in the window" column on `gold_emerging_signal`. **It is not shipped.**
The measurement says the number would not mean anything, and a number whose strength cannot be
stated is exactly what I-069 and I-075 exist to prevent. Building the column first and measuring
afterwards would have put an unvalidated figure in front of an operator.

**What would make this worth revisiting:** a placebo arm matched on TSB propensity rather than on
complaint volume — a different control-construction problem, not a schedule item.

**This is the project's second published negative**, after I-049 falsified semantic clustering.
Both were the obvious improvement; both were measured rather than assumed.

### E-10 · Genie + UC metric views, as a pair
**Status: UPGRADED to post-MVP committed (was: defer).** E-11 gives Genie a defined job —
the VP Ops analytics surface — rather than leaving it a loose platform feature, which is why
this moves up.

They belong together: Genie over raw tables invents joins, whereas Genie over a **metric
view** is constrained to defined measures. Defining `exposure_rate`, `open_work_orders`,
`mean_time_to_remediate` once gives the dashboard and the NL interface a single semantic
layer.

Metric views appear **zero** times in our docs today. Real gap, moderate value, non-trivial
cost. **Not in MVP.**

---

## Rejected — recorded so they are not silently revisited

| Item | Why not |
|---|---|
| **Agent Bricks / supervisor agent** | Two reasons, the second decisive. (1) Different product from the Agent Framework (`CLAUDE.md`); a supervisor needs specialised sub-agents, and we have one agent with eight governed tools. (2) **Routing personas through an agent puts access control in prompt space** — see E-11. UC ABAC and per-principal `EXECUTE` grants enforce it below the model, provably; an LLM router would downgrade that to a hope. |
| **DSPy** | Prompt optimisation against a metric. Our agent's quality bar is tool-grounding and approval-gating, not prompt search. New dependency, no path to a FleetGuard objective. |
| **Omnigent** | Beta. Not 24 days before a demo. |
| **The Day 4 chat-app template wholesale** | Node/TypeScript AppKit. §8.7 commits to React + FastAPI on Render first, Databricks Apps second, for stated reasons. Worth reading for the Apps phase; adopting it would discard a deliberate decision. |
| **Multi-agent orchestration** | No second agent exists. Complexity with no user-visible benefit. |

---

## MVP — target **7 September 2026** (6 days)

Demo is 25–30 September. An MVP at 7 Sept leaves ~18 days to improve on a working system
rather than to finish one. That ordering is right, but only if **MVP is defined narrowly
enough to actually land** — an undefined MVP will absorb every item in this document.

### MVP is one vertical slice, working end to end

> **A recall campaign lands → exposed vehicles ranked by depot and severity → a human
> approves → work orders are written to Lakebase → the change appears in Unity Catalog via
> CDF → all of it visible in a browser.**

Plus the agent proposing the campaign, and the measured backtest result displayed as
evidence.

### In scope

| # | Item | Why it is load-bearing |
|---|---|---|
| 1 | **Exposure load into Lakebase** (scope decision first) | Without it there is no work queue. The one hard blocker. |
| 2 | **Agent — minimal**: `ResponsesAgent`, ~3 tools (2 read, 1 gated write), tracing on (E-02/E-03) | §13's action-taking agent requirement |
| 3 | **Approval gate** writing to `fleetguard_service_campaign` / `_work_order` / `_audit_log` | The human-in-the-loop claim, and the audit trail |
| 4 | **Frontend**: React + FastAPI on Render (§8.7 phase 1) — queue, exposure detail, approve, agent panel | The only surface a viewer actually sees |
| 5 | ~~U2M OAuth (Path D)~~ **RETIRED (E-14)** — the auth seam (E-13) stays | U2M needs an account-admin OAuth registration we do not have, and OBO is better anyway. Render serves the public evidence page with **no auth**; the operator console authenticates via OBO on Databricks Apps. |
| 6 | **Evidence page** — static, showing the 16.0% / 11.1% / 1.44× result | Already measured; costs almost nothing to display |

**Hosting for MVP is Render** (E-12). Databricks Apps deployment is ~20 Sept, kept stopped
until the demo window. The App is *not* MVP scope, but the **auth seam that makes it cheap
is** — see E-13.

### Explicitly NOT in MVP

Deferred with intent, not forgotten: **Model B and the evaluation harness** (Phase 4) ·
**E-01 AI Gateway** · **E-05 scorers** · **E-07 labeling** · **E-10 Genie + metric views** ·
**Databricks Apps migration** (§8.7 phase 2) · governance beyond what ABAC gives for free ·
the write tools beyond the single gated one.

> **E-01 is deferred as *build*, not as *correction*.** The proposal's §4.5 guardrail claim
> is wrong today and must be fixed in the documents **before** MVP regardless — a known-false
> claim left standing is worse than a missing feature. Verifying the endpoint restriction
> live is a 15-minute task; building the gateway is post-MVP.

### The honest risk

Six days for items 1–6 is **aggressive**, and items 2→3→4 are a dependent chain. If it
slips, cut in this order: the agent panel (leaving a workflow-only console — still a
complete demo), then the evidence page. (U2M is no longer on the list — see E-14.)

**Do not cut the approval gate or the audit trail.** They are the difference between
FleetGuard and a dashboard, and they are what §5 and §13 are graded on.

**Do not cut the auth seam either** — even though U2M is retired and MVP runs on a locally
supplied token. The seam is a few lines on day one and a rewrite in week four, and it lands
on the code path carrying every authorisation guarantee in §5. Cutting the *implementation*
is fine; cutting the *indirection* is not.

---

## Scope reality check

Phases 6, 7 and 8 — the demo surface — are **unstarted**, and they are a dependent chain.
This backlog is safe only because Tier 0 and Tier 1 are *how those phases get built*, not
work beside them.

**Post-MVP order (7 → 25 Sept):** E-01 (gateway, corrects a claim) → E-05 (scorers, makes
domain rules checkable) → Phase 4 Model B → E-07 → E-10 → Databricks Apps migration.

**If time compresses, Tier 3 goes first, then E-07 and E-08.**
