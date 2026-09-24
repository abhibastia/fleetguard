"""`/readyz` exists because `/healthz` answered `ok` while nothing worked.

Between the two online windows this project's AI Search index is deleted, its agent endpoint is
stopped and its App is stopped — and `/healthz` reported healthy throughout, because it only
ever checked the process. I-115 filed that; this file pins the replacement.

What is asserted here, in order of what would actually hurt:

1. **A failing dependency produces 503, not 200.** The entire point. A readiness endpoint that
   stays green while the demo is broken is worse than no readiness endpoint, because someone
   trusts it.
2. **One check failing does not hide the others.** The value of this route is naming *which*
   thing is down 20 minutes before a demo, so a single raising check must not abort the sweep.
3. **It costs nothing to call.** `serving_endpoints.query` would wake a scale-to-zero container
   and bill for it; the test below fails if anyone ever reaches for it.
4. **It is authenticated**, because the Lakebase check runs under the caller's token.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import fleetguard_api.deps as deps_module
import pytest
from fastapi.testclient import TestClient
from fleetguard_api.main import app
from fleetguard_api.routers import readyz as readyz_module

client = TestClient(app)


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setenv("FLEETGUARD_DEV_TOKEN", "dapi-test-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    deps_module.get_token_provider.cache_clear()


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return (1,)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cursor()


class _FakeWorkspace:
    """Stands in for the SDK client.

    `query` raises rather than returning a value: waking the agent endpoint is the one thing
    this route must never do, and a mock that quietly answered it would let that regress
    silently. Same reasoning as the `scale_to_zero_enabled` note in the router — the cheap
    wrong call and the correct call look identical at the call site.
    """

    def __init__(self, *, endpoint_ready="READY", index_ready=True, rows=115_499):
        self.serving_endpoints = SimpleNamespace(
            get=lambda name: SimpleNamespace(
                state=SimpleNamespace(ready=SimpleNamespace(value=endpoint_ready), config_update=None),
                config=SimpleNamespace(served_entities=[SimpleNamespace(entity_version="7")]),
            ),
            query=self._forbidden,
        )
        self.vector_search_indexes = SimpleNamespace(
            get_index=lambda index_name: SimpleNamespace(
                status=SimpleNamespace(ready=index_ready, indexed_row_count=rows)
            )
        )

    @staticmethod
    def _forbidden(*a, **k):
        raise AssertionError(
            "readyz queried the serving endpoint — that wakes a scale-to-zero container and "
            "bills for it. Check state with serving_endpoints.get instead."
        )


def _patch_all(monkeypatch: pytest.MonkeyPatch, *, workspace=None, connect=None) -> None:
    monkeypatch.setattr(readyz_module.db, "connect", connect or (lambda principal: _Conn()))
    monkeypatch.setattr(
        readyz_module.db, "_workspace_client", lambda principal: workspace or _FakeWorkspace()
    )


def _get(monkeypatch: pytest.MonkeyPatch, **kw):
    _patch_all(monkeypatch, **kw)
    return client.get("/api/readyz")


def test_everything_up_is_200_and_says_so(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _get(monkeypatch)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert {c["name"] for c in body["checks"]} == {
        "lakebase",
        "agent_endpoint",
        "search_index",
        "snapshots",
    }
    assert all(c["status"] == "ok" for c in body["checks"])
    # Detail is populated on SUCCESS too — "ok" alone is not useful before a demo.
    assert all(c["detail"] for c in body["checks"])
    index = next(c for c in body["checks"] if c["name"] == "search_index")
    assert "115499" in index["detail"].replace(",", "")


def test_a_deleted_index_is_a_503_not_a_green_tick(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact state this system sits in between online windows, and the defect I-115 filed:
    `/healthz` returned `ok` for weeks with no index at all."""

    class _NoIndex(_FakeWorkspace):
        def __init__(self):
            super().__init__()
            self.vector_search_indexes = SimpleNamespace(
                get_index=lambda index_name: (_ for _ in ()).throw(
                    RuntimeError("RESOURCE_DOES_NOT_EXIST: index not found")
                )
            )

    resp = _get(monkeypatch, workspace=_NoIndex())
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["search_index"]["status"] == "down"
    assert "RESOURCE_DOES_NOT_EXIST" in by_name["search_index"]["detail"]
    # ...and the other three still ran and still passed. This is the property that makes the
    # route worth having: it names the broken thing rather than failing as a unit.
    assert by_name["lakebase"]["status"] == "ok"
    assert by_name["agent_endpoint"]["status"] == "ok"
    assert by_name["snapshots"]["status"] == "ok"


def test_a_stopped_agent_endpoint_is_down(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _get(monkeypatch, workspace=_FakeWorkspace(endpoint_ready="NOT_READY"))
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["agent_endpoint"]["status"] == "down"
    assert by_name["search_index"]["status"] == "ok"


def test_lakebase_unreachable_is_down_and_named(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(principal):
        raise OSError("connection refused")

    resp = _get(monkeypatch, connect=_refuse)
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["lakebase"]["status"] == "down"
    assert "connection refused" in by_name["lakebase"]["detail"]
    assert by_name["agent_endpoint"]["status"] == "ok"


def test_a_missing_snapshot_is_down(configured, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A missing snapshot breaks only the two PUBLIC routes, so it is invisible to anyone
    signed in — which is precisely why a readiness check should see it."""
    monkeypatch.setitem(readyz_module.SNAPSHOTS, "corpus", tmp_path / "absent.json")
    resp = _get(monkeypatch)
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["snapshots"]["status"] == "down"
    assert "corpus" in by_name["snapshots"]["detail"]


def test_snapshot_detail_carries_generated_at(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """Freshness, not just presence — a snapshot from before the last re-export is a stale
    number on a public page, which is the failure I-115 spent a session sweeping up."""
    resp = _get(monkeypatch)
    detail = next(c for c in resp.json()["checks"] if c["name"] == "snapshots")["detail"]
    for name, path in readyz_module.SNAPSHOTS.items():
        assert name in detail
        assert json.loads(path.read_text())["generated_at"] in detail


def test_readyz_requires_a_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike `/healthz`. The Lakebase check runs under the caller's own token because this app
    holds no privileges of its own (§8a) — checking the *app's* access would test something the
    product does not do."""
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "databricks-apps")
    deps_module.get_token_provider.cache_clear()
    assert client.get("/api/readyz").status_code == 401


def test_healthz_is_untouched_and_still_always_ok(configured) -> None:
    """`/readyz` must not turn the container probe into something that can fail."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
