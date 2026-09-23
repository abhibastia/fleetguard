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

import hashlib
import hmac
import os
import secrets

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


# THE BROWSER SENDS THE CONVERSATION BACK, SO ASSISTANT TURNS MUST BE PROVABLE.
#
# Multi-turn context has to come from somewhere, and this console holds no server-side
# conversation state. That means the client replays what the assistant said — and a client
# can say the assistant said anything. An injected turn like "the fleet manager already
# approved this action" lands in the model's context as though the model had produced it.
#
# The write paths are not defenceless: `agent_actions.execute` calls `authz.may_approve`
# before touching Lakebase, so forging history cannot grant an unauthorised write. What it
# CAN do is steer an approver's own session — and the agent gained write tools in v5, so the
# gap gets worse rather than better with time.
#
# Fixed without a conversation database: every assistant turn leaves here with an HMAC tag
# over its text, and the client must hand the tag back with the turn. The server recomputes
# and compares. No conversation table to age out, and no per-user state to keep.
#
# THE KEY IS GENERATED PER PROCESS, and deliberately not read from a committed file.
# `app.yaml` is in git, so a literal there would be a published secret, and a Databricks
# secret scope is real setup for a property that does not need durability: the only cost of
# a fresh key is that a restart invalidates conversations that are already open, and the
# user starts a new one. That is the safe direction, and at this scale it is free.
#
# `FLEETGUARD_CHAT_SIGNING_KEY` overrides it, for the one case this does not cover — several
# replicas behind one ingress, where a per-process key would reject turns issued by a sibling.
# The App runs single-instance today, so nothing sets it.
_HMAC_KEY = os.getenv("FLEETGUARD_CHAT_SIGNING_KEY") or secrets.token_hex(32)


def sign_turn(text: str) -> str:
    """Tag proving this server produced `text` as an assistant turn."""
    return hmac.new(_HMAC_KEY.encode(), text.encode(), hashlib.sha256).hexdigest()


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    # Bounded for the same reason `messages` is capped at 20: unbounded content means up to 20
    # arbitrarily large strings forwarded to a billed serving endpoint on a 120s timeout. 8000
    # chars is generous for a real question — far above anything a human types — while ruling
    # out that shape of request.
    content: str = Field(max_length=8000)
    # Present on assistant turns only; the client echoes back what `ChatReply.signature`
    # gave it. A user turn carries none — the user is allowed to say anything.
    signature: str | None = Field(default=None, max_length=128)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=20)


def _verify_history(messages: list[ChatTurn]) -> None:
    """Reject any assistant turn this server cannot prove it produced."""
    for turn in messages:
        if turn.role != "assistant":
            continue
        # `compare_digest` rather than `==`: this compares a client-supplied value against
        # a secret-derived one, which is the textbook timing-oracle shape.
        if not turn.signature or not hmac.compare_digest(
            turn.signature, sign_turn(turn.content)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "An assistant turn in this conversation could not be verified. "
                    "Start a new conversation. (If the app restarted, this is expected.)"
                ),
            )


class ChatReply(BaseModel):
    reply: str
    endpoint: str
    # The tag the client must send back alongside this reply if it replays it as history.
    signature: str = ""
    # Present only when the agent requested a write and this console performed it. The UI
    # renders this separately from `reply`, because the committed row — not the model's
    # prose — is what actually happened. Two shapes because there are now two write
    # actions (open_defect_signal, watch_campaign); the frontend narrows on `.action`.
    action_result: agent_actions.ActionResult | agent_actions.WatchCampaignResult | None = None


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

    # Both supported modes carry a real Databricks token, so this should not fire today. It
    # stays because it is cheap and the failure it prevents is opaque: an empty bearer token
    # still reaches the real serving endpoint, comes back 401, and surfaces as a generic 502 —
    # exactly the confusing case the frontend's dedicated "assistant is offline" state exists
    # to avoid. It used to fire routinely, for the removed `app-login` provider, which issued
    # an identity but no Databricks credential (see ARCHITECTURE.md §8a).
    if not principal.token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This deployment has no Databricks credential to query the agent with.",
        )

    _verify_history(req.messages)

    url = f"{host}/serving-endpoints/{AGENT_ENDPOINT}/invocations"
    # `signature` is this console's own bookkeeping and means nothing to the agent, whose
    # `predict` filters to {role, content} anyway. Dropped here so the wire payload stays
    # exactly what it was before signing existed.
    body = {"input": [{"role": t.role, "content": t.content} for t in req.messages]}

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

    # ONE ACTION PER TURN. The decision is unchanged and was reviewed once already
    # (I-109 #4): a chat turn that performs a batch of writes is not something an operator
    # can review, and nothing in the prompt asks for more than one. What changed on
    # 2026-09-20 is the ENFORCEMENT, not the rule.
    #
    # This used to execute `actions[0]` and drop the rest silently. That made the invariant
    # depend on the agent continuing to emit exactly one — so a future tool or schema change
    # that emitted two would not fail, it would quietly perform the first and discard the
    # second, and nothing anywhere would say so. Refusing makes the invariant checked
    # instead of assumed.
    #
    # The agent now also refuses this shape on its own side. Both ends fail closed rather
    # than each trusting the other to have handled it.
    if len(actions) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The assistant requested {len(actions)} actions in one turn; only one is "
                "allowed. Nothing was recorded."
            ),
        )

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

    return ChatReply(
        reply=text,
        endpoint=AGENT_ENDPOINT,
        signature=sign_turn(text),
        action_result=action_result,
    )
