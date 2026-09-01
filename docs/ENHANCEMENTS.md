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

## Tier 3 — genuinely additive; only if Phases 6–8 land early

### E-10 · Genie + UC metric views, as a pair
**Status: DEFER, adopt together or not at all.** Currently on the cut list, though §9 calls
Genie "load-bearing".

They belong together: Genie over raw tables invents joins, whereas Genie over a **metric
view** is constrained to defined measures. Defining `exposure_rate`, `open_work_orders`,
`mean_time_to_remediate` once gives the dashboards and the NL interface a single semantic
layer — and serves the "reliability analyst" persona in §2 that nothing currently serves.

Metric views appear **zero** times in our docs today. Real gap, moderate value, non-trivial
cost.

---

## Rejected — recorded so they are not silently revisited

| Item | Why not |
|---|---|
| **Agent Bricks / supervisor agent** | Different product from the Agent Framework (`CLAUDE.md`). A supervisor needs specialised sub-agents to supervise; we have one agent with eight governed tools. Adds orchestration with nothing to orchestrate. |
| **DSPy** | Prompt optimisation against a metric. Our agent's quality bar is tool-grounding and approval-gating, not prompt search. New dependency, no path to a FleetGuard objective. |
| **Omnigent** | Beta. Not 24 days before a demo. |
| **The Day 4 chat-app template wholesale** | Node/TypeScript AppKit. §8.7 commits to React + FastAPI on Render first, Databricks Apps second, for stated reasons. Worth reading for the Apps phase; adopting it would discard a deliberate decision. |
| **Multi-agent orchestration** | No second agent exists. Complexity with no user-visible benefit. |

---

## Scope reality check

Phases 6, 7 and 8 — the demo surface — are **unstarted at 24 days out**, and they are a
dependent chain. This backlog is safe only because Tier 0 and Tier 1 are *how those phases
get built*, not work beside them.

**If time compresses, Tier 3 goes first, then E-07 and E-08.** Tier 0 (E-01) is not
optional: it corrects a claim already written into the proposal, and leaving a known-false
claim in place is worse than omitting the feature entirely.
