"""`update_work_order`'s remaining error branches — the gate itself is covered by
`test_work_order_gate.py` and the write semantics by `test_work_order_mutations.py`. This
covers: the work order doesn't exist, an assignment names a technician not active at the same
depot (the check this feature exists for, per the handler's own docstring), and a mid-write
failure rolls back rather than committing a partial update.
"""

from __future__ import annotations

import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import work_orders
from fleetguard_api.routers.work_orders import WorkOrderUpdate, update_work_order

APPROVER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")
SELECT_FOR_UPDATE = "FOR UPDATE"


@pytest.fixture(autouse=True)
def _approver_and_live(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)


def test_unknown_work_order_is_a_404(monkeypatch):
    cur = FakeCursor({SELECT_FOR_UPDATE: []})
    install(monkeypatch, work_orders, cur)

    with pytest.raises(HTTPException) as exc:
        update_work_order(APPROVER, "WO-NOPE", WorkOrderUpdate(status="COMPLETED"))

    assert exc.value.status_code == 404


def test_assigning_a_technician_from_a_different_depot_is_rejected(monkeypatch):
    """The check this feature exists for: a technician picker that let you assign DEP-051's
    work to a DEP-042 technician would be decorative, not a real constraint."""
    cur = FakeCursor(
        {
            SELECT_FOR_UPDATE: [
                {
                    "status": "OPEN",
                    "depot_id": "DEP-001",
                    "assigned_to": None,
                    "actual_cost": None,
                }
            ],
            # The roster check finds nothing — either the technician doesn't exist, isn't
            # active, or belongs to a different depot than this work order.
            "fleetguard_technician": [],
        }
    )
    install(monkeypatch, work_orders, cur)

    with pytest.raises(HTTPException) as exc:
        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(assigned_to="TECH-DEP-999-1"))

    assert exc.value.status_code == 400
    assert "not an active technician" in exc.value.detail


def test_a_failed_write_rolls_back_rather_than_committing_a_partial_update(monkeypatch):
    cur = FakeCursor(
        {
            SELECT_FOR_UPDATE: [
                {
                    "status": "OPEN",
                    "depot_id": "DEP-001",
                    "assigned_to": None,
                    "actual_cost": None,
                }
            ],
        },
        raise_on=("UPDATE bootcamp_students.fleetguard_work_order", RuntimeError("boom")),
    )
    conn = install(monkeypatch, work_orders, cur)

    with pytest.raises(RuntimeError):
        update_work_order(APPROVER, "WO-1", WorkOrderUpdate(status="COMPLETED"))

    assert conn.rolled_back
    assert not conn.committed
