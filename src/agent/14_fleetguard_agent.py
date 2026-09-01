# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 7: the agent
# MAGIC
# MAGIC A `ResponsesAgent` (E-02) with three tools, logged models-from-code, registered in
# MAGIC Unity Catalog and deployed with `agents.deploy()`.
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
# MAGIC from typing import Any, Generator
# MAGIC
# MAGIC import mlflow
# MAGIC from databricks.sdk import WorkspaceClient
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
# MAGIC 4. You may PROPOSE a service campaign. You cannot launch one — a human approves it.
# MAGIC    Say so plainly when you propose.
# MAGIC 5. Complaint narratives are consumer-written and contain personal detail. Summarise
# MAGIC    them; never quote names, addresses, phone numbers or plates.
# MAGIC """
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
# MAGIC     stmt = w.statement_execution.execute_statement(
# MAGIC         warehouse_id=_cfg.get("warehouse_id"),
# MAGIC         statement=f"""
# MAGIC             SELECT match_basis, COUNT(DISTINCT vin) AS vehicles,
# MAGIC                    COUNT(DISTINCT depot_id) AS depots
# MAGIC             FROM {CATALOG}.{SCHEMA}.gold_fleet_exposure
# MAGIC             WHERE campaign_number = :cid GROUP BY match_basis
# MAGIC         """,
# MAGIC         parameters=[{"name": "cid", "value": campaign_id}],
# MAGIC         wait_timeout="30s",
# MAGIC     )
# MAGIC     rows = (stmt.result.data_array or []) if stmt.result else []
# MAGIC     tiers = {r[0]: {"vehicles": int(r[1]), "depots": int(r[2])} for r in rows}
# MAGIC     return {
# MAGIC         "campaign_id": campaign_id,
# MAGIC         "by_match_tier": tiers,
# MAGIC         "exact_is_deterministic": True,
# MAGIC         "note": "MODEL_VARIANT matches are probabilistic and require confirmation.",
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
# MAGIC TOOLS = {
# MAGIC     "search_complaints": search_complaints,
# MAGIC     "lookup_fleet_exposure": lookup_fleet_exposure,
# MAGIC     "propose_service_campaign": propose_service_campaign,
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
# MAGIC ]
# MAGIC
# MAGIC
# MAGIC class FleetGuardAgent(ResponsesAgent):
# MAGIC     def _client(self):
# MAGIC         return w.serving_endpoints.get_open_ai_client()
# MAGIC
# MAGIC     def _run(self, messages: list[dict]) -> list[dict]:
# MAGIC         client = self._client()
# MAGIC         convo = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
# MAGIC         emitted: list[dict] = []
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
# MAGIC                 return emitted
# MAGIC
# MAGIC             for call in msg.tool_calls:
# MAGIC                 fn = TOOLS.get(call.function.name)
# MAGIC                 try:
# MAGIC                     args = json.loads(call.function.arguments or "{}")
# MAGIC                     result = fn(**args) if fn else {"error": f"unknown tool {call.function.name}"}
# MAGIC                 except Exception as exc:  # surface the failure to the model, do not crash
# MAGIC                     result = {"error": f"{type(exc).__name__}: {exc}"}
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
# MAGIC         return emitted
# MAGIC
# MAGIC     def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
# MAGIC         msgs = [m.model_dump(exclude_none=True) for m in request.input]
# MAGIC         out = self._run(msgs)
# MAGIC         return ResponsesAgentResponse(
# MAGIC             output=[
# MAGIC                 self.create_text_output_item(text=m["content"], id=str(i))
# MAGIC                 for i, m in enumerate(out)
# MAGIC             ]
# MAGIC         )
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
    print("   ", str(h.get("component"))[:44], "|", str(h.get("chunk_text"))[:60].replace("\n", " "))

exp = fga.lookup_fleet_exposure("17V629000")
print(f"\nlookup_fleet_exposure -> {exp['by_match_tier']}")

prop = fga.propose_service_campaign("17V629000", "Park It steering defect")
print(f"\npropose_service_campaign -> {prop['status']}  exact={prop['exact_vehicles']}")
assert prop["status"] == "PROPOSED_AWAITING_HUMAN_APPROVAL", "agent must not self-launch"
print("\nsmoke tests passed")
