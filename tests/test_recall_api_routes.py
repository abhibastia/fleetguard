"""The recall-API status route — where the interesting cases are the *empty* ones.

This surface exists to answer "is the live recall feed working, and has it found anything".
The failure that matters is not a crash: it is rendering "the feed is broken" and "the feed
is working and there is no news" identically. Both are common states here — the alert table
held zero rows for weeks and then found two real campaigns on the same day — so the tests
below spend most of their effort on that distinction rather than on the happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import recall_api
from fleetguard_api.routers.recall_api import recall_api_status

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

# Distinctive fragments of each of the four statements the handler issues.
HEAD_Q = "COUNT(*) FILTER (WHERE last_status = 'ok')"
ALERT_TOTALS_Q = "COUNT(DISTINCT campaign_number)"
POLL_LIST_Q = "SELECT combo_key, make, model"
ALERT_LIST_Q = "SELECT alert_key, campaign_number"

POLLED_AT = datetime(2026, 9, 20, 14, 39, tzinfo=UTC)


def head(combos=200, ok=200, vehicles=33231, at=POLLED_AT):
    return [
        {
            "combos": combos,
            "combos_ok": ok,
            "fleet_vehicles_covered": vehicles,
            "last_polled_at": at,
        }
    ]


def alert_totals(alerts=0, campaigns=0, exposed=0):
    return [
        {
            "alerts": alerts,
            "alert_campaigns": campaigns,
            "vehicles_exposed_by_alerts": exposed,
        }
    ]


def combo(**over):
    row = {
        "combo_key": "FREIGHTLINER|CASCADIA|2020",
        "make": "FREIGHTLINER",
        "model": "CASCADIA",
        "model_year": 2020,
        "fleet_vehicles": 235,
        "last_polled_at": POLLED_AT,
        "last_status": "ok",
        "last_campaign_count": 7,
    }
    return row | over


def alert(**over):
    row = {
        "alert_key": "26V583000|FREIGHTLINER|CASCADIA|2020",
        "campaign_number": "26V583000",
        "make": "FREIGHTLINER",
        "model": "CASCADIA",
        "model_year": 2020,
        "component": "SERVICE BRAKES, AIR:ANTILOCK",
        "park_it": False,
        "park_outside": False,
        "consequence": "Increased stopping distance.",
        "vehicles_exposed": 235,
        "depots_affected": 59,
    }
    return row | over


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(recall_api.snapshot, "is_snapshot", lambda: False)


def test_healthy_feed_with_no_alerts_is_not_an_error_state(monkeypatch):
    """The state this route spent most of its life in, and the one most easily mis-read.

    200/200 combos OK with zero alerts means the feed works and there is no news. The
    summary must carry a real success rate so a client can distinguish it from a broken
    feed, rather than presenting two empty lists.
    """
    cur = FakeCursor({HEAD_Q: head(), ALERT_TOTALS_Q: alert_totals(), POLL_LIST_Q: [combo()]})
    install(monkeypatch, recall_api, cur)

    out = recall_api_status(USER)

    assert out.summary.combos == 200
    assert out.summary.success_rate_pct == 100.0
    assert out.summary.alerts == 0
    assert out.alerts == []
    assert out.summary.fleet_vehicles_covered == 33231


def test_never_polled_reports_unknown_success_rate_not_zero(monkeypatch):
    """Zero combos must give `None`, never 0.0.

    0% success is a catastrophic feed; "not run yet" is a Tuesday. Rendering them the same
    is the same class of mistake as `gold_agent_activity_daily` reporting 0% for untraced
    agent calls, and it is also a division by zero waiting to happen.
    """
    cur = FakeCursor(
        {
            HEAD_Q: head(combos=0, ok=0, vehicles=0, at=None),
            ALERT_TOTALS_Q: alert_totals(),
        }
    )
    install(monkeypatch, recall_api, cur)

    out = recall_api_status(USER)

    assert out.summary.combos == 0
    assert out.summary.success_rate_pct is None
    assert out.summary.last_polled_at is None
    assert out.poll == []


def test_partial_failure_is_reported_as_a_rate(monkeypatch):
    cur = FakeCursor({HEAD_Q: head(combos=200, ok=180), ALERT_TOTALS_Q: alert_totals()})
    install(monkeypatch, recall_api, cur)

    assert recall_api_status(USER).summary.success_rate_pct == 90.0


def test_alerts_are_returned_with_their_exposure(monkeypatch):
    cur = FakeCursor(
        {
            HEAD_Q: head(),
            ALERT_TOTALS_Q: alert_totals(alerts=5, campaigns=2, exposed=651),
            POLL_LIST_Q: [combo()],
            ALERT_LIST_Q: [
                alert(),
                alert(
                    alert_key="26V578000|FORD|F-150|2024",
                    campaign_number="26V578000",
                    make="FORD",
                    model="F-150",
                    model_year=2024,
                    vehicles_exposed=272,
                    depots_affected=60,
                ),
            ],
        }
    )
    install(monkeypatch, recall_api, cur)

    out = recall_api_status(USER)

    assert out.summary.alerts == 5
    # Five rows, two campaigns: one campaign spans several model years. Asserting both
    # numbers is what stops a later "simplification" collapsing the two into one count.
    assert out.summary.alert_campaigns == 2
    assert out.summary.vehicles_exposed_by_alerts == 651
    assert [a.campaign_number for a in out.alerts] == ["26V583000", "26V578000"]


def test_one_campaign_spanning_model_years_keeps_distinct_alert_keys(monkeypatch):
    """The grain that a `campaign_number` primary key got wrong on first contact with live
    data (see `25_load_recall_api_status.py`). Four rows, one campaign, four keys."""
    rows = [
        alert(alert_key=f"26V583000|FREIGHTLINER|CASCADIA|{y}", model_year=y, vehicles_exposed=v)
        for y, v in ((2020, 235), (2022, 79), (2023, 50), (2021, 15))
    ]
    cur = FakeCursor(
        {
            HEAD_Q: head(),
            ALERT_TOTALS_Q: alert_totals(alerts=4, campaigns=1, exposed=379),
            ALERT_LIST_Q: rows,
        }
    )
    install(monkeypatch, recall_api, cur)

    out = recall_api_status(USER)

    assert len({a.alert_key for a in out.alerts}) == 4
    assert len({a.campaign_number for a in out.alerts}) == 1


def test_limit_is_bound_as_a_parameter(monkeypatch):
    cur = FakeCursor({HEAD_Q: head(), ALERT_TOTALS_Q: alert_totals(), POLL_LIST_Q: [combo()]})
    install(monkeypatch, recall_api, cur)

    recall_api_status(USER, limit=25)

    assert cur.params_for(POLL_LIST_Q)["limit"] == 25


def test_snapshot_mode_never_touches_the_database(monkeypatch):
    monkeypatch.setattr(recall_api.snapshot, "is_snapshot", lambda: True)

    def explode(*_a, **_k):
        raise AssertionError("snapshot mode must not open a Lakebase connection")

    monkeypatch.setattr(recall_api, "connect", explode)

    out = recall_api_status(USER)

    assert out.summary.combos == 0
    assert out.summary.success_rate_pct is None
    assert out.poll == [] and out.alerts == []
