"""The work-order update (status and/or assignment) must be gated the same way approving a
campaign is — the same allowlist, unconditional on auth source, for the same reason: marking
a safety-recall work order "completed" when it wasn't, or reassigning it, is a real compliance
risk, not casual data entry. Mirrors tests/test_approval_gate.py exactly so the two gates
can't silently drift apart.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import work_orders
from fleetguard_api.routers.work_orders import WorkOrderUpdate, update_work_order

BODY = WorkOrderUpdate(status="IN_PROGRESS")


@pytest.fixture(autouse=True)
def _snapshot_mode(monkeypatch):
    """Reached only if the gate passes; True lets the 501 stand in for 'the gate let this
    request through', without needing a live Lakebase connection."""
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: True)


@pytest.mark.parametrize("source", ["databricks-apps", "static-dev"])
def test_non_approver_is_refused_regardless_of_auth_source(monkeypatch, source):
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "someone-else@example.com")
    principal = Principal(token="tok", user_name="not-an-approver@example.com", source=source)

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", BODY)
    assert exc.value.status_code == 403
    assert "not an approver" in exc.value.detail


@pytest.mark.parametrize("source", ["databricks-apps", "static-dev"])
def test_approver_passes_the_gate_regardless_of_auth_source(monkeypatch, source):
    """Passing the gate means reaching the next check (snapshot -> 501), not a 403."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source=source)

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", BODY)
    assert exc.value.status_code == 501


def test_unset_approvers_blocks_everyone_on_every_source(monkeypatch):
    """No FLEETGUARD_APPROVERS configured means nobody can update a work order, on any
    surface — an explicit decision the operator must make, never a silent default."""
    monkeypatch.delenv("FLEETGUARD_APPROVERS", raising=False)
    principal = Principal(token="tok", user_name="anyone@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", BODY)
    assert exc.value.status_code == 403


def test_unidentified_principal_is_refused_before_the_approver_check():
    principal = Principal(token="", user_name=None, source="databricks-apps")
    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", BODY)
    assert exc.value.status_code == 403
    assert "no identity" in exc.value.detail


def test_empty_body_is_refused_even_for_an_approver(monkeypatch):
    """Neither field set at all - not even an explicit null - is a request that changes
    nothing and must not silently succeed as a no-op."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", WorkOrderUpdate())
    assert exc.value.status_code == 400
    assert "at least one" in exc.value.detail


def test_explicit_unassign_counts_as_a_provided_field(monkeypatch):
    """assigned_to=None is a deliberate unassign, not 'nothing was provided' - it must pass
    the presence check and reach the next stage (snapshot -> 501), same as a status-only body."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", WorkOrderUpdate(assigned_to=None))
    assert exc.value.status_code == 501


def test_cost_only_update_counts_as_a_provided_field(monkeypatch):
    """actual_cost is a third independent field, same presence rule as status/assigned_to -
    setting only it must pass the presence check and reach the next stage (snapshot -> 501)."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", WorkOrderUpdate(actual_cost=1875.50))
    assert exc.value.status_code == 501


def test_negative_actual_cost_is_rejected(monkeypatch):
    """A negative logged cost is always a data-entry mistake - caught here for a clean 400
    rather than letting the live fg_wo_actual_cost_nonnegative CHECK constraint surface a raw
    psycopg error to the caller."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        update_work_order(principal, "WO-1", WorkOrderUpdate(actual_cost=-1))
    assert exc.value.status_code == 400
    assert "cannot be negative" in exc.value.detail


class TestWorkOrderUpdateValidation:
    """The status field is a closed set — a typo must not silently write garbage."""

    def test_valid_statuses_are_accepted(self):
        for s in ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"):
            assert WorkOrderUpdate(status=s).status == s

    def test_invalid_status_is_rejected(self):
        with pytest.raises(ValueError):
            WorkOrderUpdate(status="DONE")

    def test_model_fields_set_distinguishes_omitted_from_explicit_none(self):
        """The whole assign/unassign distinction rests on this Pydantic behaviour - a
        regression here would silently break `test_explicit_unassign_counts_as_a_provided_field`
        above in a way that's easy to miss without a direct check on the mechanism itself."""
        omitted = WorkOrderUpdate(status="OPEN")
        assert "assigned_to" not in omitted.model_fields_set

        explicit_null = WorkOrderUpdate(status="OPEN", assigned_to=None)
        assert "assigned_to" in explicit_null.model_fields_set
