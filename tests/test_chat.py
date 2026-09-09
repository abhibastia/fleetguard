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
