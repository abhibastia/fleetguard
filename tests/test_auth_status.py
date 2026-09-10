"""`/auth/status` answers the console's mount-time question without requiring an identity.

It replaces the session-backed version removed with Render on 2026-09-10. That one read a
cookie store directly, which is how it drifted out of step with the token providers when
server-side session expiry was added (I-071): the console drew a signed-in header, and even
a "you may approve" state, while every data request 401'd underneath it. Deriving from the
same provider every real request uses is what makes that drift structurally impossible — so
these tests pin the derivation, not just the field values.

Two directions to hold:
  - it must never 401. The Evidence page needs no identity, so the console has to be able to
    ask this before it has one.
  - `may_approve` must track `FLEETGUARD_APPROVERS`, because the console enables the Approve
    button from it. Reporting true where the write path would 403 is the I-071 failure again.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

TOKEN = "dapi-not-a-real-token-0123456789"
APPROVER = "ops@example.com"


def _client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    for key, value in env.items():
        monkeypatch.setitem(os.environ, key, value)
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    return TestClient(main.app)


@pytest.fixture()
def signed_in(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _client(
        monkeypatch,
        FLEETGUARD_AUTH_MODE="static-dev",
        FLEETGUARD_DEV_TOKEN=TOKEN,
        FLEETGUARD_DEV_USER=APPROVER,
        FLEETGUARD_APPROVERS=APPROVER,
    )


class TestSignedIn:
    def test_reports_the_resolved_identity(self, signed_in: TestClient):
        body = signed_in.get("/api/auth/status").json()
        assert body["signed_in"] is True
        assert body["user_name"] == APPROVER

    def test_approver_may_approve(self, signed_in: TestClient):
        assert signed_in.get("/api/auth/status").json()["may_approve"] is True

    def test_non_approver_may_not(self, monkeypatch: pytest.MonkeyPatch):
        client = _client(
            monkeypatch,
            FLEETGUARD_AUTH_MODE="static-dev",
            FLEETGUARD_DEV_TOKEN=TOKEN,
            FLEETGUARD_DEV_USER="reader@example.com",
            FLEETGUARD_APPROVERS=APPROVER,
        )
        body = client.get("/api/auth/status").json()
        assert body["signed_in"] is True
        assert body["may_approve"] is False

    def test_unset_approvers_means_nobody(self, monkeypatch: pytest.MonkeyPatch):
        """Matches the write path: FLEETGUARD_APPROVERS unset blocks everyone, so the console
        must not offer an Approve button that is guaranteed to 403."""
        monkeypatch.delenv("FLEETGUARD_APPROVERS", raising=False)
        client = _client(
            monkeypatch,
            FLEETGUARD_AUTH_MODE="static-dev",
            FLEETGUARD_DEV_TOKEN=TOKEN,
            FLEETGUARD_DEV_USER=APPROVER,
        )
        assert client.get("/api/auth/status").json()["may_approve"] is False


class TestNoIdentity:
    """`databricks-apps` mode with no forwarded header — an unauthenticated caller."""

    @pytest.fixture()
    def anonymous(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        return _client(
            monkeypatch,
            FLEETGUARD_AUTH_MODE="databricks-apps",
            FLEETGUARD_APPROVERS=APPROVER,
        )

    def test_answers_200_never_401(self, anonymous: TestClient):
        resp = anonymous.get("/api/auth/status")
        assert resp.status_code == 200, resp.text

    def test_reports_signed_out(self, anonymous: TestClient):
        body = anonymous.get("/api/auth/status").json()
        assert body == {"signed_in": False, "user_name": None, "may_approve": False}

    def test_the_authenticated_routes_still_401(self, anonymous: TestClient):
        """The point of the exemption being narrow: /auth/status is public, /me is not."""
        assert anonymous.get("/api/me").status_code == 401


class TestRetiredFieldsAreGone:
    """`enabled`, `provider` and `login_url` described a login flow this app no longer runs.
    Leaving them as always-false constants would tell the console a sign-in button might
    appear on a surface where one can never exist."""

    def test_response_carries_no_login_flow_fields(self, signed_in: TestClient):
        body = signed_in.get("/api/auth/status").json()
        assert set(body) == {"signed_in", "user_name", "may_approve"}
