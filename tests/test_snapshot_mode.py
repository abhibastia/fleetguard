"""Snapshot mode must serve every read endpoint without a Databricks credential.

**Why this exists even though nothing selects it.** Both deployment surfaces run
`FLEETGUARD_DATA_MODE=lakebase`, so snapshot mode is dormant — but it is dormant, not removed:
it is the only way to run this console with no Databricks credential at all, and it still
works (verified 2026-09-07, all endpoints 200). Dormant-but-functional code with no test is
how you discover on a redeploy that it stopped working three weeks ago.

The failure this actually guards against is adding a new endpoint and forgetting its
`if snapshot.is_snapshot()` branch. Without the guard the handler calls `connect()`, which on
a snapshot deployment has no credential to mint — so the endpoint 500s. Every read endpoint is
enumerated here, so a new one has to be added to this list deliberately, and the person adding
it has to decide what it serves with no database.

`/api/evidence` is included because it is the one route that must work on the public surface
regardless; it reads a committed JSON file rather than Lakebase, so it returns real data here.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Every GET endpoint that reads fleet data. A new router must be added here; that is the
# point of the list being explicit rather than discovered from the route table.
READ_ENDPOINTS = [
    "/api/queue?limit=2",
    "/api/campaigns/17V629000",
    "/api/signals",
    "/api/evidence",
    "/api/work-orders",
    "/api/service-campaigns",
    "/api/cost-breakdown",
    "/api/depot-risk",
    "/api/recall-trend",
    "/api/audit-log",
    "/api/technicians",
]

# Endpoints backed by the committed snapshot file rather than by an empty fallback.
SERVED_FROM_SNAPSHOT = {"/api/queue?limit=2", "/api/campaigns/17V629000", "/api/signals"}


@pytest.fixture()
def snapshot_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """static-dev with a dummy token: proves these paths never reach Lakebase, because that
    token could not mint a credential if they tried."""
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_TOKEN", "not-a-real-token")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DEV_USER", "ops@example.com")
    monkeypatch.setitem(os.environ, "FLEETGUARD_DATA_MODE", "snapshot")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    return TestClient(main.app)


@pytest.mark.parametrize("endpoint", READ_ENDPOINTS)
def test_every_read_endpoint_serves_without_a_credential(snapshot_client, endpoint):
    """A 500 here means a handler reached `connect()` — i.e. a missing snapshot guard."""
    resp = snapshot_client.get(endpoint)
    assert resp.status_code == 200, f"{endpoint} -> {resp.status_code}: {resp.text[:200]}"


@pytest.mark.parametrize(
    "endpoint",
    [e for e in READ_ENDPOINTS if e not in SERVED_FROM_SNAPSHOT and e != "/api/evidence"],
)
def test_unsnapshotted_endpoints_return_an_honest_empty(snapshot_client, endpoint):
    """The snapshot file only covers queue, campaigns and signals. Everything added later
    returns empty — which is the truthful answer ("nothing has been approved on a read-only
    surface"), not a placeholder. Asserted so that a future change to *fabricate* plausible
    data here has to break a test first."""
    body = snapshot_client.get(endpoint).json()
    assert body in (
        [],
        {"by_component": [], "by_depot": []},
        {"points": [], "latest_issued_at": None},
    )


def test_health_declares_the_mode_and_capture_date(snapshot_client):
    """Honesty about freshness is part of the product — the console labels the data from
    this. A snapshot deployment silently reporting `lakebase` is the failure."""
    body = snapshot_client.get("/healthz").json()
    assert body["data_mode"] == "snapshot"
    assert body["snapshot_captured_at"]


def test_evidence_still_serves_real_measured_numbers(snapshot_client):
    """Evidence is the reason the public URL exists; it must not be emptied by snapshot mode
    the way the Lakebase-backed endpoints are."""
    body = snapshot_client.get("/api/evidence").json()
    assert body["real"]["rate_pct"] == 16.0
    assert body["lift"] == 1.44


def test_the_write_path_refuses_rather_than_faking_success(snapshot_client, monkeypatch):
    """A snapshot deployment cannot create work orders. Returning a plausible service-campaign
    id would be the safety-critical path telling a lie (I-050's lesson applied to a write), so
    it must refuse — 501, not 201."""
    monkeypatch.setitem(os.environ, "FLEETGUARD_APPROVERS", "ops@example.com")
    resp = snapshot_client.post(
        "/api/campaigns/17V629000/service-campaign",
        json={
            "title": "should not happen",
            "rationale": "snapshot write attempt",
            "due_in_days": 14,
        },
    )
    assert resp.status_code == 501
