"""Small remaining gaps around snapshot loading and the `/healthz`/`/me` ops routes:
`snapshot.load()`'s missing-file guard, `snapshot.signals()`'s `fleet_only` filter (exercised
through the real `/api/signals` route in snapshot mode — `test_snapshot_mode.py` covers the
unfiltered case), `healthz`'s snapshot-captured-at branch when the file legitimately can't be
read, and `/me`'s success response.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from fleetguard_api import snapshot


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    """`load()` is `@lru_cache`d per process — a stale hit from an earlier test would make
    these tests pass or fail for the wrong reason."""
    snapshot.load.cache_clear()
    yield
    snapshot.load.cache_clear()


def test_load_raises_a_clear_error_when_the_snapshot_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshot, "SNAPSHOT_PATH", tmp_path / "does-not-exist.json")
    with pytest.raises(FileNotFoundError, match="scripts/export_demo_snapshot.py"):
        snapshot.load()


def test_signals_fleet_only_filters_out_zero_fleet_vehicle_rows():
    result = snapshot.signals(fleet_only=True, limit=50)
    assert all((r.get("fleet_vehicles") or 0) > 0 for r in result["signals"])


@pytest.fixture()
def snapshot_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_TOKEN", "not-a-real-token")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_USER", "ops@example.com")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DATA_MODE", "snapshot")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    return TestClient(main.app)


def test_signals_endpoint_honours_fleet_only_in_snapshot_mode(snapshot_client):
    resp = snapshot_client.get("/api/signals?fleet_only=true")
    assert resp.status_code == 200
    body = resp.json()
    assert all((r.get("fleet_vehicles") or 0) > 0 for r in body["signals"])


def test_healthz_reports_no_capture_date_when_the_snapshot_file_is_unreadable(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_TOKEN", "not-a-real-token")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DATA_MODE", "snapshot")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    monkeypatch.setattr(snapshot, "SNAPSHOT_PATH", tmp_path / "does-not-exist.json")

    client = TestClient(main.app)
    body = client.get("/healthz").json()

    assert body["data_mode"] == "snapshot"
    assert body["snapshot_captured_at"] is None


def test_me_returns_the_callers_identity_and_token_source(monkeypatch):
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_TOKEN", "not-a-real-token")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_USER", "ops@example.com")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    client = TestClient(main.app)

    resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.json() == {"user_name": "ops@example.com", "token_source": "static-dev"}
