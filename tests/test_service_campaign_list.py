"""`list_service_campaigns`' live-query path — until now only its snapshot-mode short-circuit
was exercised (via `test_watchlist_routes.py`'s comment, not a real test of this function).
This covers the per-status work-order breakdown query actually running and the `limit` bound
being threaded through, which is what the `WorkOrders.tsx` dropdown depends on (see the
`le=500` comment in the handler itself).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import approval
from fleetguard_api.routers.approval import list_service_campaigns

CALLER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

ROW = {
    "service_campaign_id": "SC-1",
    "campaign_id": "24V123",
    "title": "Brake pad recall",
    "vehicle_count": 12,
    "status": "APPROVED",
    "approved_by": "ops@example.com",
    "approved_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    "open_count": 5,
    "in_progress_count": 2,
    "completed_count": 4,
    "cancelled_count": 1,
    "total_actual_cost": 1234.56,
    "costed_count": 4,
}


def test_snapshot_mode_returns_empty_rather_than_querying(monkeypatch):
    monkeypatch.setattr(approval.snapshot, "is_snapshot", lambda: True)
    assert list_service_campaigns(CALLER, limit=50) == []


def test_lists_campaigns_with_the_work_order_breakdown(monkeypatch):
    monkeypatch.setattr(approval.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"FROM bootcamp_students.fleetguard_service_campaign": [ROW]})
    install(monkeypatch, approval, cur)

    result = list_service_campaigns(CALLER, limit=50)

    assert len(result) == 1
    assert result[0]["service_campaign_id"] == "SC-1"
    assert result[0]["open_count"] == 5
    assert result[0]["total_actual_cost"] == 1234.56


def test_limit_is_passed_through_to_the_query(monkeypatch):
    monkeypatch.setattr(approval.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"FROM bootcamp_students.fleetguard_service_campaign": [ROW]})
    install(monkeypatch, approval, cur)

    list_service_campaigns(CALLER, limit=500)

    params = cur.params_for("FROM bootcamp_students.fleetguard_service_campaign")
    assert params["limit"] == 500
