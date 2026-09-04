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


def _extract_text(payload: dict) -> str:
    """Pull assistant text out of a Responses-API payload.

    A `ResponsesAgent` returns `output` — a list of items, which for a tool-using turn
    includes tool calls as well as the final message. Only `output_text` content is shown;
    tool-call plumbing is not something an operator should have to read.
    """
    parts: list[str] = []
    for item in payload.get("output", []):
        for chunk in item.get("content", []) or []:
            if chunk.get("type") == "output_text":
                parts.append(chunk.get("text", ""))
    return "".join(parts).strip()


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
        detail = f"Agent endpoint returned {resp.status_code}"
        if reason:
            detail += f": {reason}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    text = _extract_text(resp.json())
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent returned no answer.",
        )
    return ChatReply(reply=text, endpoint=AGENT_ENDPOINT)
