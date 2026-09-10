"""The approver allowlist must gate every auth source, without exception.

Found 2026-09-03: the gate used to be conditional on an app-owned login flow being
configured, which skipped the FLEETGUARD_APPROVERS check entirely for any principal carrying
a real Databricks token — fine when "has a Databricks identity in this shared workspace"
implied "is a trusted operator", wrong once the workspace turned out to include the
judges/cohort too. These tests exercise the gate directly (no Lakebase, no FastAPI request
cycle) so the fix can't silently regress per source.

Parametrized over every source the seam can still produce. Two more (`app-login`,
`render-u2m`) went with Render on 2026-09-10; the invariant is unchanged, the list is shorter.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import approval
from fleetguard_api.routers.approval import ApprovalRequest, approve_campaign

BODY = ApprovalRequest(title="Test campaign", rationale="Exercise the approver gate.")


@pytest.fixture(autouse=True)
def _snapshot_mode(monkeypatch):
    """Reached only if the gate passes; True lets the 501 stand in for 'the gate let this
    request through', without needing a live Lakebase connection."""
    monkeypatch.setattr(approval.snapshot, "is_snapshot", lambda: True)


@pytest.mark.parametrize("source", ["databricks-apps", "static-dev"])
def test_non_approver_is_refused_regardless_of_auth_source(monkeypatch, source):
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "someone-else@example.com")
    principal = Principal(token="tok", user_name="not-an-approver@example.com", source=source)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(principal, "CAMP1", BODY)
    assert exc.value.status_code == 403
    assert "not an approver" in exc.value.detail


@pytest.mark.parametrize("source", ["databricks-apps", "static-dev"])
def test_approver_passes_the_gate_regardless_of_auth_source(monkeypatch, source):
    """Passing the gate means reaching the next check (snapshot -> 501), not a 403."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    principal = Principal(token="tok", user_name="ops@example.com", source=source)

    with pytest.raises(HTTPException) as exc:
        approve_campaign(principal, "CAMP1", BODY)
    assert exc.value.status_code == 501


def test_unset_approvers_blocks_everyone_on_every_source(monkeypatch):
    """No FLEETGUARD_APPROVERS configured means nobody can approve, on any surface — an
    explicit decision the operator must make, never a silent default."""
    monkeypatch.delenv("FLEETGUARD_APPROVERS", raising=False)
    principal = Principal(token="tok", user_name="anyone@example.com", source="databricks-apps")

    with pytest.raises(HTTPException) as exc:
        approve_campaign(principal, "CAMP1", BODY)
    assert exc.value.status_code == 403


def test_unidentified_principal_is_refused_before_the_approver_check():
    principal = Principal(token="", user_name=None, source="databricks-apps")
    with pytest.raises(HTTPException) as exc:
        approve_campaign(principal, "CAMP1", BODY)
    assert exc.value.status_code == 403
    assert "no identity" in exc.value.detail
