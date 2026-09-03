"""The Databricks login routes must stay inert until FLEETGUARD_AUTH_MODE=render-u2m is the
deployment's actual selected mode — credentials being configured is not the same thing, and
mixing the two up would open a side door into app-login's signed-in state ahead of the mode
switch (found while preparing to set real credentials, 2026-09-03).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from fleetguard_api.main import app

client = TestClient(app, follow_redirects=False)


def _set_credentials(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-123")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("FLEETGUARD_PUBLIC_URL", "https://fleetguard-console-abhi.onrender.com")


def test_login_503s_when_credentials_exist_but_mode_is_app_login(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "app-login")
    monkeypatch.setenv("FLEETGUARD_DATA_MODE", "snapshot")
    _set_credentials(monkeypatch)

    resp = client.get("/api/auth/databricks/login")
    assert resp.status_code == 503
    assert "render-u2m" in resp.json()["detail"]


def test_callback_503s_when_credentials_exist_but_mode_is_app_login(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "app-login")
    monkeypatch.setenv("FLEETGUARD_DATA_MODE", "snapshot")
    _set_credentials(monkeypatch)

    resp = client.get("/api/auth/databricks/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 503
    assert "render-u2m" in resp.json()["detail"]


def test_login_503s_in_render_u2m_mode_without_credentials(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "render-u2m")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)

    resp = client.get("/api/auth/databricks/login")
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


def test_login_redirects_to_databricks_when_mode_and_credentials_both_present(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "render-u2m")
    _set_credentials(monkeypatch)

    resp = client.get("/api/auth/databricks/login")
    assert resp.status_code == 307
    assert resp.headers["location"].startswith(
        "https://example.cloud.databricks.com/oidc/v1/authorize?"
    )
