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
import logging
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

# Prefix `src/agent/14_fleetguard_agent.py`'s `predict()` puts on the id of every output item
# carrying an action envelope. Must stay in sync with it, same as `ACTION_SENTINEL`.
log = logging.getLogger(__name__)

ACTION_ITEM_ID_PREFIX = "action-"


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


def sign_turn(text: str, *, user: str | None = None, index: int = 0, prev: str = "") -> str:
    """Tag proving this server produced `text` as assistant turn `index` for `user`.

    THE TAG COVERS MORE THAN THE TEXT (I-115). Signing the content alone made every tag
    portable in two directions that matter:

      * **Across users.** A tag minted in one session verified in anyone else's, because
        nothing in it named a principal. User A could hand User B a genuine assistant turn
        and B's session would accept it as its own history.
      * **Across positions.** Turns could be reordered, replayed, or duplicated within one
        conversation and every tag still checked out, because nothing in it named a position.

    Neither grants an unauthorised write — `agent_actions.execute` gates on
    `authz.may_approve` regardless — but both steer the model's context for a caller who IS
    an approver, which is the same exposure I-110 closed the larger half of.

    So the tag binds four things: the principal, the turn's index in the conversation, the
    previous assistant turn's tag (a hash chain, which is what makes reordering detectable)
    and the content. Field-separated with a character that cannot occur in a hex digest or an
    email, so two different tuples cannot serialise to the same string.

    `user` is `None` under auth modes that resolve no identity. That degrades to the old,
    unbound behaviour for those modes rather than failing closed — they have no identity to
    bind to, and the property was never available there.
    """
    payload = "\x1f".join([user or "", str(index), prev, text])
    return hmac.new(_HMAC_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


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


def _verify_history(messages: list[ChatTurn], user: str | None) -> str:
    """Reject any assistant turn this server cannot prove it produced for THIS caller.

    Returns the chain head — the last verified assistant tag — which the reply is then
    signed against. An empty string means no assistant turn has been verified yet, which is
    the correct chain seed for the first reply in a conversation.
    """
    prev = ""
    for i, turn in enumerate(messages):
        if turn.role != "assistant":
            continue
        # `compare_digest` rather than `==`: this compares a client-supplied value against
        # a secret-derived one, which is the textbook timing-oracle shape.
        if not turn.signature or not hmac.compare_digest(
            turn.signature, sign_turn(turn.content, user=user, index=i, prev=prev)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "An assistant turn in this conversation could not be verified. "
                    "Start a new conversation. (If the app restarted, or you signed in as "
                    "a different user, this is expected.)"
                ),
            )
        prev = turn.signature
    return prev


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

    THE SENTINEL ALONE IS NOT PROOF OF ORIGIN (I-115). It is a literal string, and the agent's
    `predict()` turns every assistant message into an output item — so a model induced to begin
    its reply with it produced something this function used to parse and hand to
    `agent_actions.execute`. The corpus the agent retrieves from is public user-submitted
    complaint narrative, so that is a reachable injection surface, not a thought experiment.

    The discriminator is the item **id**, which the model cannot author: `predict()` stamps
    envelopes `action-<n>` and prose with a bare ordinal, both assigned in Python from the
    tool's own return value. The agent independently strips the sentinel out of model-authored
    prose, so this is the second of two barriers rather than the only one.

    **FAIL CLOSED, including on a missing id (changed 2026-09-24, I-117).** This previously
    read *"no id at all → previous behaviour"*, i.e. execute anyway — which states the exact
    opposite of the invariant above. The reasoning was that the item-level `id` had not been
    confirmed on a live payload and an mlflow version omitting it must not stop every write
    from working. That is a real risk and it is the wrong trade: it makes the discriminator
    optional, so anything that can produce an item without an id can produce an executable
    action, and the security story degrades to "it works because our current wrapper happens to
    stamp ids".

    So now: id starts with `action-` → envelope. **Anything else, including no id at all →
    dropped**, from both the actions and the reply — rendering it would show the operator a
    forged envelope as though the assistant had said it.

    The risk that motivated the old behaviour is handled by **making the failure loud instead
    of making it safe-looking**: a dropped envelope logs at WARNING with the observed item
    shape. If a wrapper really does omit ids, Run 2's verification sees an unmistakable log
    line rather than a write that silently does nothing, and the revert is one condition.
    """
    parts: list[str] = []
    actions: list[dict] = []
    for item in payload.get("output", []):
        item_id = item.get("id")
        for chunk in item.get("content", []) or []:
            if chunk.get("type") != "output_text":
                continue
            text = chunk.get("text", "")
            envelope = agent_actions.parse_envelope(text)
            if envelope is None:
                parts.append(text)
            elif item_id is not None and str(item_id).startswith(ACTION_ITEM_ID_PREFIX):
                actions.append(envelope)
            else:
                # A sentinel in an item Python did NOT label as an action. Either a forgery, or
                # a payload shape whose ids we cannot read. Dropped either way — but logged
                # loudly, because those two causes need very different responses and neither is
                # diagnosable from a write that quietly does nothing.
                log.warning(
                    "dropping an action envelope: item id %r does not start with %r "
                    "(action=%r). If this fires for every action, the payload shape changed "
                    "and the id discriminator needs re-checking — see _extract's docstring.",
                    item_id,
                    ACTION_ITEM_ID_PREFIX,
                    envelope.get("action"),
                )
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

    chain_head = _verify_history(req.messages, principal.user_name)

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
        # The reply will sit at `len(req.messages)` when the client replays it — the
        # same index `_verify_history` computes walking the list back.
        signature=sign_turn(
            text, user=principal.user_name, index=len(req.messages), prev=chain_head
        ),
        action_result=action_result,
    )
