"""One active service campaign per recall (I-063) — the duplicate-approval refusal.

Approving the same recall twice used to create two `fleetguard_service_campaign` rows and a
full duplicate set of work orders, one per exposed vehicle. Not hypothetical: the 2026-09-04
test-data cleanup found six campaigns for recall `17V629000`, several sharing an identical
auto-generated title minutes apart — repeated approvals leaving footprints.

**Two mechanisms, and they are not redundant.** The handler's `SELECT` produces the useful
error message (it names the campaign that already exists). The partial unique index
`ux_fg_service_campaign_active` is what actually *enforces* the rule, because the case being
defended against is a double-click — two requests milliseconds apart, where both read
"nothing there" before either commits. These tests cover the handler halves; the index itself
is proved against live Postgres by `src/lakebase/19_add_service_campaign_uniqueness.py` (both
directions: a duplicate LAUNCHED row is rejected, a CANCELLED row for the same recall is
not), and end-to-end by firing five concurrent approvals — verified 2026-09-07 to yield
exactly one 201, four 409s, and one campaign.

Lakebase is faked here rather than mocked wholesale: these assert the handler's *decisions*
given what the database returns, which is what regresses. The wiring is covered live.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.db import UniqueViolation
from fleetguard_api.routers import approval
from fleetguard_api.routers.approval import ApprovalRequest, approve_campaign

BODY = ApprovalRequest(title="Steering remediation", rationale="Park It campaign.")
APPROVER = Principal(token="tok", user_name="ops@example.com", source="render-u2m")

EXISTING = {
    "service_campaign_id": "SC-21V037000-abc12345",
    "approved_at": datetime(2026, 9, 7, 14, 59, tzinfo=UTC),
}


@pytest.fixture(autouse=True)
def _approver_and_live_mode(monkeypatch):
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    monkeypatch.setattr(approval.snapshot, "is_snapshot", lambda: False)


def test_second_approval_is_refused_with_409(monkeypatch):
    """The common case: the operator (or a double-click) hits Approve on a recall that
    already has a live campaign.

    **Exposure rows are supplied deliberately.** `approve_campaign` has a second, unrelated
    409 ("exposes no vehicles in scope"), so a test that starves the exposure query and then
    asserts only `status_code == 409` passes even with the duplicate check deleted — which is
    exactly what happened on the first draft of this file, caught by re-running it against
    the pre-fix handler. Giving it vehicles means the only 409 available is the one under
    test, and the message assertion pins which.
    """
    cursor = FakeCursor(
        {
            "fleetguard_recall_campaign": [
                {"campaign_id": "21V037000", "component": "BRAKES", "park_it": True}
            ],
            "status = 'LAUNCHED'": [EXISTING],
            "fleetguard_vehicle_exposure": [{"vin": "VIN1", "depot_id": "DEP-001"}],
        }
    )
    conn = install(monkeypatch, approval, cursor)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(APPROVER, "21V037000", BODY)

    assert exc.value.status_code == 409
    assert "already has an active service campaign" in exc.value.detail
    assert conn.rolled_back and not conn.committed


def test_the_409_names_the_campaign_that_already_exists(monkeypatch):
    """A refusal an operator can act on. 'Already approved' with no pointer to *what* leaves
    them re-clicking; the id and launch time are the actionable part."""
    cursor = FakeCursor(
        {
            "fleetguard_recall_campaign": [
                {"campaign_id": "21V037000", "component": "BRAKES", "park_it": True}
            ],
            "status = 'LAUNCHED'": [EXISTING],
        }
    )
    install(monkeypatch, approval, cursor)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(APPROVER, "21V037000", BODY)

    assert "SC-21V037000-abc12345" in exc.value.detail
    assert "2026-09-07 14:59" in exc.value.detail


def test_concurrent_insert_losing_the_race_is_also_409(monkeypatch):
    """The double-click that actually interleaves: the pre-check passed because the other
    request had not committed yet, so the unique index is what refuses the INSERT. The caller
    must see the same 409 — which request lost the race is a timing detail they cannot act on.

    This is the path that makes the pre-check insufficient on its own, so it is asserted
    separately rather than folded into the case above.
    """
    cursor = FakeCursor(
        {
            "fleetguard_recall_campaign": [
                {"campaign_id": "21V037000", "component": "BRAKES", "park_it": True}
            ],
            "status = 'LAUNCHED'": [],  # pre-check sees nothing — the race window
            "fleetguard_vehicle_exposure": [{"vin": "VIN1", "depot_id": "DEP-001"}],
        },
        raise_on=(
            "(service_campaign_id, campaign_id, title",
            UniqueViolation("duplicate key value violates unique constraint"),
        ),
    )
    conn = install(monkeypatch, approval, cursor)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(APPROVER, "21V037000", BODY)

    assert exc.value.status_code == 409
    assert "concurrently" in exc.value.detail
    assert conn.rolled_back and not conn.committed


def test_first_approval_of_an_unlaunched_recall_still_succeeds(monkeypatch):
    """The guard must not block the normal path — a recall with no active campaign approves
    exactly as before."""
    cursor = FakeCursor(
        {
            "fleetguard_recall_campaign": [
                {"campaign_id": "21V037000", "component": "BRAKES", "park_it": True}
            ],
            "status = 'LAUNCHED'": [],
            "fleetguard_vehicle_exposure": [
                {"vin": "VIN1", "depot_id": "DEP-001"},
                {"vin": "VIN2", "depot_id": "DEP-002"},
            ],
        }
    )
    conn = install(monkeypatch, approval, cursor)

    result = approve_campaign(APPROVER, "21V037000", BODY)

    assert result.work_orders_created == 2
    assert result.campaign_id == "21V037000"
    assert result.approved_by == "ops@example.com"
    assert conn.committed and not conn.rolled_back


def test_unknown_recall_still_404s_before_the_duplicate_check(monkeypatch):
    """Ordering matters: a recall that does not exist is a 404, not a 409. The duplicate
    check runs after the existence check and must not shadow it."""
    cursor = FakeCursor({"fleetguard_recall_campaign": []})
    install(monkeypatch, approval, cursor)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(APPROVER, "NOPE", BODY)

    assert exc.value.status_code == 404


def test_non_approver_is_refused_before_any_database_work(monkeypatch):
    """The 403 must come first — an unauthorised caller should not learn whether a recall
    exists or is already launched. Asserted by making any DB use blow up."""

    def explode(*a, **k):
        raise AssertionError("connect() must not be reached for a non-approver")

    monkeypatch.setattr(approval, "connect", explode)
    outsider = Principal(token="tok", user_name="stranger@example.com", source="render-u2m")

    with pytest.raises(HTTPException) as exc:
        approve_campaign(outsider, "21V037000", BODY)

    assert exc.value.status_code == 403
