"""The agent chat panel — advisory, and deliberately powerless.

This router proxies a question to the deployed `ResponsesAgent` endpoint and returns its
answer. It is **beside** the queue, never in front of it (E-11): the recall → exposure →
approval path stays deterministic SQL, and nothing an LLM says can create a work order. The
agent's own `propose_service_campaign` tool returns a proposal object; the only code that
writes work orders is `routers/approval.py`, behind the human gate.

The call carries the **caller's** token, not the app's. A chat panel that queried the agent
as a service principal would let any signed-in user reach data their own grants exclude —
the exact bypass the auth seam exists to prevent.

Non-streaming on purpose. Unity AI Gateway output guardrails do not apply to streamed
responses (I-015), so a streaming panel would silently lose the PII output check that §4.5
claims. Answers take a few seconds; that is an acceptable price for a claim that stays true.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .. import agent_actions
from ..deps import CurrentPrincipal

router = APIRouter(tags=["agent"])

# Named by `agents.deploy()` as agents_<catalog>-<schema>-<model>. Overridable so a rebuilt
# endpoint does not require a code change.
AGENT_ENDPOINT = os.getenv(
    "FLEETGUARD_AGENT_ENDPOINT",
    "agents_bootcamp_students-fleetguard-fleetguard_agent",
)

# The agent runs a bounded tool loop — retrieval, exposure lookup, then a reply — so a
# single turn can legitimately take tens of seconds. A short timeout here would surface as
# a phantom failure while the endpoint was still working correctly.
AGENT_TIMEOUT_S = 120.0


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=20)


class ChatReply(BaseModel):
    reply: str
    endpoint: str
    # Present only when the agent requested a write and this console performed it. The UI
    # renders this separately from `reply`, because the committed row — not the model's
    # prose — is what actually happened.
    action_result: agent_actions.ActionResult | None = None


def _extract(payload: dict) -> tuple[str, list[dict]]:
    """Split a Responses-API payload into (visible text, requested actions).

    A `ResponsesAgent` returns `output` — a list of items. Ordinary items are prose. Items
    whose text begins with `agent_actions.ACTION_SENTINEL` are machine-readable envelopes the
    agent emitted for this console to execute (it has no Lakebase access itself); they are
    **stripped from the reply**, never shown, and never left in the text the frontend replays
    as conversation history — otherwise the envelope would re-enter the model's context on the
    next turn and could be acted on twice.
    """
    parts: list[str] = []
    actions: list[dict] = []
    for item in payload.get("output", []):
        for chunk in item.get("content", []) or []:
            if chunk.get("type") != "output_text":
                continue
            text = chunk.get("text", "")
            envelope = agent_actions.parse_envelope(text)
            if envelope is not None:
                actions.append(envelope)
            else:
                parts.append(text)
    return "".join(parts).strip(), actions


@router.post("/chat", response_model=ChatReply)
def chat(principal: CurrentPrincipal, req: ChatRequest) -> ChatReply:
    host = os.getenv("DATABRICKS_HOST", "").rstrip("/")
    if not host:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent is not configured (DATABRICKS_HOST unset).",
        )

    # `app-login` principals (Render) are signed in but carry no Databricks token by design
    # (snapshot.py's whole reason for existing). Checked explicitly, before the call, rather
    # than left to fail downstream: an empty bearer token still reaches the real serving
    # endpoint, comes back 401, and without this check that surfaces as a generic 502 —
    # exactly the confusing case the frontend's dedicated "assistant is offline" state exists
    # to avoid, and it would only ever fire for the 503 branch above, never this one.
    if not principal.token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This deployment has no Databricks credential to query the agent with.",
        )

    url = f"{host}/serving-endpoints/{AGENT_ENDPOINT}/invocations"
    body = {"input": [t.model_dump() for t in req.messages]}

    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {principal.token}"},
            timeout=AGENT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent endpoint unreachable: {type(exc).__name__}",
        ) from exc

    if resp.status_code == 404:
        # A stopped or deleted endpoint must read as "the assistant is offline", not as a
        # generic error the operator might mistake for "no exposure found".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent endpoint '{AGENT_ENDPOINT}' is not serving.",
        )
    if resp.status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have query permission on the agent endpoint.",
        )
    if resp.status_code >= 400:
        # The serving endpoint's own error body is the useful part — e.g. "the given
        # endpoint is stopped" vs. a malformed request are both bare 400s otherwise
        # indistinguishable from this message alone. Found 2026-09-04: a stopped endpoint
        # surfaced as an opaque "returned 400" with no way to tell it apart from a real bug.
        try:
            reason = resp.json().get("message", "")
        except ValueError:
            reason = ""

        # A *stopped* endpoint answers **400**, not 404 — so the 404 branch above never fires
        # for by far the most common real cause, and this fell through to the 502 below.
        # `Assistant.tsx` keys its "the assistant is offline" message on **503**, and its own
        # comment says "503 means the serving endpoint is stopped" — so that message was
        # unreachable for exactly the case it was written for, and a judge hitting a
        # scaled-down endpoint saw a raw 502 instead (I-093, measured 2026-09-09).
        #
        # Matching on the provider's message text is unlovely and deliberate: both cases are
        # bare 400s, so the body is the only signal there is. If the wording changes this
        # degrades to the old 502 — worse, not broken — and `tests/test_chat_offline.py`
        # pins the string actually observed from the endpoint.
        if resp.status_code == 400 and "stopped" in reason.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Agent endpoint '{AGENT_ENDPOINT}' is stopped.",
            )

        detail = f"Agent endpoint returned {resp.status_code}"
        if reason:
            detail += f": {reason}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    payload = resp.json()
    text, actions = _extract(payload)
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent returned no answer.",
        )

    # The serving endpoint's own request id, written to `fleetguard_agent_action.trace_id`
    # below. **This is what joins the audit trail to the observability trail** (E-03): it is
    # the key of `fleetguard_agent_payload`, the inference table `agents.deploy()` creates,
    # which holds the model's full request and response, token usage and latency. With it, one
    # SQL join in Unity Catalog answers "which human authorised this write" *and* "what did the
    # model actually see and say" together; without it those are two unrelated tables.
    #
    # `databricks_output.databricks_request_id` is the documented location; the top-level `id`
    # mirrors it (verified on a live call 2026-09-09) and is the fallback. Both absent is fine —
    # see the write below.
    request_id = (payload.get("databricks_output") or {}).get(
        "databricks_request_id"
    ) or payload.get("id")

    # Execute at most one action per turn. The agent's loop could in principle emit several,
    # but a chat turn that silently performs a batch of writes is not something an operator
    # can review — and nothing in the prompt asks for more than one. Extra envelopes are
    # dropped rather than executed; if that ever becomes a real pattern it should be a
    # deliberate design, not an emergent one.
    action_result = None
    if actions:
        # Deliberately NOT wrapped in a try/except that degrades to a plain reply: a failed
        # write must surface as an error status, not as the agent's prose saying it asked for
        # something while the console quietly did nothing.
        #
        # `trace_id` is passed but never *required*: if the endpoint returned no request id the
        # column is written NULL, exactly as before. An action that executed but could not be
        # traced is a far better outcome than an action refused because it could not be traced —
        # the write is the part with real-world consequences.
        action_result = agent_actions.execute(principal, actions[0], trace_id=request_id)

    return ChatReply(reply=text, endpoint=AGENT_ENDPOINT, action_result=action_result)
