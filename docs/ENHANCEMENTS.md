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
**Status: ADOPT.** The one Gateway feature that *is* supported on agent endpoints. Feeds
§8.6 observability and per-request cost.

Day 4 supplies the non-obvious extraction path — token usage is buried in the trace metadata,
not a column:
`$.databricks_output.trace.info.trace_metadata['mlflow.trace.tokenUsage']`
Also notes that `system.serving.endpoint_usage` tracks **only** foundation-model PAYG calls,
never custom agent endpoints. Both are the kind of detail that costs an afternoon to
rediscover.

### E-05 · `mlflow.genai.evaluate` with built-in and custom scorers
**Status: ADOPT — this is Phase 4, with the pattern solved.**

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
**Status: ADOPT, SMALL SLICE.** This is the scratchpad's "closed feedback loop", and
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
**Status: ADOPT — for a specific reason, not for alignment.** The reason is the **human
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

### E-13 · The auth seam — **MVP requirement**

**Status: ADOPT — in MVP scope, and the reason E-12 is cheap.**

The two hosting environments differ in **exactly one** way:

| | How the user token arrives |
|---|---|
| Render (§8.7 phase 1) | U2M OAuth redirect flow (**Path D**) — our code obtains it |
| Databricks Apps (phase 2) | `X-Forwarded-Access-Token` header — the platform hands it over |

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
| 5 | **U2M OAuth (Path D)** behind the **auth seam** (E-13) | Required for the Render console to hold a real user token — and the seam is what makes the September move to Databricks Apps an afternoon rather than a rewrite |
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
complete demo), then the evidence page, then U2M OAuth by demoing locally.

**Do not cut the approval gate or the audit trail.** They are the difference between
FleetGuard and a dashboard, and they are what §5 and §13 are graded on.

**Do not cut the auth seam either** — even if U2M itself is cut and MVP runs on a locally
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
