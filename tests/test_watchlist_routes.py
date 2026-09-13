"""The watchlist read endpoint — a plain list over what `agent_actions.py`'s `watch_campaign`
write path committed. No detector, no gold layer, no tiered match — just a bookmark with a
reason, so these tests only need to cover the snapshot short-circuit and the round trip.
"""

from __future__ import annotations

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import watchlist
from fleetguard_api.routers.watchlist import list_watchlist

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")


def _row(**over):
    return {
        "watchlist_id": "WATCH-test",
        "campaign_id": "17V629000",
        "rationale": "worth tracking",
        "watched_by": "ops@example.com",
        "status": "ACTIVE",
        "watched_at": "2026-09-13T12:00:00Z",
    } | over


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(watchlist.snapshot, "is_snapshot", lambda: False)


def test_snapshot_mode_returns_an_empty_list(monkeypatch):
    """Same reasoning as approval.py's list_service_campaigns: a snapshot deployment has no
    Databricks identity to write under, so nothing has genuinely been watched there."""
    monkeypatch.setattr(watchlist.snapshot, "is_snapshot", lambda: True)
    monkeypatch.setattr(
        watchlist, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
    )

    assert list_watchlist(USER, limit=50) == []


def test_rows_round_trip_through_the_response_model(monkeypatch):
    cur = FakeCursor({"SELECT watchlist_id": [_row(), _row(watchlist_id="WATCH-other")]})
    install(monkeypatch, watchlist, cur)

    result = list_watchlist(USER, limit=50)

    assert [r.watchlist_id for r in result] == ["WATCH-test", "WATCH-other"]
    assert result[0].campaign_id == "17V629000"
    assert result[0].status == "ACTIVE"


def test_limit_is_passed_through_as_a_bound_parameter(monkeypatch):
    cur = FakeCursor({"SELECT watchlist_id": []})
    install(monkeypatch, watchlist, cur)

    list_watchlist(USER, limit=7)

    assert cur.params_for("SELECT watchlist_id")["limit"] == 7
