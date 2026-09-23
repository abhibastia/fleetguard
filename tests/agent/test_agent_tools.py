"""Tool-boundary behaviour: truncation, result caps, and error-vs-empty.

Each of these is a case where the model is handed something it cannot verify — a JSON
document, a row count, an empty list — and the failure mode is that a malformed or
ambiguous value reads as a confident fact. Same family as I-050.
"""

from __future__ import annotations

import json
import time

import pytest
from agent_source import load_agent_module


def _prime_evidence(m, value):
    """Seed the evidence cache AND its expiry.

    Both are needed since I-115 made the cache TTL'd: setting `_evidence_cache` alone leaves
    `_evidence_expires_at` at 0, so the very next read treats the entry as stale and goes
    back to the warehouse. That is the correct production behaviour and the reason this
    helper exists rather than each test poking one global.
    """
    m._evidence_cache = value
    m._evidence_expires_at = time.monotonic() + 3600


@pytest.fixture
def agent():
    m = load_agent_module()
    _prime_evidence(m, {"detect_pct": 16.0, "control_pct": 11.1, "lead_days": 197.0})
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


@pytest.mark.parametrize(
    "asked,fetched",
    [(1, 3), (5, 15), (10, 30), (50, 30), (10_000, 30), (0, 3), (-3, 3)],
)
def test_limit_is_clamped_then_over_fetched(agent, asked, fetched):
    """`limit` is still clamped to 1-10; the index is asked for 3x that, capped at 30.

    The clamp and the over-fetch are different properties and both matter: the clamp bounds
    what model output can demand, the multiplier compensates for the sibling-chunk dedupe
    below (I-023, I-115). Before I-115 these were the same number, which is exactly why a
    request for 10 could return 3.
    """
    idx = _wire(agent, type("Res", (), {"data_array": []})())
    agent.search_complaints("brakes", limit=asked)
    assert idx.num_results == fetched


def test_sibling_chunks_no_longer_shrink_the_result_set(agent):
    """The regression I-115 found: ask for 3, get 1.

    One complaint dominating the ranking with several of its own chunks is the NORMAL shape
    of this corpus, not an edge case — multi-component complaints yield sibling chunks by
    construction (I-023), and the more relevant a complaint is the more of its chunks rank
    highly. So the shrinkage was worst exactly when retrieval was working best.
    """
    rows = [
        ["c1", "CMP-1", "FORD", "F-250", "BRAKES", "Y", "chunk a"],
        ["c2", "CMP-1", "FORD", "F-250", "STEERING", "Y", "chunk b"],
        ["c3", "CMP-1", "FORD", "F-250", "ENGINE", "N", "chunk c"],
        ["c4", "CMP-2", "FORD", "F-250", "BRAKES", "N", "chunk d"],
        ["c5", "CMP-3", "FORD", "F-250", "BRAKES", "N", "chunk e"],
    ]
    _wire(agent, type("Res", (), {"data_array": rows})())
    out = agent.search_complaints("brakes", limit=3)
    assert [d["complaint_id"] for d in out] == ["CMP-1", "CMP-2", "CMP-3"]


def test_dedupe_never_returns_more_than_asked(agent):
    """Over-fetching must not leak the extra rows out to the model."""
    rows = [[f"c{i}", f"CMP-{i}", "FORD", "F-250", "BRAKES", "N", "x"] for i in range(20)]
    _wire(agent, type("Res", (), {"data_array": rows})())
    assert len(agent.search_complaints("brakes", limit=2)) == 2


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
    _prime_evidence(agent, None)
    prompt = agent._system_prompt()
    assert "16.0%" not in prompt and "197 days" not in prompt
    assert "evidence page" in prompt


def test_evidence_failure_is_cached_briefly_not_forever(agent):
    """**Deliberate behaviour change (I-115); this test asserted the opposite and passed.**

    The old rule was "a sleeping warehouse costs one timeout per process, not one per turn",
    and the first half of that is still right — a retry on every turn would make each one pay
    the timeout. What was wrong was the *permanence*: `_evidence_cache = None` was never
    re-examined, so a 30-second blip at the first query of a judging window meant the agent
    quoted no measured figures until the container restarted. A transient infrastructure
    fault became a session-long degradation of this project's own headline result.

    Now: still cached (a burst of turns costs one timeout), but the entry expires, so the
    next turn after the TTL retries and recovers. Same handling as I-110's work-order
    transition change — the old test is rewritten to assert the new rule, not deleted.
    """
    calls = []

    def boom(stmt, params):
        calls.append(stmt)
        raise RuntimeError("warehouse asleep")

    agent._run_sql = boom
    agent._evidence_cache = agent._EVIDENCE_UNSET
    agent._evidence_expires_at = 0.0

    assert agent._evidence() is None
    assert agent._evidence() is None
    assert len(calls) == 1, "within the TTL a failure must not be re-queried every turn"

    # Expire it the way the clock would.
    agent._evidence_expires_at = 0.0
    assert agent._evidence() is None
    assert len(calls) == 2, "after the TTL the warehouse must be retried, not written off"


def test_evidence_failure_ttl_is_much_shorter_than_success(agent):
    """The asymmetry is the point: recover fast from a blip, don't re-query a stable table."""
    assert agent._EVIDENCE_TTL_FAIL_S < agent._EVIDENCE_TTL_OK_S
    assert agent._EVIDENCE_TTL_FAIL_S <= 60


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
