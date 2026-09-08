# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 7: the agent
# MAGIC
# MAGIC A `ResponsesAgent` (E-02) with six tools — five reads and one write request — logged
# MAGIC models-from-code, registered in Unity Catalog and deployed with `agents.deploy()`.
# MAGIC
# MAGIC ## Design constraints that are not negotiable
# MAGIC
# MAGIC **The agent proposes; it never launches.** `propose_service_campaign` writes a
# MAGIC *proposal* and returns it for a human to approve through the existing gate. The agent
# MAGIC holds no path to `fleetguard_work_order`. This is §5.3's human gate, and it is the
# MAGIC difference between FleetGuard and an autonomous system nobody would deploy against a
# MAGIC vehicle fleet.
# MAGIC
# MAGIC **`EXACT` vs `MODEL_VARIANT` must always be stated.** Variant matches outnumber exact
# MAGIC ones ~3:1 (I-030) and §7's deterministic guarantee covers `EXACT` only. A tool that
# MAGIC returned a count without its tier would let the model present a probabilistic match as
# MAGIC a certainty.
# MAGIC
# MAGIC **Never claim a recall exists when only an investigation is open.** The three
# MAGIC lead-time intervals were conflated once already; the system prompt forbids it and a
# MAGIC `Guidelines` scorer will check it in Phase 4 (E-05).
# MAGIC
# MAGIC ## Tracing (E-03)
# MAGIC
# MAGIC `mlflow.openai.autolog()` plus explicit `@mlflow.trace` spans on retrieval and
# MAGIC exposure lookup. `fleetguard_agent_action.trace_id` has existed since Phase 5 for
# MAGIC exactly this — it makes the audit trail and the observability trail the same trail.

# COMMAND ----------

# MAGIC %pip install -q -U mlflow databricks-agents databricks-sdk
# MAGIC %restart_python

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.fleetguard_agent"
LLM_ENDPOINT = "databricks-claude-opus-4-8"
INDEX = f"{CATALOG}.{SCHEMA}.complaint_chunk_idx"

dbutils.widgets.text("llm_endpoint", LLM_ENDPOINT)
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint")
print(f"model : {MODEL_NAME}\nllm   : {LLM_ENDPOINT}\nindex : {INDEX}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The agent, as a file
# MAGIC
# MAGIC Models-from-code: logged as a `.py`, not a pickle, so the agent is reviewable in git —
# MAGIC which matters for a project whose argument is auditability.
# MAGIC
# MAGIC Config comes from `ModelConfig`, **not** from `w.current_user`. At serving runtime that
# MAGIC resolves to a service-principal UUID rather than a username, and the path silently
# MAGIC breaks (E-06).

# COMMAND ----------

# MAGIC %%writefile fleetguard_agent.py
# MAGIC """FleetGuard agent — ResponsesAgent over Databricks-hosted tools."""
# MAGIC
# MAGIC import json
# MAGIC import time
# MAGIC from typing import Any, Generator
# MAGIC
# MAGIC import mlflow
# MAGIC from databricks.sdk import WorkspaceClient
# MAGIC from databricks.sdk.service.sql import StatementParameterListItem, StatementState
# MAGIC from mlflow.entities import SpanType
# MAGIC from mlflow.models import ModelConfig, set_model
# MAGIC from mlflow.pyfunc import ResponsesAgent
# MAGIC from mlflow.types.responses import (
# MAGIC     ResponsesAgentRequest,
# MAGIC     ResponsesAgentResponse,
# MAGIC     ResponsesAgentStreamEvent,
# MAGIC )
# MAGIC
# MAGIC mlflow.openai.autolog()
# MAGIC
# MAGIC _cfg = ModelConfig(development_config="agent_config.yaml")
# MAGIC CATALOG = _cfg.get("catalog")
# MAGIC SCHEMA = _cfg.get("schema")
# MAGIC LLM_ENDPOINT = _cfg.get("llm_endpoint")
# MAGIC INDEX = f"{CATALOG}.{SCHEMA}.complaint_chunk_idx"
# MAGIC
# MAGIC w = WorkspaceClient()
# MAGIC
# MAGIC SYSTEM_PROMPT = """You are FleetGuard's recall-response assistant, used by fleet safety
# MAGIC managers who act on what you tell them.
# MAGIC
# MAGIC Rules you must never break:
# MAGIC
# MAGIC 1. Ground every factual claim in a tool result. If the tools do not support a claim,
# MAGIC    say you do not know. Never invent a campaign number, a VIN, or a vehicle count.
# MAGIC 2. When you report exposure, ALWAYS state the match tier. EXACT matches are
# MAGIC    deterministic on make, model, year and manufacture window. MODEL_VARIANT matches are
# MAGIC    probabilistic and must be described as needing confirmation.
# MAGIC 3. Never say a recall exists when only an investigation is open. An NHTSA
# MAGIC    investigation opening is not a recall; they are different events, months apart.
# MAGIC    There is a THIRD state below both: an *emerging signal* is a statistical anomaly
# MAGIC    this system detected in complaint volume. NHTSA has not acted on it at all. Never
# MAGIC    describe a signal as a recall, as an investigation, or as a confirmed defect. Say
# MAGIC    "we detected" — not "there is". The detector finds roughly one investigation in six
# MAGIC    ahead of NHTSA against 11.1% on a matched control: an edge, not an oracle, and
# MAGIC    saying so is required, not optional.
# MAGIC 4. You may PROPOSE a service campaign. You cannot launch one — a human approves it.
# MAGIC    Say so plainly when you propose.
# MAGIC 5. Complaint narratives are consumer-written and contain personal detail. Summarise
# MAGIC    them; never quote names, addresses, phone numbers or plates.
# MAGIC 6. `open_defect_signal` is a REQUEST, not a save. This rule governs how you DESCRIBE
# MAGIC    the action — it is NOT a permission gate. You do not have database access; the
# MAGIC    console performs the write and reports the result. Say "I've requested a defect
# MAGIC    signal for ..." — never "I've saved", "I've created" or "it's now tracked". If you
# MAGIC    claim a write that the console then fails to make, you have told the user something
# MAGIC    false about a safety record. Do not invent a signal id; the console assigns it.
# MAGIC 7. Being asked to open a signal IS the authorization. Do not recite rule 6's
# MAGIC    request-versus-save mechanism back as something for the user to approve, and do not
# MAGIC    ask for permission you have already been given. If the scope is underspecified,
# MAGIC    call `lookup_fleet_models` to see what the fleet actually operates, act on the most
# MAGIC    defensible reading, and STATE the assumption you made. Ask a clarifying question
# MAGIC    only when the request is genuinely unanswerable — not when it is merely vague.
# MAGIC 8. Fleet make and model names are the FLEET's, and they differ from NHTSA's spelling:
# MAGIC    NHTSA writes `F-250 SD`, the fleet registry writes `F-250`. Before you name a make
# MAGIC    or model in `open_defect_signal`, check it with `lookup_fleet_models` and pass the
# MAGIC    fleet's spelling. Naming a make the fleet does not operate produces a signal
# MAGIC    recorded against zero vehicles, which sorts to the bottom of the operator's page.
# MAGIC """
# MAGIC
# MAGIC
# MAGIC def _run_sql(statement: str, params: list) -> list:
# MAGIC     """Run a warehouse query and REFUSE to return rows unless it actually succeeded.
# MAGIC
# MAGIC     The previous version passed `wait_timeout="30s"` and then read
# MAGIC     `stmt.result.data_array or []`. When the warehouse was cold the statement was still
# MAGIC     PENDING at 30 s, `result` was None, and the tool returned zero rows — which the model
# MAGIC     faithfully reported as "no fleet vehicles are affected" for a campaign with 25
# MAGIC     exposed vehicles across 22 depots (I-050). A recall assistant that turns an
# MAGIC     infrastructure timeout into an all-clear is worse than one that is simply down.
# MAGIC
# MAGIC     So: poll to a terminal state, and raise on anything that is not SUCCEEDED. The tool
# MAGIC     loop turns the exception into an error the model can see and report honestly.
# MAGIC     """
# MAGIC     stmt = w.statement_execution.execute_statement(
# MAGIC         warehouse_id=_cfg.get("warehouse_id"),
# MAGIC         statement=statement,
# MAGIC         parameters=params,
# MAGIC         wait_timeout="50s",  # API maximum for the synchronous wait
# MAGIC     )
# MAGIC     deadline = time.monotonic() + 120
# MAGIC     while stmt.status and stmt.status.state in (StatementState.PENDING, StatementState.RUNNING):
# MAGIC         if time.monotonic() > deadline:
# MAGIC             raise TimeoutError(f"warehouse query still {stmt.status.state} after 170s")
# MAGIC         time.sleep(2)
# MAGIC         stmt = w.statement_execution.get_statement(stmt.statement_id)
# MAGIC
# MAGIC     state = stmt.status.state if stmt.status else None
# MAGIC     if state != StatementState.SUCCEEDED:
# MAGIC         err = stmt.status.error.message if (stmt.status and stmt.status.error) else ""
# MAGIC         raise RuntimeError(f"warehouse query {state}: {err}")
# MAGIC     return (stmt.result.data_array or []) if stmt.result else []
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.RETRIEVER)
# MAGIC def search_complaints(query: str, limit: int = 5) -> list[dict]:
# MAGIC     """Hybrid search over 1.75M complaint-narrative chunks."""
# MAGIC     r = w.vector_search_indexes.query_index(
# MAGIC         index_name=INDEX,
# MAGIC         columns=["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"],
# MAGIC         query_text=query,
# MAGIC         query_type="HYBRID",
# MAGIC         num_results=limit,
# MAGIC     )
# MAGIC     rows = (r.result.data_array or []) if r.result else []
# MAGIC     cols = [c.name for c in r.manifest.columns] if r.manifest else []
# MAGIC     out = [dict(zip(cols, row)) for row in rows]
# MAGIC     # Multi-component complaints yield sibling chunks (I-023); dedupe by complaint.
# MAGIC     seen, deduped = set(), []
# MAGIC     for d in out:
# MAGIC         if d.get("complaint_id") in seen:
# MAGIC             continue
# MAGIC         seen.add(d.get("complaint_id"))
# MAGIC         deduped.append(d)
# MAGIC     return deduped
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.TOOL)
# MAGIC def lookup_fleet_exposure(campaign_id: str) -> dict:
# MAGIC     """How many fleet vehicles a campaign touches, BY MATCH TIER.
# MAGIC
# MAGIC     The tier is returned as a first-class field, not a footnote, because §7's
# MAGIC     deterministic guarantee applies to EXACT only.
# MAGIC     """
# MAGIC     rows = _run_sql(
# MAGIC         f"""
# MAGIC             SELECT match_basis, COUNT(DISTINCT vin) AS vehicles,
# MAGIC                    COUNT(DISTINCT depot_id) AS depots
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_fleet_exposure
# MAGIC             WHERE campaign_number = :cid GROUP BY match_basis
# MAGIC         """,
# MAGIC         # Typed parameter objects, not dicts: the SDK calls .as_dict() on these and a
# MAGIC         # plain dict raises AttributeError. Parameterised, never interpolated —
# MAGIC         # campaign_id reaches this tool from model output.
# MAGIC         [StatementParameterListItem(name="cid", value=campaign_id)],
# MAGIC     )
# MAGIC     tiers = {r[0]: {"vehicles": int(r[1]), "depots": int(r[2])} for r in rows}
# MAGIC     return {
# MAGIC         "campaign_id": campaign_id,
# MAGIC         "by_match_tier": tiers,
# MAGIC         # Only meaningful because _run_sql raises rather than returning [] on failure:
# MAGIC         # an empty result here is a measured absence, not an unnoticed error.
# MAGIC         "no_vehicles_matched": not tiers,
# MAGIC         "exact_is_deterministic": True,
# MAGIC         "note": "MODEL_VARIANT matches are probabilistic and require confirmation.",
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.TOOL)
# MAGIC def lookup_fleet_models(make: str = None) -> dict:
# MAGIC     """What this fleet actually operates, in the fleet's OWN spelling.
# MAGIC
# MAGIC     Every other read tool is keyed by `campaign_id`, so nothing answered "what does this
# MAGIC     fleet run?" — the agent was asked to name a scope in a vocabulary it had no way to
# MAGIC     inspect. Observed live 2026-09-08: asked to open a steering signal for the RAM 2500,
# MAGIC     it stalled to ask whether to widen to the Dodge 2500/3500 cluster. This fleet holds
# MAGIC     ZERO Dodge vehicles, so that scope would have written a signal affecting nobody.
# MAGIC
# MAGIC     `gold_fleet_vehicle` is the correct source, not merely a convenient one: Lakebase's
# MAGIC     `fleetguard_vehicle` is loaded from it column-for-column, and `agent_actions.py`
# MAGIC     counts a new signal's fleet exposure by matching against that table. The spellings
# MAGIC     returned here are therefore exactly the strings the write path will match on, which
# MAGIC     is what makes I-030's NHTSA-vs-vPIC vocabulary gap avoidable at the source rather
# MAGIC     than only repairable afterwards by the MODEL_VARIANT backstop (I-075).
# MAGIC
# MAGIC     The make roster is returned whether or not `make` was supplied, so a model asking
# MAGIC     about a make the fleet does not own sees the real alternatives in the same result
# MAGIC     instead of having to guess a second time.
# MAGIC     """
# MAGIC     mk = (make or "").strip().upper()
# MAGIC     # Bound only when there is a filter: an unused parameter marker is an error, and a
# MAGIC     # NULL-tolerant `:mk IS NULL OR ...` predicate gives the planner nothing to infer a
# MAGIC     # type from (the same class of failure as agent_actions.py's AmbiguousParameter).
# MAGIC     where = "WHERE UPPER(make) = :mk" if mk else ""
# MAGIC     params = [StatementParameterListItem(name="mk", value=mk)] if mk else []
# MAGIC
# MAGIC     makes = _run_sql(
# MAGIC         f"""
# MAGIC             SELECT make, COUNT(DISTINCT vin) AS vehicles
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_fleet_vehicle
# MAGIC             GROUP BY make ORDER BY vehicles DESC
# MAGIC         """,
# MAGIC         [],
# MAGIC     )
# MAGIC     rows = _run_sql(
# MAGIC         f"""
# MAGIC             SELECT make, model, COUNT(DISTINCT vin) AS vehicles,
# MAGIC                    MIN(model_year) AS year_from, MAX(model_year) AS year_to
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_fleet_vehicle
# MAGIC             {where}
# MAGIC             GROUP BY make, model ORDER BY vehicles DESC
# MAGIC         """,
# MAGIC         params,
# MAGIC     )
# MAGIC     known = {r[0] for r in makes}
# MAGIC     return {
# MAGIC         "total_vehicles": sum(int(r[1]) for r in makes),
# MAGIC         "fleet_makes": [{"make": r[0], "vehicles": int(r[1])} for r in makes],
# MAGIC         "make_filter": mk or None,
# MAGIC         # Distinguishable states, per the rule that every tool must separate "I looked
# MAGIC         # and found nothing" from "I could not look" — _run_sql raises on failure, so an
# MAGIC         # empty list here is a measured absence and can be reported as one.
# MAGIC         "make_in_fleet": (mk in known) if mk else None,
# MAGIC         "models": [
# MAGIC             {
# MAGIC                 "make": r[0], "model": r[1], "vehicles": int(r[2]),
# MAGIC                 "year_from": int(r[3]), "year_to": int(r[4]),
# MAGIC             }
# MAGIC             for r in rows
# MAGIC         ],
# MAGIC         "vocabulary_note": "These are the fleet registry's own spellings (vPIC-sourced). "
# MAGIC                            "NHTSA complaint and recall text spells some models "
# MAGIC                            "differently — `F-250 SD` there is `F-250` here. Pass THESE "
# MAGIC                            "strings to open_defect_signal.",
# MAGIC         "model_names_are_not_unique": "SPRINTER is operated under two different makes, "
# MAGIC                                       "so a model name alone does not identify a series.",
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.TOOL)
# MAGIC def lookup_emerging_signals(fleet_only: bool = True, limit: int = 10) -> dict:
# MAGIC     """Defect ramps this system DETECTED that NHTSA has not acted on.
# MAGIC
# MAGIC     This is the proactive half — the project's actual claim. It is also the tool most
# MAGIC     likely to be over-read, so it returns the counts alongside the rows: "N detected
# MAGIC     across NHTSA, M touching this fleet" is a different statement from either number
# MAGIC     alone, and a bare list invites the model to imply the fleet is affected when it is
# MAGIC     not.
# MAGIC
# MAGIC     `harm_share` is descriptive triage. It plays NO part in whether a signal fires --
# MAGIC     the detector is pure volume anomaly (I-051) -- so it must never be reported as a
# MAGIC     confidence or a severity score.
# MAGIC     """
# MAGIC     where = "WHERE fleet_vehicles > 0" if fleet_only else ""
# MAGIC     # LIMIT cannot be bound as a parameter here: StatementParameterListItem sends the
# MAGIC     # value as a STRING, and Spark rejects it -- INVALID_LIMIT_LIKE_EXPRESSION.DATA_TYPE.
# MAGIC     # Coerced to a bounded int instead. This is safe where string interpolation would
# MAGIC     # not be: after int() the value cannot carry SQL, whatever the model passed.
# MAGIC     lim = max(1, min(int(limit), 50))
# MAGIC     rows = _run_sql(
# MAGIC         f"""
# MAGIC             SELECT series_key, make, model, comp_top, run_end, max_z,
# MAGIC                    complaints_in_run, harm_share, fleet_vehicles, is_live
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_emerging_signal
# MAGIC             {where}
# MAGIC             ORDER BY fleet_vehicles DESC, run_end DESC, max_z DESC
# MAGIC             LIMIT {lim}
# MAGIC         """,
# MAGIC         [],
# MAGIC     )
# MAGIC     counts = _run_sql(
# MAGIC         f"""
# MAGIC             SELECT COUNT(*), SUM(CASE WHEN is_live THEN 1 ELSE 0 END),
# MAGIC                    SUM(CASE WHEN fleet_vehicles > 0 THEN 1 ELSE 0 END),
# MAGIC                    MAX(as_of_month)
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_emerging_signal
# MAGIC         """,
# MAGIC         [],
# MAGIC     )
# MAGIC     c = counts[0] if counts else [0, 0, 0, None]
# MAGIC     return {
# MAGIC         "detected_total": int(c[0]),
# MAGIC         "still_firing": int(c[1] or 0),
# MAGIC         "affecting_this_fleet": int(c[2] or 0),
# MAGIC         "data_through": c[3],
# MAGIC         "signals": [
# MAGIC             {
# MAGIC                 "series": r[0], "make": r[1], "model": r[2], "component": r[3],
# MAGIC                 "last_fired": r[4], "peak_z": float(r[5]) if r[5] is not None else None,
# MAGIC                 "complaints_in_run": int(r[6]), "harm_share": r[7],
# MAGIC                 "fleet_vehicles": int(r[8]), "still_firing": r[9],
# MAGIC             }
# MAGIC             for r in rows
# MAGIC         ],
# MAGIC         "what_this_is": "Statistical anomalies detected by FleetGuard. NOT recalls, NOT "
# MAGIC                         "NHTSA investigations, NOT confirmed defects. Detection rate is "
# MAGIC                         "16.0% vs 11.1% on a matched control.",
# MAGIC         "harm_share_note": "Descriptive only. Not an input to detection, not a confidence.",
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.TOOL)
# MAGIC def propose_service_campaign(campaign_id: str, rationale: str) -> dict:
# MAGIC     """PROPOSE a campaign for human approval. Does NOT launch anything.
# MAGIC
# MAGIC     Returns a proposal object only. The agent has no grant on
# MAGIC     fleetguard_work_order — launching happens through the console's approval gate,
# MAGIC     where the approver's identity is recorded (§5.3).
# MAGIC     """
# MAGIC     exposure = lookup_fleet_exposure(campaign_id)
# MAGIC     exact = exposure["by_match_tier"].get("EXACT", {}).get("vehicles", 0)
# MAGIC     return {
# MAGIC         "status": "PROPOSED_AWAITING_HUMAN_APPROVAL",
# MAGIC         "campaign_id": campaign_id,
# MAGIC         "rationale": rationale,
# MAGIC         "exact_vehicles": exact,
# MAGIC         "exposure": exposure["by_match_tier"],
# MAGIC         "next_step": "A fleet safety manager must approve this in the console. "
# MAGIC                      "No work orders have been created.",
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC # Marks a tool result the CONSOLE must execute rather than the agent.
# MAGIC ACTION_KEY = "__fleetguard_action__"
# MAGIC # Prefix on the extra output item that carries an envelope out to the caller. Must match
# MAGIC # ACTION_SENTINEL in app/backend/fleetguard_api/routers/chat.py, which strips it.
# MAGIC ACTION_SENTINEL = "__FLEETGUARD_ACTION__"
# MAGIC
# MAGIC
# MAGIC @mlflow.trace(span_type=SpanType.TOOL)
# MAGIC def open_defect_signal(
# MAGIC     component: str,
# MAGIC     rationale: str,
# MAGIC     make: str = None,
# MAGIC     model: str = None,
# MAGIC     complaint_count: int = None,
# MAGIC ) -> dict:
# MAGIC     """REQUEST that a defect signal be opened for tracking.
# MAGIC
# MAGIC     This is the agent's one write action, and it is deliberately indirect. The serving
# MAGIC     endpoint has no path to Lakebase — it holds an auto-provisioned service principal and
# MAGIC     only vector-search / SQL-warehouse resources — so this tool cannot perform the INSERT
# MAGIC     itself. It returns a REQUESTED action envelope; the FastAPI console executes it under
# MAGIC     the **requesting user's own OBO token**, which is strictly better attribution than a
# MAGIC     service-principal write would give.
# MAGIC
# MAGIC     The signal is an *observation* — "this pattern looks worth tracking" — not a dispatch.
# MAGIC     The agent still has no route to fleetguard_work_order; launching a campaign remains
# MAGIC     behind the human approver gate.
# MAGIC     """
# MAGIC     return {
# MAGIC         ACTION_KEY: "open_defect_signal",
# MAGIC         "status": "REQUESTED",
# MAGIC         "params": {
# MAGIC             "component": component,
# MAGIC             "rationale": rationale,
# MAGIC             "make": make,
# MAGIC             "model": model,
# MAGIC             "complaint_count": complaint_count,
# MAGIC         },
# MAGIC         "note": "Requested only. The console performs the write and confirms it; do not "
# MAGIC                 "tell the user it is saved.",
# MAGIC     }
# MAGIC
# MAGIC
# MAGIC TOOLS = {
# MAGIC     "search_complaints": search_complaints,
# MAGIC     "lookup_fleet_exposure": lookup_fleet_exposure,
# MAGIC     "lookup_fleet_models": lookup_fleet_models,
# MAGIC     "lookup_emerging_signals": lookup_emerging_signals,
# MAGIC     "propose_service_campaign": propose_service_campaign,
# MAGIC     "open_defect_signal": open_defect_signal,
# MAGIC }
# MAGIC
# MAGIC TOOL_SPECS = [
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "search_complaints",
# MAGIC             "description": "Search 1.75M NHTSA complaint narratives by symptom or component.",
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {
# MAGIC                     "query": {"type": "string", "description": "Symptom or component text"},
# MAGIC                     "limit": {"type": "integer", "default": 5},
# MAGIC                 },
# MAGIC                 "required": ["query"],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "lookup_fleet_exposure",
# MAGIC             "description": "Fleet vehicles affected by a recall campaign, broken down by match tier.",
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {"campaign_id": {"type": "string"}},
# MAGIC                 "required": ["campaign_id"],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "lookup_fleet_models",
# MAGIC             "description": (
# MAGIC                 "What makes and models this fleet actually operates, with vehicle counts "
# MAGIC                 "and year ranges, in the fleet registry's own spelling. Call this BEFORE "
# MAGIC                 "naming a make or model in open_defect_signal, and whenever you are about "
# MAGIC                 "to ask the user what scope to use — it usually answers the question. "
# MAGIC                 "Omit `make` to get the whole roster."
# MAGIC             ),
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {
# MAGIC                     "make": {
# MAGIC                         "type": "string",
# MAGIC                         "description": "Optional make to filter to, e.g. 'RAM'.",
# MAGIC                     }
# MAGIC                 },
# MAGIC                 "required": [],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "lookup_emerging_signals",
# MAGIC             "description": (
# MAGIC                 "Defect ramps FleetGuard detected before NHTSA acted. These are NOT "
# MAGIC                 "recalls and NOT investigations. Use for 'what is emerging', 'anything "
# MAGIC                 "new', 'early warning' questions."
# MAGIC             ),
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {
# MAGIC                     "fleet_only": {"type": "boolean", "default": True},
# MAGIC                     "limit": {"type": "integer", "default": 10},
# MAGIC                 },
# MAGIC                 "required": [],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "propose_service_campaign",
# MAGIC             "description": "Propose a service campaign for HUMAN approval. Does not launch it.",
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {
# MAGIC                     "campaign_id": {"type": "string"},
# MAGIC                     "rationale": {"type": "string"},
# MAGIC                 },
# MAGIC                 "required": ["campaign_id", "rationale"],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC     {
# MAGIC         "type": "function",
# MAGIC         "function": {
# MAGIC             "name": "open_defect_signal",
# MAGIC             "description": (
# MAGIC                 "Request that a defect signal be opened and tracked in the fleet console. "
# MAGIC                 "Use when complaint evidence suggests a component problem worth watching "
# MAGIC                 "that is not already a recall or an open investigation. This records an "
# MAGIC                 "observation for the safety team; it does NOT create work orders and does "
# MAGIC                 "not dispatch anyone. The console performs the write and confirms it."
# MAGIC             ),
# MAGIC             "parameters": {
# MAGIC                 "type": "object",
# MAGIC                 "properties": {
# MAGIC                     "component": {
# MAGIC                         "type": "string",
# MAGIC                         "description": "NHTSA component name, e.g. 'STEERING'.",
# MAGIC                     },
# MAGIC                     "rationale": {
# MAGIC                         "type": "string",
# MAGIC                         "description": "Why this is worth tracking, grounded in tool results.",
# MAGIC                     },
# MAGIC                     "make": {"type": "string"},
# MAGIC                     "model": {"type": "string"},
# MAGIC                     "complaint_count": {
# MAGIC                         "type": "integer",
# MAGIC                         "description": "Supporting complaints found, if known.",
# MAGIC                     },
# MAGIC                 },
# MAGIC                 "required": ["component", "rationale"],
# MAGIC             },
# MAGIC         },
# MAGIC     },
# MAGIC ]
# MAGIC
# MAGIC
# MAGIC class FleetGuardAgent(ResponsesAgent):
# MAGIC     def _client(self):
# MAGIC         return w.serving_endpoints.get_open_ai_client()
# MAGIC
# MAGIC     def _run(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
# MAGIC         """Returns (emitted_messages, requested_actions).
# MAGIC
# MAGIC         `requested_actions` are envelopes this process could not execute — see
# MAGIC         `open_defect_signal`. They are collected from the *tool return value*, never
# MAGIC         parsed out of model prose, so a model that hallucinates an action cannot cause
# MAGIC         one: the envelope only exists if the tool function actually ran.
# MAGIC         """
# MAGIC         client = self._client()
# MAGIC         convo = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
# MAGIC         emitted: list[dict] = []
# MAGIC         actions: list[dict] = []
# MAGIC
# MAGIC         # Bounded loop: an unbounded one can burn tokens indefinitely on a tool error.
# MAGIC         for _ in range(6):
# MAGIC             resp = client.chat.completions.create(
# MAGIC                 model=LLM_ENDPOINT, messages=convo, tools=TOOL_SPECS
# MAGIC             )
# MAGIC             msg = resp.choices[0].message
# MAGIC             convo.append(msg.model_dump(exclude_none=True))
# MAGIC
# MAGIC             if not msg.tool_calls:
# MAGIC                 emitted.append({"role": "assistant", "content": msg.content or ""})
# MAGIC                 return emitted, actions
# MAGIC
# MAGIC             for call in msg.tool_calls:
# MAGIC                 fn = TOOLS.get(call.function.name)
# MAGIC                 try:
# MAGIC                     args = json.loads(call.function.arguments or "{}")
# MAGIC                     result = fn(**args) if fn else {"error": f"unknown tool {call.function.name}"}
# MAGIC                 except Exception as exc:  # surface the failure to the model, do not crash
# MAGIC                     result = {"error": f"{type(exc).__name__}: {exc}"}
# MAGIC                 if isinstance(result, dict) and ACTION_KEY in result:
# MAGIC                     actions.append(result)
# MAGIC                 convo.append(
# MAGIC                     {
# MAGIC                         "role": "tool",
# MAGIC                         "tool_call_id": call.id,
# MAGIC                         "content": json.dumps(result, default=str)[:6000],
# MAGIC                     }
# MAGIC                 )
# MAGIC
# MAGIC         emitted.append(
# MAGIC             {"role": "assistant", "content": "Stopped after 6 tool rounds without a final answer."}
# MAGIC         )
# MAGIC         return emitted, actions
# MAGIC
# MAGIC     def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
# MAGIC         # mlflow's Message type defaults `type` to the literal "message" (not None), so
# MAGIC         # `exclude_none=True` alone does not strip it - every item picks up a synthetic
# MAGIC         # "type" key that the underlying chat-completions endpoint rejects once a second
# MAGIC         # turn replays an assistant message back as input. Filter to what the completions
# MAGIC         # API actually accepts. Do NOT apply this same filter to the model_dump in _run()
# MAGIC         # above - that one carries tool_calls the model needs to see on its own turn.
# MAGIC         ALLOWED_KEYS = {"role", "content"}
# MAGIC         msgs = [
# MAGIC             {k: v for k, v in m.model_dump(exclude_none=True).items() if k in ALLOWED_KEYS}
# MAGIC             for m in request.input
# MAGIC         ]
# MAGIC         out, actions = self._run(msgs)
# MAGIC         items = [
# MAGIC             self.create_text_output_item(text=m["content"], id=str(i))
# MAGIC             for i, m in enumerate(out)
# MAGIC         ]
# MAGIC         # Requested actions ride out as extra text items behind a sentinel, built here in
# MAGIC         # Python from the tool's own return value — the model never formats them. A custom
# MAGIC         # output-item type would be cleaner but is not guaranteed across mlflow versions,
# MAGIC         # whereas a sentinel-prefixed text item survives anything. routers/chat.py strips
# MAGIC         # these before the reply is shown, so ACTION_SENTINEL must stay in sync with it.
# MAGIC         for j, action in enumerate(actions):
# MAGIC             items.append(
# MAGIC                 self.create_text_output_item(
# MAGIC                     text=f"{ACTION_SENTINEL} {json.dumps(action, default=str)}",
# MAGIC                     id=f"action-{j}",
# MAGIC                 )
# MAGIC             )
# MAGIC         return ResponsesAgentResponse(output=items)
# MAGIC
# MAGIC     def predict_stream(
# MAGIC         self, request: ResponsesAgentRequest
# MAGIC     ) -> Generator[ResponsesAgentStreamEvent, None, Any]:
# MAGIC         for item in self.predict(request).output:
# MAGIC             yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)
# MAGIC
# MAGIC
# MAGIC set_model(FleetGuardAgent())

# COMMAND ----------

import yaml

WAREHOUSE_ID = "b15d3d6f837ba428"
with open("agent_config.yaml", "w") as f:
    yaml.safe_dump(
        {
            "catalog": CATALOG,
            "schema": SCHEMA,
            "llm_endpoint": LLM_ENDPOINT,
            "warehouse_id": WAREHOUSE_ID,
        },
        f,
    )
with open("agent_config.yaml") as _f:
    print(_f.read())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Smoke test before logging
# MAGIC
# MAGIC Exercising the tools directly catches a broken query or index name here, rather than
# MAGIC inside a serving container where the traceback is far less accessible.

# COMMAND ----------

import fleetguard_agent as fga

hits = fga.search_complaints("brake pedal went to the floor", limit=3)
print(f"search_complaints -> {len(hits)} hits")
for h in hits:
    print(
        "   ", str(h.get("component"))[:44], "|", str(h.get("chunk_text"))[:60].replace("\n", " ")
    )

exp = fga.lookup_fleet_exposure("17V629000")
print(f"\nlookup_fleet_exposure -> {exp['by_match_tier']}")

# Ground truth, verified directly against gold_fleet_exposure on 2026-09-02: campaign
# 17V629000 is EXACT-matched to 25 vehicles across 22 depots. Asserting the number — not
# merely that the call returned — is what would have caught I-050 before it reached a
# served endpoint, where a silent empty result read as "no vehicles are affected".
_exact = exp["by_match_tier"].get("EXACT", {})
assert _exact.get("vehicles") == 25, f"expected 25 EXACT vehicles, got {exp['by_match_tier']}"
assert _exact.get("depots") == 22, f"expected 22 depots, got {exp['by_match_tier']}"

# The fleet's own vocabulary. Pinned to values measured directly against
# gold_fleet_vehicle on 2026-09-08: 15 makes, 47 make/model combos (46 distinct model
# names — SPRINTER appears under two makes), 20,000 vehicles.
veh = fga.lookup_fleet_models()
print(
    f"\nlookup_fleet_models -> {len(veh['fleet_makes'])} makes, "
    f"{len(veh['models'])} make/model combos, {veh['total_vehicles']:,} vehicles"
)
for x in veh["models"][:5]:
    print(
        f"    {x['make']} {x['model']} | {x['vehicles']} vehicles | {x['year_from']}-{x['year_to']}"
    )

assert veh["total_vehicles"] == 20_000, f"expected 20,000 vehicles, got {veh['total_vehicles']}"
assert len(veh["fleet_makes"]) == 15, f"expected 15 makes, got {len(veh['fleet_makes'])}"
assert len(veh["models"]) == 47, f"expected 47 make/model combos, got {len(veh['models'])}"

# The regression this tool exists for. Live 2026-09-08 the agent offered to scope a RAM
# 2500 signal to the "Dodge 2500/3500 cluster"; the fleet holds no DODGE at all, so that
# signal would have been recorded against zero vehicles. Assert both directions: the make
# it hallucinated is absent, and the make it should have used is present.
_makes = {m["make"] for m in veh["fleet_makes"]}
assert "DODGE" not in _makes, "DODGE is in the fleet now — rewrite the rule-8 example"
assert "RAM" in _makes, "RAM missing from the fleet roster"

# The spelling gap itself (I-030/I-075): the fleet says `F-250`, NHTSA says `F-250 SD`.
ford = fga.lookup_fleet_models(make="ford")  # lower case on purpose — the model will do this
assert ford["make_in_fleet"] is True, "case-insensitive make lookup failed"
_f250 = [m for m in ford["models"] if m["model"] == "F-250"]
assert _f250 and _f250[0]["vehicles"] == 2116, f"expected 2,116 F-250s, got {ford['models']}"
assert not any(m["model"] == "F-250 SD" for m in ford["models"]), (
    "the fleet registry now uses NHTSA's spelling — the vocabulary_note is stale"
)

# A make the fleet does not operate must be DISTINGUISHABLE from a failed lookup, and must
# still hand back the real roster so the model can correct itself in one turn.
absent = fga.lookup_fleet_models(make="DODGE")
assert absent["make_in_fleet"] is False, "an absent make must report make_in_fleet=False"
assert absent["models"] == [], "an absent make must return no models"
assert absent["fleet_makes"], "the roster must come back even when the filter matches nothing"

sig = fga.lookup_emerging_signals(fleet_only=True, limit=5)
print(
    f"\nlookup_emerging_signals -> detected={sig['detected_total']} "
    f"live={sig['still_firing']} fleet={sig['affecting_this_fleet']} through={sig['data_through']}"
)
for x in sig["signals"]:
    print(
        f"    {x['make']} {x['model']} | {x['component']} | z={x['peak_z']} | fleet={x['fleet_vehicles']}"
    )

# Pinned to the values measured 2026-09-02 against gold_emerging_signal. Asserting the
# NUMBERS, not merely that the call returned — the whole point of I-050. If the signals
# table is rebuilt these will move, and that should force a deliberate edit here.
assert sig["detected_total"] == 48, f"expected 48 signals, got {sig['detected_total']}"
assert sig["affecting_this_fleet"] == 2, (
    f"expected 2 fleet-relevant, got {sig['affecting_this_fleet']}"
)
assert sig["signals"], "fleet_only returned nothing — the proactive demo would be empty"
assert sig["signals"][0]["fleet_vehicles"] == 1256, "expected RAM 2500 (1,256 vehicles) first"

prop = fga.propose_service_campaign("17V629000", "Park It steering defect")
print(f"\npropose_service_campaign -> {prop['status']}  exact={prop['exact_vehicles']}")
assert prop["status"] == "PROPOSED_AWAITING_HUMAN_APPROVAL", "agent must not self-launch"
assert prop["exact_vehicles"] == 25, "proposal lost the exposure count"

# The write action. It must REQUEST, never claim to have written — this tool runs inside the
# serving endpoint, which has no Lakebase path at all, so a "saved" status here would be a
# lie by construction.
act = fga.open_defect_signal(component="STEERING", rationale="smoke test", make="RAM")
print(f"open_defect_signal -> {act['status']}  action={act[fga.ACTION_KEY]}")
assert act["status"] == "REQUESTED", "the agent must not claim to have performed the write"
assert act[fga.ACTION_KEY] == "open_defect_signal", "envelope lost its action name"
assert act["params"]["component"] == "STEERING", "envelope lost its parameters"
print("\nsmoke tests passed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log the agent (models-from-code)
# MAGIC
# MAGIC `resources` is the load-bearing argument. It tells Model Serving which endpoints and
# MAGIC indexes the deployed model needs, so the endpoint's own credential is granted them at
# MAGIC startup. Omit one and the agent logs cleanly, deploys cleanly, and then fails at the
# MAGIC first tool call with a permission error — a failure that only appears in production.
# MAGIC
# MAGIC The SQL warehouse resource class is name-checked at runtime rather than assumed: the
# MAGIC `mlflow.models.resources` vocabulary has changed across versions, and this project has
# MAGIC already been bitten twice by asserting a name from memory.

# COMMAND ----------

import importlib.metadata as _md

import mlflow
from mlflow.models import resources as _res

print("Databricks resource classes available in mlflow", mlflow.__version__, ":")
print("  " + ", ".join(sorted(n for n in dir(_res) if n.startswith("Databricks"))))

# Every resource the agent touches must be declared. Automatic authentication passthrough
# grants the endpoint's credential exactly what is listed here and nothing else — and a
# missing grant does NOT surface as a crash. It surfaces as a FAILED statement whose result
# is None, which the first version of the exposure tool read as "zero vehicles affected"
# (I-050). Declaring the warehouse is not enough: the warehouse is the *engine*, the table
# is the *data*, and they are separate grants.
resources = [
    _res.DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT),
    _res.DatabricksVectorSearchIndex(index_name=INDEX),
    _res.DatabricksSQLWarehouse(warehouse_id=WAREHOUSE_ID),
    _res.DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.gold_fleet_exposure"),
    # Every table the agent reads must be declared, or the query FAILS SILENTLY (I-050).
    _res.DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.gold_emerging_signal"),
    # lookup_fleet_models. Omitting this would not break the build or the deploy — it would
    # make the agent answer "I could not find the fleet's models" at demo time, which is the
    # exact shape of I-050 in a tool added to prevent a different silent-zero bug.
    _res.DatabricksTable(table_name=f"{CATALOG}.{SCHEMA}.gold_fleet_vehicle"),
]

for r in resources:
    print("resource:", r)

# Pin to what actually ran here, rather than to a range that may resolve differently in the
# serving container six weeks from now.
PIP = [
    f"mlflow=={_md.version('mlflow')}",
    f"databricks-sdk=={_md.version('databricks-sdk')}",
    f"openai=={_md.version('openai')}",
]
print("\npip_requirements:", PIP)

# COMMAND ----------

INPUT_EXAMPLE = {
    "input": [
        {
            "role": "user",
            "content": "Which fleet vehicles does recall 17V629000 affect, and should we act?",
        }
    ]
}

with mlflow.start_run(run_name="fleetguard-agent-v1") as run:
    model_info = mlflow.pyfunc.log_model(
        python_model="fleetguard_agent.py",
        name="agent",
        resources=resources,
        model_config={
            "catalog": CATALOG,
            "schema": SCHEMA,
            "llm_endpoint": LLM_ENDPOINT,
            "warehouse_id": WAREHOUSE_ID,
        },
        input_example=INPUT_EXAMPLE,
        pip_requirements=PIP,
    )

print(f"model_uri : {model_info.model_uri}")
print(f"run_id    : {run.info.run_id}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validate the logged artifact before registering
# MAGIC
# MAGIC Loading it back proves the file executes standalone and that `ModelConfig` resolves from
# MAGIC the *packaged* config rather than the `agent_config.yaml` sitting in the notebook's
# MAGIC working directory — the failure mode where an agent works in the notebook and dies in
# MAGIC the container.

# COMMAND ----------

from mlflow.models import validate_serving_input

validate_serving_input(model_info.model_uri, INPUT_EXAMPLE)
print("serving input schema OK")

loaded = mlflow.pyfunc.load_model(model_info.model_uri)
reply = loaded.predict(INPUT_EXAMPLE)

# Sentinel-prefixed items are requested actions for the console, not prose — strip them
# the same way routers/chat.py does, so this smoke test sees what a user would see.
_ACTION_SENTINEL = "__FLEETGUARD_ACTION__"
texts = [
    c.get("text", "")
    for item in reply["output"]
    for c in item.get("content", [])
    if c.get("type") == "output_text" and not c.get("text", "").startswith(_ACTION_SENTINEL)
]
answer = "\n".join(texts)
print("\n--- agent reply ---\n", answer[:1500])

assert answer.strip(), "agent returned no text"
# The proposal tool must never be presented as a launch. If the model starts claiming it
# dispatched work orders, that is the single most damaging regression this system can have.
assert "work order" not in answer.lower() or "approv" in answer.lower(), (
    "agent mentioned work orders without mentioning approval"
)
print("\nround-trip validation passed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Register in Unity Catalog
# MAGIC
# MAGIC Registration is free and reversible. It is deliberately separated from `agents.deploy()`
# MAGIC below, which is neither.

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")

registered = mlflow.register_model(model_uri=model_info.model_uri, name=MODEL_NAME)
print(f"registered: {registered.name}  version={registered.version}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deploy — gated, because this one bills
# MAGIC
# MAGIC `agents.deploy()` provisions a Model Serving endpoint that bills for as long as it is
# MAGIC running, and enables inference tables (E-04). This workspace is a shared bootcamp
# MAGIC metastore and the project has already been surprised once by compute that started
# MAGIC billing the moment it was created.
# MAGIC
# MAGIC So the deploy is behind a widget that defaults to `false`. Running this notebook end to
# MAGIC end logs, validates and registers the agent — and stops. Flip `deploy` to `true`
# MAGIC deliberately, with the demo window in mind, and **stop the endpoint when the demo is
# MAGIC over**.

# COMMAND ----------

dbutils.widgets.dropdown("deploy", "false", ["false", "true"], "Create serving endpoint (BILLS)")
DEPLOY = dbutils.widgets.get("deploy") == "true"

if not DEPLOY:
    print("deploy=false — skipping agents.deploy(). Nothing is billing.")
    print(
        f"To deploy later: run this job with deploy=true, or deploy {MODEL_NAME} "
        f"version {registered.version} from the UI."
    )
else:
    from databricks import agents

    deployment = agents.deploy(model_name=MODEL_NAME, model_version=registered.version)
    print(f"endpoint : {deployment.endpoint_url}")
    print(f"review   : {getattr(deployment, 'review_app_url', 'n/a')}")
    print("\nThis endpoint is now billing. Stop it when the demo is done.")
