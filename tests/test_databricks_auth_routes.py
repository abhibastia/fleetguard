"""The Databricks login routes must stay inert until FLEETGUARD_AUTH_MODE=render-u2m is the
deployment's actual selected mode — credentials being configured is not the same thing, and
mixing the two up would open a side door into app-login's signed-in state ahead of the mode
switch (found while preparing to set real credentials, 2026-09-03).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from fleetguard_api.main import app

client = TestClient(app, follow_redirects=False)


def _set_credentials(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-123")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("FLEETGUARD_PUBLIC_URL", "https://fleetguard-console-abhi.onrender.com")


def _issued_state(monkeypatch) -> str:
    """Drive a real /login call so the returned `state` is one the callback will accept —
    the CSRF state store is in-process, not something a test can fabricate directly."""
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "render-u2m")
    _set_credentials(monkeypatch)
    resp = client.get("/api/auth/databricks/login")
    location = resp.headers["location"]
    return parse_qs(urlparse(location).query)["state"][0]


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


def test_callback_rejects_unknown_or_reused_state(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "render-u2m")
    _set_credentials(monkeypatch)

    resp = client.get(
        "/api/auth/databricks/callback", params={"code": "c", "state": "never-issued"}
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_callback_surfaces_the_oauth_error_instead_of_a_generic_message(monkeypatch):
    """A denied/failed authorization redirects back with `error` (+ `error_description`)
    instead of `code` — the standard OAuth2 shape. Found live 2026-09-03: without this,
    'No authorization code returned' gave no way to tell a scope/consent problem from a
    misconfigured redirect_uri from anything else Databricks might reject."""
    state = _issued_state(monkeypatch)

    resp = client.get(
        "/api/auth/databricks/callback",
        params={"state": state, "error": "access_denied", "error_description": "user cancelled"},
    )
    assert resp.status_code == 401
    assert "access_denied" in resp.json()["detail"]
    assert "user cancelled" in resp.json()["detail"]


def test_callback_with_neither_code_nor_error_is_still_reported(monkeypatch):
    state = _issued_state(monkeypatch)

    resp = client.get("/api/auth/databricks/callback", params={"state": state})
    assert resp.status_code == 400
    assert "No authorization code returned" in resp.json()["detail"]


def test_callback_state_is_single_use(monkeypatch):
    """Replaying a state (e.g. a duplicated redirect) must not be accepted twice — the
    verifier it carries is single-use PKCE material."""
    state = _issued_state(monkeypatch)

    first = client.get("/api/auth/databricks/callback", params={"state": state})
    assert first.status_code == 400  # no code — expected, but the state IS consumed here

    second = client.get("/api/auth/databricks/callback", params={"state": state, "code": "c"})
    assert second.status_code == 400
    assert "Invalid or expired" in second.json()["detail"]
