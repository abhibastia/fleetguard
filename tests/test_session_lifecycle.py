"""Session expiry and pruning — the half of I-062 that the original fix missed (I-071).

I-062 added server-side session expiry after a repo review found the cookie's `max_age` was
a client-side courtesy only. That fix landed in `auth/tokens.py`'s token providers, which is
where every *data* request is authorised — and it was correct there. But it left two gaps,
both found in the 2026-09-07 review:

  1. `routers/auth_routes.py::auth_status` reads `SESSIONS` directly instead of going
     through a provider, so it kept reporting expired sessions as `signed_in: true` — and,
     for an allowlisted login, `may_approve: true`. Not an access-control hole (the data
     endpoints still 401'd correctly) but a console drawing a signed-in header over an
     everything-401s state.
  2. Rejecting an expired session on read does not *remove* it. `SESSIONS` only ever shrank
     on explicit logout, so on a long-running process it grew without bound — which I-062's
     own writeup named, but its resolution did not cover.

The regression these guard against is drift between two definitions of "still valid", so the
first test here pins the thing that makes drift impossible: both forms share one predicate.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient
from fleetguard_api import deps
from fleetguard_api.auth.tokens import AuthError, _check_not_expired, session_is_live

NOW = 1_800_000_000.0  # arbitrary fixed instant; tests control the clock via now_fn
TTL = 8 * 3600


class TestSessionIsLive:
    """The shared predicate. `_check_not_expired` is its raising twin — same rules."""

    def test_fresh_session_is_live(self):
        assert session_is_live({"created": NOW}, TTL, lambda: NOW) is True

    def test_session_at_exactly_the_ttl_is_still_live(self):
        """Valid through the last second, not evicted one tick early — `> ttl`, not `>=`."""
        assert session_is_live({"created": NOW}, TTL, lambda: NOW + TTL) is True

    def test_session_one_second_past_the_ttl_is_dead(self):
        assert session_is_live({"created": NOW}, TTL, lambda: NOW + TTL + 1) is False

    def test_missing_created_fails_closed(self):
        """A session without `created` did not come from this codebase's login flow, so
        treating unknown age as valid would be the 'fall back to a broader principal' the
        auth seam forbids."""
        assert session_is_live({"user_name": "a@b.com"}, TTL, lambda: NOW) is False

    def test_none_and_empty_are_not_live(self):
        assert session_is_live(None, TTL, lambda: NOW) is False
        assert session_is_live({}, TTL, lambda: NOW) is False

    @pytest.mark.parametrize(
        ("session", "expect_raise"),
        [
            ({"created": NOW}, False),
            ({"created": NOW - TTL - 1}, True),
            ({"no_created": True}, True),
        ],
    )
    def test_raising_twin_agrees_with_the_predicate(self, session, expect_raise):
        """The two must never disagree — one definition of valid, two call shapes. This is
        the actual guard against I-071 recurring."""
        live = session_is_live(session, TTL, lambda: NOW)
        if expect_raise:
            assert live is False
            with pytest.raises(AuthError):
                _check_not_expired(session, TTL, lambda: NOW)
        else:
            assert live is True
            _check_not_expired(session, TTL, lambda: NOW)  # must not raise


class TestPruneSessions:
    """Unbounded growth — I-062's second half, unfixed until I-071."""

    @pytest.fixture(autouse=True)
    def _clean_store(self):
        deps.SESSIONS.clear()
        yield
        deps.SESSIONS.clear()

    def test_removes_expired_and_keeps_live(self):
        deps.SESSIONS.update(
            {
                "live": {"user_name": "a@b.com", "created": NOW},
                "stale": {"user_name": "b@b.com", "created": NOW - TTL - 1},
                "no-created": {"user_name": "c@b.com"},
            }
        )
        removed = deps.prune_sessions(now_fn=lambda: NOW)
        assert removed == 2
        assert set(deps.SESSIONS) == {"live"}

    def test_is_a_no_op_when_everything_is_live(self):
        deps.SESSIONS.update({"a": {"created": NOW}, "b": {"created": NOW}})
        assert deps.prune_sessions(now_fn=lambda: NOW) == 0
        assert len(deps.SESSIONS) == 2

    def test_does_not_raise_on_an_empty_store(self):
        assert deps.prune_sessions(now_fn=lambda: NOW) == 0

    def test_concurrent_insert_during_prune_does_not_raise(self):
        """FastAPI runs sync endpoints in a threadpool, so a login can land mid-prune. If
        `prune_sessions` iterated `_SESSIONS` directly this raises `RuntimeError: dictionary
        changed size during iteration`; the `list(...)` snapshot is what prevents it.

        Driven through `session_is_live` via `now_fn`, so the insert happens *during* the
        comprehension rather than relying on real thread timing to collide.
        """
        deps.SESSIONS.update({f"s{i}": {"created": NOW - TTL - 1} for i in range(20)})
        calls = {"n": 0}

        def now_fn_that_inserts():
            calls["n"] += 1
            if calls["n"] == 5:  # mid-iteration, simulating a concurrent login
                deps.SESSIONS["late-arrival"] = {"user_name": "z@b.com", "created": NOW}
            return NOW

        removed = deps.prune_sessions(now_fn=now_fn_that_inserts)
        assert removed == 20  # the 20 stale ones, all evicted
        assert set(deps.SESSIONS) == {"late-arrival"}  # the live insert survived

    def test_repeated_pruning_is_stable(self):
        """Pruning mutates the dict it iterates — the list comprehension materialises the
        keys first for exactly that reason. A second call must be a clean no-op, not a
        RuntimeError or a partial sweep."""
        deps.SESSIONS.update({f"s{i}": {"created": NOW - TTL - 1} for i in range(5)})
        assert deps.prune_sessions(now_fn=lambda: NOW) == 5
        assert deps.prune_sessions(now_fn=lambda: NOW) == 0
        assert deps.SESSIONS == {}


class TestAuthStatusRespectsExpiry:
    """The user-visible half of I-071, at the endpoint rather than the helper.

    `/api/auth/status` is what the console reads to decide whether to draw a signed-in
    header and an enabled Approve button. Before this fix it answered from a bare
    `SESSIONS.get(...)`, so an expired session produced `signed_in: true` and — for an
    allowlisted login — `may_approve: true`, while every data request 401'd underneath it.
    """

    @pytest.fixture()
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "app-login")
        monkeypatch.setitem(os.environ, "GITHUB_CLIENT_ID", "cid")
        monkeypatch.setitem(os.environ, "GITHUB_CLIENT_SECRET", "secret")
        monkeypatch.setitem(os.environ, "FLEETGUARD_APPROVERS", "operator")
        from fleetguard_api import main

        deps.get_token_provider.cache_clear()
        deps.SESSIONS.clear()
        yield TestClient(main.app)
        deps.SESSIONS.clear()

    def test_live_session_reports_signed_in_and_may_approve(self, client: TestClient):
        deps.SESSIONS["live"] = {"user_name": "operator", "created": time.time()}
        client.cookies.set("fg_session", "live")
        body = client.get("/api/auth/status").json()
        assert body["signed_in"] is True
        assert body["user_name"] == "operator"
        assert body["may_approve"] is True

    def test_expired_session_reports_signed_out(self, client: TestClient):
        """The regression. Pre-fix this returned signed_in=True for an 8h+ old session."""
        deps.SESSIONS["stale"] = {"user_name": "operator", "created": time.time() - TTL - 60}
        client.cookies.set("fg_session", "stale")
        body = client.get("/api/auth/status").json()
        assert body["signed_in"] is False
        assert body["user_name"] is None

    def test_expired_session_never_reports_may_approve(self, client: TestClient):
        """Worst version of the bug: an expired session for an allowlisted login showed an
        enabled Approve button. `may_approve` is derived from the user, so it must go false
        with it — asserted separately because this is the claim with teeth."""
        deps.SESSIONS["stale"] = {"user_name": "operator", "created": time.time() - TTL - 60}
        client.cookies.set("fg_session", "stale")
        body = client.get("/api/auth/status").json()
        assert body["may_approve"] is False

    def test_status_call_prunes_the_expired_session(self, client: TestClient):
        """auth_status is where pruning is driven from, so hitting it must actually shrink
        the store — otherwise the leak persists behind a correct-looking response."""
        deps.SESSIONS["stale"] = {"user_name": "operator", "created": time.time() - TTL - 60}
        deps.SESSIONS["live"] = {"user_name": "other", "created": time.time()}
        client.get("/api/auth/status")
        assert set(deps.SESSIONS) == {"live"}
