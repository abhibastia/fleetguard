"""The `/chat` route's remaining error branches — `test_chat.py` covers the 400-vs-stopped
disambiguation (I-093) and the trace-id join (E-03); this covers what was still untested:
no agent configured, the endpoint unreachable at the network level, a deleted/never-existed
endpoint (404), no query permission (403), and a 200 response that yields no visible text.
"""

from __future__ import annotations

import fleetguard_api.deps as deps_module
import fleetguard_api.routers.chat as chat_module
import httpx
from fastapi.testclient import TestClient
from fleetguard_api.main import app

client = TestClient(app)


def _configure(monkeypatch, *, host: str | None = "https://example.cloud.databricks.com"):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setenv("FLEETGUARD_DEV_TOKEN", "dapi-test-token")
    if host is None:
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    else:
        monkeypatch.setenv("DATABRICKS_HOST", host)
    deps_module.get_token_provider.cache_clear()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_no_databricks_host_is_a_503_not_configured(monkeypatch):
    _configure(monkeypatch, host=None)
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


def test_network_failure_reaching_the_endpoint_is_a_503(monkeypatch):
    _configure(monkeypatch)

    def _raise(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(chat_module.httpx, "post", _raise)

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert "unreachable" in resp.json()["detail"].lower()


def test_a_stopped_or_deleted_endpoint_404_reads_as_offline(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        chat_module.httpx, "post", lambda *a, **k: _FakeResponse(404, {"message": "not found"})
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert "not serving" in resp.json()["detail"].lower()


def test_no_query_permission_on_the_endpoint_is_a_403(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        chat_module.httpx, "post", lambda *a, **k: _FakeResponse(403, {"message": "forbidden"})
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 403
    assert "query permission" in resp.json()["detail"].lower()


def test_a_response_with_no_extractable_text_is_a_502(monkeypatch):
    """The endpoint answered 200 but every output item was an action envelope or otherwise
    produced no visible prose — the operator must see an error, not a blank reply."""
    _configure(monkeypatch)
    monkeypatch.setattr(
        chat_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(200, {"object": "response", "output": []}),
    )

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    assert "no answer" in resp.json()["detail"].lower()
