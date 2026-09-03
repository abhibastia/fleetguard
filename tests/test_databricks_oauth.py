"""Unit tests for the Databricks U2M OAuth mechanics (`auth/databricks_oauth.py`).

Same rationale as `test_auth_seam.py`: no network, no Databricks — these are pure functions
(PKCE generation, URL building) plus the refresh/expiry logic in `resolve()`, which is
exercised here by monkeypatching the module's own `_post_token`/`refresh` rather than the
transport layer, so the tests stay fast and don't depend on `httpx`'s internals.
"""

from __future__ import annotations

import time

import pytest
from fleetguard_api.auth import databricks_oauth as dbx

HOST = "https://example.cloud.databricks.com"
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-123")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret-456")


class TestEnabled:
    def test_enabled_when_all_three_vars_set(self):
        assert dbx.enabled() is True

    @pytest.mark.parametrize(
        "missing", ["DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"]
    )
    def test_disabled_when_any_var_missing(self, monkeypatch, missing):
        monkeypatch.delenv(missing, raising=False)
        assert dbx.enabled() is False


class TestPKCE:
    def test_verifier_and_challenge_differ(self):
        verifier, challenge = dbx.new_pkce_pair()
        assert verifier != challenge
        assert verifier and challenge

    def test_successive_pairs_are_not_reused(self):
        """A replayed verifier would let a stolen `code` be exchanged twice."""
        pairs = {dbx.new_pkce_pair() for _ in range(20)}
        assert len(pairs) == 20

    def test_challenge_is_url_safe(self):
        _, challenge = dbx.new_pkce_pair()
        assert "+" not in challenge and "/" not in challenge and "=" not in challenge


class TestAuthorizeUrl:
    def test_carries_pkce_mandatory_params(self):
        url = dbx.authorize_url(
            redirect_uri="https://app.example/cb", state="st-1", code_challenge="chal"
        )
        assert url.startswith(f"{HOST}{dbx.AUTHORIZE_PATH}?")
        assert "code_challenge=chal" in url
        assert "code_challenge_method=S256" in url
        assert "response_type=code" in url
        assert "state=st-1" in url
        assert "client_id=client-123" in url

    def test_requests_offline_access_for_refresh(self):
        """Without offline_access there is no refresh_token, and the session dies with the
        1-hour access token regardless of the cookie's own TTL."""
        url = dbx.authorize_url(
            redirect_uri="https://app.example/cb", state="s", code_challenge="c"
        )
        assert "offline_access" in url


class TestTokenExchange:
    def test_exchange_code_returns_token_set(self, monkeypatch):
        monkeypatch.setattr(
            dbx,
            "_post_token",
            lambda data: dbx.TokenSet(
                access_token="tok", refresh_token="ref", expires_at=NOW + 3600
            ),
        )
        result = dbx.exchange_code(
            code="c", code_verifier="v", redirect_uri="https://app.example/cb"
        )
        assert result.access_token == "tok"
        assert result.refresh_token == "ref"

    def test_post_token_raises_on_non_200(self, monkeypatch):
        class FakeResp:
            status_code = 400

            def json(self):
                return {}

        monkeypatch.setattr(dbx.httpx, "post", lambda *a, **k: FakeResp())
        with pytest.raises(dbx.OAuthError, match="400"):
            dbx.exchange_code(code="c", code_verifier="v", redirect_uri="https://app.example/cb")

    def test_post_token_raises_when_access_token_missing(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def json(self):
                return {"token_type": "Bearer"}  # no access_token

        monkeypatch.setattr(dbx.httpx, "post", lambda *a, **k: FakeResp())
        with pytest.raises(dbx.OAuthError, match="no access_token"):
            dbx.exchange_code(code="c", code_verifier="v", redirect_uri="https://app.example/cb")

    def test_post_token_wraps_network_errors(self, monkeypatch):
        import httpx as real_httpx

        def boom(*a, **k):
            raise real_httpx.ConnectError("nope")

        monkeypatch.setattr(dbx.httpx, "post", boom)
        with pytest.raises(dbx.OAuthError, match="unreachable"):
            dbx.exchange_code(code="c", code_verifier="v", redirect_uri="https://app.example/cb")

    def test_error_response_body_never_reaches_the_exception_text(self, monkeypatch):
        """A leaked code or verifier in an error description would be a credential-adjacent
        disclosure, same rule as tokens.py's `Principal.__repr__` never printing a token."""

        class FakeResp:
            status_code = 400

            def json(self):
                return {"error_description": "code=super-secret-code was already used"}

        monkeypatch.setattr(dbx.httpx, "post", lambda *a, **k: FakeResp())
        with pytest.raises(dbx.OAuthError) as exc:
            dbx.exchange_code(code="c", code_verifier="v", redirect_uri="https://app.example/cb")
        assert "super-secret-code" not in str(exc.value)


class TestResolve:
    """`session_lookup` for FLEETGUARD_AUTH_MODE=render-u2m — refreshes in place, or fails
    closed to 'no session' rather than handing back a token about to be rejected."""

    def test_unknown_session_returns_none(self):
        assert dbx.resolve("missing", {}) is None

    def test_fresh_session_is_returned_unmodified(self):
        sessions = {"s": {"access_token": "tok", "expires_at": NOW + 3600, "created": NOW}}
        result = dbx.resolve("s", sessions)
        assert result["access_token"] == "tok"

    def test_session_with_no_expiry_is_returned_as_is(self):
        """Not every session shape carries expires_at (e.g. a hand-built test fixture); no
        expiry info means no refresh decision can be made, not an error."""
        sessions = {"s": {"access_token": "tok", "created": NOW}}
        assert dbx.resolve("s", sessions)["access_token"] == "tok"

    def test_near_expiry_session_refreshes(self, monkeypatch):
        monkeypatch.setattr(
            dbx,
            "refresh",
            lambda rt: dbx.TokenSet(
                access_token="new-tok", refresh_token="new-ref", expires_at=NOW + 3600
            ),
        )
        sessions = {
            "s": {
                "access_token": "old-tok",
                "refresh_token": "old-ref",
                "expires_at": NOW + 10,  # inside REFRESH_SKEW_S
                "created": NOW,
            }
        }
        monkeypatch.setattr(time, "time", lambda: NOW)
        result = dbx.resolve("s", sessions)
        assert result["access_token"] == "new-tok"
        assert sessions["s"]["access_token"] == "new-tok"  # mutated in place

    def test_refresh_failure_returns_none(self, monkeypatch):
        def boom(rt):
            raise dbx.OAuthError("refresh rejected")

        monkeypatch.setattr(dbx, "refresh", boom)
        sessions = {
            "s": {
                "access_token": "old-tok",
                "refresh_token": "old-ref",
                "expires_at": NOW + 10,
                "created": NOW,
            }
        }
        monkeypatch.setattr(time, "time", lambda: NOW)
        assert dbx.resolve("s", sessions) is None

    def test_near_expiry_with_no_refresh_token_returns_none(self, monkeypatch):
        """offline_access might not have been granted; without a refresh_token there is
        nothing to do but let the session die rather than serve a token about to expire."""
        sessions = {"s": {"access_token": "old-tok", "expires_at": NOW + 10, "created": NOW}}
        monkeypatch.setattr(time, "time", lambda: NOW)
        assert dbx.resolve("s", sessions) is None
