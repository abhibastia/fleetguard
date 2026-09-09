"""The chat route must surface the serving endpoint's own error message, not just its status
code — found live 2026-09-04: a stopped endpoint and a malformed request are both bare 400s,
indistinguishable from "Agent endpoint returned 400" alone.
"""

from __future__ import annotations

import fleetguard_api.deps as deps_module
import fleetguard_api.routers.chat as chat_module
from fastapi.testclient import TestClient
from fleetguard_api.main import app

client = TestClient(app)


def _configure(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setenv("FLEETGUARD_DEV_TOKEN", "dapi-test-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    deps_module.get_token_provider.cache_clear()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _action_result():
    """A valid `ActionResult`. A bare dict here fails `ChatReply` validation *before* the
    assertion runs, which makes the test fail for a reason unrelated to what it checks —
    caught while confirming these tests fail against the unfixed code, which is exactly what
    that step is for."""
    return chat_module.agent_actions.ActionResult(
        action="open_defect_signal",
        signal_id="AGENT-test",
        component="ELECTRICAL SYSTEM",
        make="RAM",
        model="2500",
        fleet_vehicles=1256,
        match_basis="EXACT",
        opened_by="ops@example.com",
    )


def test_a_stopped_endpoint_reads_as_offline_503_not_a_raw_502(monkeypatch):
    """I-093. This test previously asserted **502**, which encoded the bug.

    Surfacing the endpoint's message (the 2026-09-04 fix) was necessary but not sufficient:
    `Assistant.tsx` renders its "the assistant is offline" message only on **503**, and its
    own comment says "503 means the serving endpoint is stopped" — so the friendly state was
    unreachable for precisely the case it was written for, and a judge hitting a scaled-down
    endpoint got a raw 502 instead. The message body is pinned verbatim as returned by the
    live endpoint on 2026-09-09, because matching on it is the only signal available: a
    stopped endpoint and a malformed request are both bare 400s.
    """
    _configure(monkeypatch)
    monkeypatch.setattr(
        chat_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            400,
            {
                "error_code": "BAD_REQUEST",
                "message": "The given endpoint is stopped, please retry after starting the endpoint.",
            },
        ),
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503, "the frontend's offline state keys on 503"
    assert "stopped" in resp.json()["detail"].lower()


def test_a_real_400_is_still_a_502_and_still_says_why(monkeypatch):
    """The offline branch must not swallow genuine request errors — a malformed request is a
    bug to see, not a service to wait for."""
    _configure(monkeypatch)
    monkeypatch.setattr(
        chat_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            400, {"error_code": "BAD_REQUEST", "message": "Invalid input schema for messages."}
        ),
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert "Invalid input schema" in resp.json()["detail"]


def test_400_with_unparseable_body_still_reports_the_status(monkeypatch):
    _configure(monkeypatch)

    class _UnparseableResponse:
        status_code = 400

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(chat_module.httpx, "post", lambda *a, **k: _UnparseableResponse())

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert "400" in resp.json()["detail"]


def test_the_agent_request_id_reaches_the_audit_row(monkeypatch):
    """E-03. `fleetguard_agent_action.trace_id` existed and `execute()` accepted it, but
    `chat.py` never passed one — so the column was NULL for every row ever written.

    The id is what makes the audit trail and the observability trail the *same* trail:
    `trace_id` joins to `fleetguard_agent_payload.databricks_request_id`, the inference table,
    which carries the model's own request/response, token usage and latency. Without it the
    two halves of the governance story cannot be connected to each other.
    """
    _configure(monkeypatch)
    captured: dict = {}

    def _fake_execute(principal, envelope, trace_id=None):
        captured["trace_id"] = trace_id
        return _action_result()

    monkeypatch.setattr(chat_module.agent_actions, "execute", _fake_execute)
    monkeypatch.setattr(
        chat_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            200,
            {
                "object": "response",
                "id": "5ec15af5-e058-40a9-956d-60e49f0d7a1c",
                "databricks_output": {
                    "databricks_request_id": "5ec15af5-e058-40a9-956d-60e49f0d7a1c"
                },
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "Requested."},
                            {
                                "type": "output_text",
                                "text": chat_module.agent_actions.ACTION_SENTINEL
                                + ' {"__fleetguard_action__": "open_defect_signal"}',
                            },
                        ]
                    }
                ],
            },
        ),
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "open one"}]})
    assert resp.status_code == 200
    assert captured["trace_id"] == "5ec15af5-e058-40a9-956d-60e49f0d7a1c"


def test_a_response_without_a_request_id_still_writes_the_action(monkeypatch):
    """Capturing the id must never become a precondition for the write. An action that
    executed but could not be traced is a worse outcome than an untraced action — the write
    is the thing with real-world consequences."""
    _configure(monkeypatch)
    captured: dict = {}

    def _fake_execute(principal, envelope, trace_id=None):
        captured["trace_id"] = trace_id
        return _action_result()

    monkeypatch.setattr(chat_module.agent_actions, "execute", _fake_execute)
    monkeypatch.setattr(
        chat_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            200,
            {
                "object": "response",
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "Requested."},
                            {
                                "type": "output_text",
                                "text": chat_module.agent_actions.ACTION_SENTINEL
                                + ' {"__fleetguard_action__": "open_defect_signal"}',
                            },
                        ]
                    }
                ],
            },
        ),
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "open one"}]})
    assert resp.status_code == 200
    assert captured["trace_id"] is None
