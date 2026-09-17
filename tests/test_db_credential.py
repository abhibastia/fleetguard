"""Unit tests for db.py's Lakebase credential path.

Covers the OBO (on-behalf-of) machinery that had gone untested despite being the
security-critical part of this module: the psycopg-implementation workaround, the
credential cache's staleness check, minting a credential as the *caller* rather than the
app, and the connection parameters `connect` builds. `WorkspaceClient` and `psycopg.connect`
are faked rather than hit live — these tests are about the Python around those calls, not
about Lakebase or the SDK themselves.
"""

from __future__ import annotations

import time

import pytest
from fleetguard_api import db
from fleetguard_api.auth.tokens import Principal


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Every test gets its own credential cache so minting in one test can't be read by
    another — the whole point of the digest-keyed cache is that a hit proves real work."""
    monkeypatch.setattr(db, "_cache", {})


# -- _select_psycopg_impl -----------------------------------------------------------------


def test_leaves_psycopg_impl_alone_if_caller_already_set_it(monkeypatch):
    monkeypatch.setenv("PSYCOPG_IMPL", "c")
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    db._select_psycopg_impl()
    assert __import__("os").environ["PSYCOPG_IMPL"] == "c"


def test_forces_python_impl_on_databricks_runtime(monkeypatch):
    monkeypatch.delenv("PSYCOPG_IMPL", raising=False)
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    db._select_psycopg_impl()
    assert __import__("os").environ["PSYCOPG_IMPL"] == "python"


def test_does_not_set_psycopg_impl_outside_databricks(monkeypatch):
    monkeypatch.delenv("PSYCOPG_IMPL", raising=False)
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    db._select_psycopg_impl()
    assert "PSYCOPG_IMPL" not in __import__("os").environ


# -- _CachedCredential.stale ---------------------------------------------------------------


def test_credential_is_stale_after_the_ttl():
    old = db._CachedCredential(
        token="t", host="h", user="u", minted_at=time.time() - db._CREDENTIAL_TTL_S - 1
    )
    assert old.stale


def test_credential_is_fresh_just_minted():
    fresh = db._CachedCredential(token="t", host="h", user="u", minted_at=time.time())
    assert not fresh.stale


# -- _workspace_client -----------------------------------------------------------------


def test_workspace_client_requires_databricks_host(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    with pytest.raises(RuntimeError, match="DATABRICKS_HOST"):
        db._workspace_client(Principal(token="tok"))


def test_workspace_client_is_built_with_the_callers_token_not_the_apps(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.databricks.com")
    captured = {}

    def fake_workspace_client(*, host, token, auth_type):
        captured.update(host=host, token=token, auth_type=auth_type)
        return "fake-client"

    monkeypatch.setattr(db, "WorkspaceClient", fake_workspace_client)
    result = db._workspace_client(Principal(token="caller-token"))

    assert result == "fake-client"
    assert captured == {
        "host": "https://example.databricks.com",
        "token": "caller-token",
        # Pinned, not inferred — Databricks Apps injects ambient OAuth creds that would
        # otherwise make the SDK refuse to guess between them and the caller's token.
        "auth_type": "pat",
    }


# -- _credential --------------------------------------------------------------------------


class _FakeEndpointStatus:
    def __init__(self, host: str) -> None:
        self.status = type("S", (), {"hosts": type("H", (), {"host": host})()})()


class _FakePostgresAPI:
    def __init__(self, token: str, host: str) -> None:
        self._token = token
        self._host = host
        self.generate_calls = []
        self.get_endpoint_calls = []

    def generate_database_credential(self, *, endpoint):
        self.generate_calls.append(endpoint)
        return type("Cred", (), {"token": self._token})()

    def get_endpoint(self, *, name):
        self.get_endpoint_calls.append(name)
        return _FakeEndpointStatus(self._host)


class _FakeWorkspaceClient:
    def __init__(self, token: str = "minted-pg-token", host: str = "pg.example.com") -> None:
        self.postgres = _FakePostgresAPI(token, host)
        self.current_user = type(
            "CU",
            (),
            {"me": staticmethod(lambda: type("U", (), {"user_name": "sdk@example.com"})())},
        )()


def test_credential_mints_via_the_sdk_and_caches_it(monkeypatch):
    fake_client = _FakeWorkspaceClient()
    monkeypatch.setattr(db, "_workspace_client", lambda principal: fake_client)

    principal = Principal(token="abc", user_name="alice@example.com")
    cred = db._credential(principal)

    assert cred.token == "minted-pg-token"
    assert cred.host == "pg.example.com"
    assert cred.user == "alice@example.com"
    assert fake_client.postgres.generate_calls == [db.ENDPOINT]
    # Second call with the same principal (same token) is a cache hit — no second mint.
    cred2 = db._credential(principal)
    assert cred2 is cred
    assert fake_client.postgres.generate_calls == [db.ENDPOINT]


def test_credential_falls_back_to_sdk_current_user_when_principal_has_no_username(monkeypatch):
    fake_client = _FakeWorkspaceClient()
    monkeypatch.setattr(db, "_workspace_client", lambda principal: fake_client)

    cred = db._credential(Principal(token="abc", user_name=None))
    assert cred.user == "sdk@example.com"


def test_credential_cache_is_keyed_on_the_token_not_just_the_username(monkeypatch):
    """A cache hit must prove possession of the same token that earned the entry."""
    calls = []

    def workspace_client(principal):
        calls.append(principal.token)
        return _FakeWorkspaceClient()

    monkeypatch.setattr(db, "_workspace_client", workspace_client)

    db._credential(Principal(token="token-one", user_name="alice@example.com"))
    db._credential(Principal(token="token-two", user_name="alice@example.com"))

    assert calls == ["token-one", "token-two"]


def test_stale_cached_credential_is_re_minted(monkeypatch):
    fake_client = _FakeWorkspaceClient()
    monkeypatch.setattr(db, "_workspace_client", lambda principal: fake_client)
    principal = Principal(token="abc", user_name="alice@example.com")

    first = db._credential(principal)
    # Force staleness without waiting out the real TTL.
    db._cache[next(iter(db._cache))] = db._CachedCredential(
        token=first.token, host=first.host, user=first.user, minted_at=0.0
    )

    second = db._credential(principal)
    assert fake_client.postgres.generate_calls == [db.ENDPOINT, db.ENDPOINT]
    assert second is not first


# -- connect ------------------------------------------------------------------------------


def test_connect_passes_the_minted_credential_to_psycopg(monkeypatch):
    fake_cred = db._CachedCredential(
        token="pg-token", host="pg.example.com", user="alice", minted_at=time.time()
    )
    monkeypatch.setattr(db, "_credential", lambda principal: fake_cred)

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "fake-connection"

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)

    result = db.connect(Principal(token="whatever"), autocommit=False)

    assert result == "fake-connection"
    assert captured["host"] == "pg.example.com"
    assert captured["user"] == "alice"
    assert captured["password"] == "pg-token"
    assert captured["dbname"] == db.PG_DB
    assert captured["sslmode"] == "verify-full"
    assert captured["autocommit"] is False
    assert captured["connect_timeout"] == 15
