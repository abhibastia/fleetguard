"""Tool-boundary behaviour: truncation, result caps, and error-vs-empty.

Each of these is a case where the model is handed something it cannot verify — a JSON
document, a row count, an empty list — and the failure mode is that a malformed or
ambiguous value reads as a confident fact. Same family as I-050.
"""

from __future__ import annotations

import json

import pytest
from agent_source import load_agent_module


@pytest.fixture
def agent():
    m = load_agent_module()
    m._evidence_cache = {"detect_pct": 16.0, "control_pct": 11.1, "lead_days": 197.0}
    return m


# ------------------------------------------------------------------ truncation


def test_short_results_are_untouched(agent):
    result = {"a": 1, "rows": [{"x": 1}]}
    assert json.loads(agent._encode_tool_result(result)) == result


def test_oversized_results_stay_valid_json(agent):
    """The whole point: the old `json.dumps(...)[:6000]` produced a document with its
    closing braces cut off, and the model had to guess at the shape."""
    result = {"signals": [{"id": i, "text": "x" * 200} for i in range(200)]}
    out = agent._encode_tool_result(result)
    assert len(out) <= agent.MAX_TOOL_CHARS
    parsed = json.loads(out)  # would raise if truncated mid-token
    assert parsed["truncated"] is True
    assert parsed["total_available"]["signals"] == 200
    assert 0 < len(parsed["signals"]) < 200


def test_truncation_reports_the_real_total_not_the_trimmed_one(agent):
    """`total_available` must describe the ORIGINAL result. Reporting the trimmed length
    would tell the model "there were 10" when there were 84 — the precise confusion this
    replaced."""
    result = {"hits": [{"text": "y" * 500} for _ in range(60)]}
    parsed = json.loads(agent._encode_tool_result(result))
    assert parsed["total_available"]["hits"] == 60


def test_non_dict_results_are_still_bounded(agent):
    """A tool returning a bare list (`search_complaints` does) has no field to trim."""
    out = agent._encode_tool_result([{"text": "z" * 400} for _ in range(100)])
    assert len(out) <= agent.MAX_TOOL_CHARS


# ---------------------------------------------------------- search_complaints


class _FakeIndexes:
    def __init__(self, result, manifest="default"):
        self._result, self._manifest = result, manifest
        self.num_results: int | None = None

    def query_index(self, index_name, columns, query_text, query_type, num_results):
        self.num_results = num_results
        cols = [type("C", (), {"name": c})() for c in columns]
        manifest = type("M", (), {"columns": cols})() if self._manifest else None
        return type("R", (), {"result": self._result, "manifest": manifest})()


def _wire(agent, result, manifest="default"):
    idx = _FakeIndexes(result, manifest)
    agent.w = type("W", (), {"vector_search_indexes": idx})()
    return idx


@pytest.mark.parametrize("asked,expected", [(1, 1), (5, 5), (10, 10), (50, 10), (10_000, 10), (0, 1), (-3, 1)])
def test_limit_is_clamped(agent, asked, expected):
    idx = _wire(agent, type("Res", (), {"data_array": []})())
    agent.search_complaints("brakes", limit=asked)
    assert idx.num_results == expected


def test_zero_hits_returns_empty(agent):
    """A valid query that matched nothing. This must NOT raise — 'no complaints found' is
    a real, reportable answer."""
    _wire(agent, type("Res", (), {"data_array": []})())
    assert agent.search_complaints("nothing matches this") == []


def test_absent_result_object_raises_rather_than_reading_as_no_hits(agent):
    """A deleted or not-ready index. Collapsing this to `[]` is how a broken retrieval
    system gets reported to an operator as an all-clear."""
    _wire(agent, None)
    with pytest.raises(RuntimeError, match="RETRIEVAL_ERROR"):
        agent.search_complaints("brakes")


def test_absent_manifest_raises(agent):
    _wire(agent, type("Res", (), {"data_array": [["c1", "42"]]})(), manifest=None)
    with pytest.raises(RuntimeError, match="RETRIEVAL_ERROR"):
        agent.search_complaints("brakes")


# -------------------------------------------------------------------- evidence


def test_prompt_carries_the_measured_numbers(agent):
    prompt = agent._system_prompt()
    assert "16.0%" in prompt and "11.1%" in prompt and "197 days" in prompt
    assert "{evidence_sentence}" not in prompt


def test_prompt_quotes_no_numbers_when_the_lookup_fails(agent):
    """Falling back to a stale hard-coded figure is the failure being prevented; falling
    back to a qualitative claim is honest."""
    agent._evidence_cache = None
    prompt = agent._system_prompt()
    assert "16.0%" not in prompt and "197 days" not in prompt
    assert "evidence page" in prompt


def test_evidence_failure_is_cached_not_retried(agent):
    """A sleeping warehouse must cost one timeout per process, not one per turn."""
    calls = []

    def boom(stmt, params):
        calls.append(stmt)
        raise RuntimeError("warehouse asleep")

    agent._run_sql = boom
    agent._evidence_cache = agent._EVIDENCE_UNSET
    assert agent._evidence() is None
    assert agent._evidence() is None
    assert len(calls) == 1


# -------------------------------------------------------------- timeout budget


def test_turn_budget_is_below_the_client_timeout(agent):
    """`routers/chat.py` gives up at 120 s. If the agent could outlive that, a browser
    could time out while a write was still in flight — which is how a retry creates a
    duplicate signal."""
    assert agent.TURN_BUDGET_S < 120.0
    assert agent.QUERY_BUDGET_S < agent.TURN_BUDGET_S


def test_no_deadline_outside_a_turn(agent):
    """Direct tool calls and the notebook smoke tests must behave as they always did."""
    assert agent._remaining() == float("inf")


def test_query_refuses_to_start_with_no_time_left(agent):
    """Below the API's 5 s `wait_timeout` floor there is nothing useful to start."""
    import time as _t

    token = agent._turn_deadline.set(_t.monotonic() + 1.0)
    try:
        with pytest.raises(TimeoutError, match="not enough time"):
            agent._run_sql("SELECT 1", [])
    finally:
        agent._turn_deadline.reset(token)
