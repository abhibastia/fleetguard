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


def test_stopped_endpoint_error_is_surfaced_not_swallowed(monkeypatch):
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
    assert resp.status_code == 502
    assert "stopped" in resp.json()["detail"]


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
