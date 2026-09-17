"""`list_work_orders`' query-assembly path — untested until this review despite being the
main GET endpoint behind the Work Orders view. `test_work_order_gate.py` and
`test_work_order_mutations.py` cover the write side; this covers the read side: snapshot
mode short-circuits, filters compose into the WHERE clause correctly, and the depot-scope
column is qualified (`w.depot_id`) to avoid the join ambiguity the router's own comment
warns about.

Every `Query(...)`-defaulted parameter is passed explicitly on every call — calling the
handler directly (not through FastAPI's dependency injection) binds the literal `Query(...)`
marker object as the default, which is truthy, so omitting an argument does not mean "no
filter" the way it does through a real request.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fakes import FakeCursor, install
from fleetguard_api import scoping as real_scoping
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import work_orders
from fleetguard_api.routers.work_orders import list_work_orders

CALLER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

ROW = {
    "wo_id": "WO-1",
    "service_campaign_id": "SC-1",
    "vin": "V1",
    "depot_id": "DEP-001",
    "assigned_to": None,
    "assigned_to_name": None,
    "due_date": None,
    "status": "OPEN",
    "created_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    "completed_at": None,
    "actual_cost": None,
}


def test_snapshot_mode_returns_empty_rather_than_querying(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: True)
    result = list_work_orders(
        CALLER, service_campaign_id=None, depot_id=None, status_=None, limit=100
    )
    assert result == []


def test_lists_work_orders_from_the_join(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    result = list_work_orders(
        CALLER, service_campaign_id=None, depot_id=None, status_=None, limit=100
    )

    assert len(result) == 1
    assert result[0].wo_id == "WO-1"


def test_service_campaign_filter_is_added_to_the_where_clause(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    list_work_orders(CALLER, service_campaign_id="SC-1", depot_id=None, status_=None, limit=100)

    sql = cur.sql_for("LEFT JOIN")
    params = cur.params_for("LEFT JOIN")
    assert "w.service_campaign_id = %(service_campaign_id)s" in sql
    assert params["service_campaign_id"] == "SC-1"


def test_status_filter_is_added_to_the_where_clause(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    list_work_orders(
        CALLER, service_campaign_id=None, depot_id=None, status_="COMPLETED", limit=100
    )

    sql = cur.sql_for("LEFT JOIN")
    params = cur.params_for("LEFT JOIN")
    assert "w.status = %(status)s" in sql
    assert params["status"] == "COMPLETED"


def test_no_filters_means_no_where_clause(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    list_work_orders(CALLER, service_campaign_id=None, depot_id=None, status_=None, limit=100)

    sql = cur.sql_for("LEFT JOIN")
    assert "WHERE" not in sql


def test_depot_scope_column_is_qualified_to_avoid_join_ambiguity(monkeypatch):
    """The router joins fleetguard_technician, which also has a depot_id column — the scope
    predicate must reference w.depot_id explicitly, not a bare column name."""
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    captured = {}

    def fake_resolve_scope(principal, depot_id, *, column):
        captured["column"] = column
        return real_scoping.resolve_scope(principal, depot_id, column=column)

    monkeypatch.setattr(work_orders, "resolve_scope", fake_resolve_scope)
    list_work_orders(CALLER, service_campaign_id=None, depot_id="DEP-001", status_=None, limit=100)

    assert captured["column"] == "w.depot_id"


def test_limit_is_passed_through_to_the_query(monkeypatch):
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    cur = FakeCursor({"LEFT JOIN": [ROW]})
    install(monkeypatch, work_orders, cur)

    list_work_orders(CALLER, service_campaign_id=None, depot_id=None, status_=None, limit=17)

    params = cur.params_for("LEFT JOIN")
    assert params["limit"] == 17
