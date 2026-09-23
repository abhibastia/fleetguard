"""Work-order mutation semantics and the cost breakdown's reconcilability.

Two low-severity findings from the 2026-09-07 review, both of the "produces a plausible wrong
number" kind rather than the "throws an error" kind:

  - `completed_at` was stamped with a bare `now()` on every save where status was COMPLETED,
    so re-saving an already-completed work order silently replaced the real completion time.
  - `cost_breakdown`'s two tables counted different populations, so their `total_work_orders`
    columns could disagree on the same page with no way to tell which was right.

The gate tests live in `test_work_order_gate.py`; this file is about what the handler *writes*
once the gate has let it through.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import work_orders
from fleetguard_api.routers.work_orders import (
    WorkOrderUpdate,
    cost_breakdown,
    update_work_order,
)

APPROVER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")
SELECT_FOR_UPDATE = "FOR UPDATE"
UPDATE_STMT = "UPDATE bootcamp_students.fleetguard_work_order"

# `created_at` is NOT NULL DEFAULT now() in the schema, so a real row always carries one —
# the fixture must too, or the response model rejects it for a reason the test is not about.
ROW = {
    "wo_id": "WO-1",
    "service_campaign_id": "SC-1",
    "vin": "V1",
    "depot_id": "DEP-001",
    "assigned_to": None,
    "assigned_to_name": None,
    "due_date": None,
    "status": "COMPLETED",
    "created_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    "completed_at": datetime(2026, 9, 4, 18, 51, tzinfo=UTC),
    "actual_cost": None,
}


@pytest.fixture(autouse=True)
def _approver_and_live(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)


def _cursor(current_status: str = "OPEN") -> FakeCursor:
    return FakeCursor(
        {
            SELECT_FOR_UPDATE: [
                {
                    "status": current_status,
                    "depot_id": "DEP-001",
                    "assigned_to": None,
                    "actual_cost": None,
                }
            ],
            "LEFT JOIN bootcamp_students.fleetguard_technician": [ROW],
        }
    )


class TestCompletedAt:
    def test_completing_preserves_an_existing_timestamp(self, monkeypatch):
        """The bug: a bare `now()` overwrote the original completion time whenever an
        already-COMPLETED work order was saved again. The UI re-sends the current status on
        any change, so COMPLETED -> COMPLETED is routine, and the audit row would read
        'COMPLETED -> COMPLETED', leaving the real time unrecoverable."""
        cur = _cursor(current_status="COMPLETED")
        install(monkeypatch, work_orders, cur)

        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status="COMPLETED"))

        assert "COALESCE(completed_at, now())" in cur.sql_for(UPDATE_STMT)

    def test_completed_is_terminal(self, monkeypatch):
        """CHANGED 2026-09-20 (I-110 #14). This used to assert the opposite — that moving
        out of COMPLETED cleared `completed_at` — because the API validated the status
        *value* and never the *transition*, so un-completing a work order was reachable.

        A work order is an instruction to a depot about a specific vehicle. Finishing it is
        terminal in the world, so it is terminal here: the remedy for one completed in error
        is a new work order, not a silent rewrite of the finished one. The `ELSE NULL` branch
        of the `completed_at` CASE is still there and still correct — it is simply no longer
        reachable *from* COMPLETED.
        """
        cur = _cursor(current_status="COMPLETED")
        install(monkeypatch, work_orders, cur)

        with pytest.raises(HTTPException) as exc:
            update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status="OPEN"))

        assert exc.value.status_code == 409
        assert "cannot move to OPEN" in exc.value.detail
        # `sql_for` raises when nothing matched, so assert over `executed` directly.
        assert all(UPDATE_STMT not in sql for sql, _ in cur.executed), (
            "nothing may be written on a refused transition"
        )

    def test_cost_only_update_does_not_touch_completed_at(self, monkeypatch):
        """Logging a cost against a finished job must not restamp or clear its completion —
        `completed_at` is only written when `status` is in the request at all."""
        cur = _cursor(current_status="COMPLETED")
        install(monkeypatch, work_orders, cur)

        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(actual_cost=1875.50))

        sql = cur.sql_for(UPDATE_STMT)
        assert "actual_cost" in sql
        assert "completed_at" not in sql

    def test_assignment_only_update_does_not_touch_completed_at(self, monkeypatch):
        cur = FakeCursor(
            {
                SELECT_FOR_UPDATE: [
                    {
                        "status": "COMPLETED",
                        "depot_id": "DEP-001",
                        "assigned_to": None,
                        "actual_cost": None,
                    }
                ],
                "fleetguard_technician\n": [{"1": 1}],  # roster check finds the technician
                "LEFT JOIN bootcamp_students.fleetguard_technician": [ROW],
            }
        )
        install(monkeypatch, work_orders, cur)

        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(assigned_to="TECH-DEP-001-1"))

        assert "completed_at" not in cur.sql_for(UPDATE_STMT)


class TestCostBreakdownReconciles:
    """The two tables render side by side under the same heading, so their populations must
    match. `service_campaign_id` is nullable, so only a LEFT JOIN guarantees that."""

    def test_component_query_left_joins_so_no_work_order_is_dropped(self, monkeypatch):
        cur = FakeCursor({})
        install(monkeypatch, work_orders, cur)

        cost_breakdown(APPROVER)

        component_sql = cur.sql_for("rc.component")
        assert "LEFT JOIN" in component_sql
        # An inner join on either hop would silently drop unattributable rows.
        assert "\n                JOIN" not in component_sql

    def test_unattributable_work_orders_get_an_explicit_bucket(self, monkeypatch):
        cur = FakeCursor({})
        install(monkeypatch, work_orders, cur)

        cost_breakdown(APPROVER)

        assert "'(unattributed)'" in cur.sql_for("rc.component")

    def test_totals_reconcile_between_the_two_tables(self, monkeypatch):
        """The property that matters, asserted on returned data rather than SQL text: every
        work order counted by depot is also counted by component."""
        cur = FakeCursor(
            {
                "rc.component": [
                    {
                        "key": "STEERING",
                        "total_actual_cost": 100.0,
                        "costed_count": 1,
                        "total_work_orders": 20,
                    },
                    {
                        "key": "(unattributed)",
                        "total_actual_cost": 0.0,
                        "costed_count": 0,
                        "total_work_orders": 5,
                    },
                ],
                "GROUP BY w.depot_id": [
                    {
                        "key": "DEP-001",
                        "total_actual_cost": 100.0,
                        "costed_count": 1,
                        "total_work_orders": 25,
                    }
                ],
            }
        )
        install(monkeypatch, work_orders, cur)

        out = cost_breakdown(APPROVER)

        assert sum(r.total_work_orders for r in out.by_component) == sum(
            r.total_work_orders for r in out.by_depot
        )

    def test_snapshot_returns_empty_without_connecting(self, monkeypatch):
        monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            work_orders, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
        )

        out = cost_breakdown(APPROVER)
        assert out.by_component == [] and out.by_depot == []


class TestStatusTransitions:
    """I-110 #14. The DB CHECK and the `Literal` both constrain the status *value*; neither
    constrained the *transition*, so the API would take a COMPLETED work order back to OPEN
    or restart a CANCELLED one.
    """

    LEGAL = [
        ("OPEN", "IN_PROGRESS"),
        ("OPEN", "CANCELLED"),
        ("OPEN", "OPEN"),
        ("IN_PROGRESS", "COMPLETED"),
        ("IN_PROGRESS", "CANCELLED"),
        ("IN_PROGRESS", "IN_PROGRESS"),
        ("COMPLETED", "COMPLETED"),
        ("CANCELLED", "CANCELLED"),
    ]
    ILLEGAL = [
        ("COMPLETED", "OPEN"),
        ("COMPLETED", "IN_PROGRESS"),
        ("COMPLETED", "CANCELLED"),
        ("CANCELLED", "OPEN"),
        ("CANCELLED", "IN_PROGRESS"),
        ("CANCELLED", "COMPLETED"),
        ("OPEN", "COMPLETED"),
    ]

    @pytest.mark.parametrize(("before", "after"), LEGAL)
    def test_legal_transitions_are_written(self, monkeypatch, before, after):
        cur = _cursor(current_status=before)
        install(monkeypatch, work_orders, cur)

        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status=after))

        assert "status = %(status)s" in cur.sql_for(UPDATE_STMT)

    @pytest.mark.parametrize(("before", "after"), ILLEGAL)
    def test_illegal_transitions_are_409_and_write_nothing(self, monkeypatch, before, after):
        cur = _cursor(current_status=before)
        install(monkeypatch, work_orders, cur)

        with pytest.raises(HTTPException) as exc:
            update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status=after))

        assert exc.value.status_code == 409
        assert all(UPDATE_STMT not in sql for sql, _ in cur.executed)

    def test_open_to_completed_must_pass_through_in_progress(self, monkeypatch):
        """Not pedantry: `IN_PROGRESS` is what tells a depot the vehicle is actually off the
        road. Jumping straight to COMPLETED from OPEN means a work order that was never
        started reports as finished."""
        cur = _cursor(current_status="OPEN")
        install(monkeypatch, work_orders, cur)

        with pytest.raises(HTTPException) as exc:
            update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status="COMPLETED"))

        assert "IN_PROGRESS" in exc.value.detail

    def test_every_status_has_a_transition_rule(self):
        """A status missing from the table would fall to `frozenset()` and become a dead
        end — reachable and then permanently stuck."""
        assert set(work_orders.ALLOWED_TRANSITIONS) == set(work_orders.STATUSES)

    def test_every_status_permits_itself(self):
        """The UI re-sends the current status whenever any other field changes, so a no-op
        status change must never block editing an assignee or a cost."""
        for s, allowed in work_orders.ALLOWED_TRANSITIONS.items():
            assert s in allowed, f"{s} cannot be re-saved"

    def test_a_status_free_update_skips_the_check_entirely(self, monkeypatch):
        """Logging a cost against a COMPLETED work order stays legal — the transition rule
        applies to `status`, and `status` is not in this request."""
        cur = _cursor(current_status="COMPLETED")
        install(monkeypatch, work_orders, cur)

        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(actual_cost=1875.50))

        assert "actual_cost" in cur.sql_for(UPDATE_STMT)
