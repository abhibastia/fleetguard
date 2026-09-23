"""The agent's tool-loop protocol, exercised against the committed source.

These are the tests for the property that makes the write path safe: **an action envelope
is a request until the turn concludes, and the turn must conclude close enough to the
request that the two cannot disagree.** I-109 fixed one end of that (loop exhaustion must
discard); this file covers the other (a write request ends the tool phase).

`routers/chat.py` executes `actions[0]` with real-world consequences, so every test here
asserts on what `_run()` *returns* as actions — not on what the model said about it.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_source import load_agent_module


class FakeMessage:
    """Stands in for an OpenAI `ChatCompletionMessage`."""

    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = False) -> dict:
        d: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"id": c.id, "function": {"name": c.function.name}} for c in self.tool_calls]
        return {k: v for k, v in d.items() if not (exclude_none and v is None)}


class FakeToolCall:
    def __init__(self, name: str, arguments: str = "{}", id: str = "call-1") -> None:
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeCompletions:
    """Replays a scripted list of assistant messages, recording whether each request was
    made with tools available — which is how the terminality guarantee is observed."""

    def __init__(self, script: list[FakeMessage]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def create(self, model: str, messages: list, tools: list | None = None) -> Any:
        self.calls.append({"tools_offered": tools is not None, "messages": list(messages)})
        if not self._script:
            raise AssertionError("model was called more times than the test scripted")
        msg = self._script.pop(0)
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


class FakeClient:
    def __init__(self, script: list[FakeMessage]) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(script)})()


@pytest.fixture
def agent():
    """A fresh agent module per test, with the evidence lookup pre-primed so no test
    touches the (stubbed, exploding) SQL warehouse."""
    m = load_agent_module()
    m._evidence_cache = {"detect_pct": 16.0, "control_pct": 11.1, "lead_days": 197.0}
    return m


def _run_with(agent, script: list[FakeMessage], tools: dict):
    a = agent.FleetGuardAgent()
    client = FakeClient(script)
    a._client = lambda: client
    agent.TOOLS.update(tools)
    return a._run([{"role": "user", "content": "q"}]), client


def _envelope(agent, action: str = "open_defect_signal") -> dict:
    """What a write tool returns: a dict carrying the action key.

    The action name is a literal, not a constant off the agent module — the tool-name
    strings live in `app/backend/fleetguard_api/agent_actions.py`, and the two sides are
    kept in sync by `ACTION_SENTINEL`/`ACTION_KEY`, which `test_naming.py` already pins.
    """
    return {agent.ACTION_KEY: action, "params": {"component": "BRAKES"}}


# --------------------------------------------------------------------- terminality


def test_write_request_ends_the_tool_phase(agent):
    """After a write request, the model gets exactly one more call and it is toolless."""
    (emitted, actions), client = _run_with(
        agent,
        [
            FakeMessage(tool_calls=[FakeToolCall("open_defect_signal")]),
            FakeMessage(content="I've requested a defect signal for BRAKES."),
        ],
        {"open_defect_signal": lambda **k: _envelope(agent)},
    )
    assert len(actions) == 1
    assert emitted[-1]["content"] == "I've requested a defect signal for BRAKES."
    assert [c["tools_offered"] for c in client.chat.completions.calls] == [True, False], (
        "the final call must omit tools, or the model can reach for another one after "
        "the write was already requested"
    )


def test_model_cannot_call_another_tool_after_a_write_request(agent):
    """The scripted second message asks for another tool. Because the final call is made
    without tools, that request can never be issued — the fake would raise if a third
    model call happened, and the tool itself must never run."""
    ran: list[str] = []

    (emitted, actions), client = _run_with(
        agent,
        [
            FakeMessage(tool_calls=[FakeToolCall("open_defect_signal")]),
            FakeMessage(tool_calls=[FakeToolCall("search_complaints")]),
        ],
        {
            "open_defect_signal": lambda **k: _envelope(agent),
            "search_complaints": lambda **k: ran.append("search") or [],
        },
    )
    assert ran == [], "no tool may run after the write request"
    assert len(actions) == 1
    assert len(client.chat.completions.calls) == 2


def test_later_tool_failure_cannot_reach_the_write(agent):
    """The review's first case: action requested, a subsequent tool blows up, NO WRITE.

    With terminality in place the subsequent tool is never reached at all, so the
    exception cannot occur — which is the strongest form of the guarantee.
    """

    def explode(**k):
        raise RuntimeError("warehouse down")

    (emitted, actions), _ = _run_with(
        agent,
        [
            FakeMessage(tool_calls=[FakeToolCall("open_defect_signal")]),
            FakeMessage(content="done"),
        ],
        {"open_defect_signal": lambda **k: _envelope(agent), "lookup_fleet_exposure": explode},
    )
    assert len(actions) == 1


def test_two_envelopes_in_one_batch_write_nothing(agent):
    """Fail closed, the same direction as the exhaustion path."""
    calls = [FakeToolCall("open_defect_signal", id="a"), FakeToolCall("watch_campaign", id="b")]
    (emitted, actions), _ = _run_with(
        agent,
        [FakeMessage(tool_calls=calls)],
        {
            "open_defect_signal": lambda **k: _envelope(agent),
            "watch_campaign": lambda **k: _envelope(agent, "watch_campaign"),
        },
    )
    assert actions == []
    assert "one action at a time" in emitted[-1]["content"]


def test_every_tool_call_in_the_batch_is_answered(agent):
    """Cutting the batch short would leave a `tool_call_id` unanswered, which the
    completions API rejects on the next message. The write request is the *first* call
    here, so a naive `break` would strand the second."""
    calls = [FakeToolCall("open_defect_signal", id="a"), FakeToolCall("lookup_fleet_models", id="b")]
    (_, actions), client = _run_with(
        agent,
        [FakeMessage(tool_calls=calls), FakeMessage(content="done")],
        {
            "open_defect_signal": lambda **k: _envelope(agent),
            "lookup_fleet_models": lambda **k: {"makes": ["FORD"]},
        },
    )
    answered = {
        m["tool_call_id"]
        for m in client.chat.completions.calls[-1]["messages"]
        if isinstance(m, dict) and m.get("role") == "tool"
    }
    assert answered == {"a", "b"}
    assert len(actions) == 1


def test_no_write_request_still_runs_the_normal_loop(agent):
    """Read-only turns are unaffected: tools stay available for every round."""
    (emitted, actions), client = _run_with(
        agent,
        [
            FakeMessage(tool_calls=[FakeToolCall("lookup_fleet_models")]),
            FakeMessage(content="You operate FORD and RAM."),
        ],
        {"lookup_fleet_models": lambda **k: {"makes": ["FORD", "RAM"]}},
    )
    assert actions == []
    assert [c["tools_offered"] for c in client.chat.completions.calls] == [True, True]


def test_loop_exhaustion_still_discards_actions(agent):
    """I-109's guard, pinned so terminality cannot regress it."""
    (emitted, actions), _ = _run_with(
        agent,
        [FakeMessage(tool_calls=[FakeToolCall("lookup_fleet_models")]) for _ in range(6)],
        {"lookup_fleet_models": lambda **k: {"makes": []}},
    )
    assert actions == []
    assert "6 tool rounds" in emitted[-1]["content"]
