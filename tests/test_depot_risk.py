"""Depot risk — three independent aggregates merged in Python, which is where it can break.

`depot_risk` deliberately does *not* do one big multi-join: fleet size, exposure, and
work-order backlog have independently different cardinalities per depot, and joining all
three at once fans out. It runs three `GROUP BY` queries and merges them by `depot_id` in
Python instead — so the interesting failure is a depot present in one result set and absent
from another. A depot with no exposure and no work orders is the *common* case (most of the
60 have neither), and it must come back as zeros rather than a `KeyError` or a missing row.
"""

from __future__ import annotations

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import depots
from fleetguard_api.routers.depots import depot_risk

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

THREE_DEPOTS = [
    {
        "depot_id": "DEP-001",
        "depot_name": "Boston North",
        "region": "NORTHEAST",
        "city": "Boston",
        "state": "MA",
        "fleet_size": 300,
    },
    {
        "depot_id": "DEP-002",
        "depot_name": "Atlanta North",
        "region": "SOUTHEAST",
        "city": "Atlanta",
        "state": "GA",
        "fleet_size": 250,
    },
    {
        "depot_id": "DEP-003",
        "depot_name": "Chicago North",
        "region": "MIDWEST",
        "city": "Chicago",
        "state": "IL",
        "fleet_size": 400,
    },
]

# Keys chosen to match a distinctive fragment of each of the three statements.
DEPOT_Q = "fleetguard_depot d"
EXPOSURE_Q = "urgent_vehicles_exposed"
WORK_Q = "outstanding_work_orders"


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(depots.snapshot, "is_snapshot", lambda: False)


def test_depot_with_no_exposure_and_no_work_orders_is_all_zeros(monkeypatch):
    """The common case — most depots have neither. This is what a missing `.get(..., 0)`
    default would turn into a KeyError, taking the whole page down rather than one row."""
    cur = FakeCursor({DEPOT_Q: THREE_DEPOTS, EXPOSURE_Q: [], WORK_Q: []})
    install(monkeypatch, depots, cur)

    rows = depot_risk(USER)

    assert len(rows) == 3
    for r in rows:
        assert r.urgent_vehicles_exposed == 0
        assert r.total_vehicles_exposed == 0
        assert r.distinct_campaigns == 0
        assert r.outstanding_work_orders == 0
        assert r.overdue_work_orders == 0
    # Fleet size comes from the depot query, so it must survive the empty merges.
    assert {r.depot_id: r.fleet_size for r in rows} == {
        "DEP-001": 300,
        "DEP-002": 250,
        "DEP-003": 400,
    }


def test_merges_exposure_and_work_orders_onto_the_right_depots(monkeypatch):
    """Partial coverage in both directions: DEP-001 has exposure but no work orders, DEP-002
    has work orders but no exposure, DEP-003 has neither. Getting the join key wrong would
    smear one depot's numbers onto another and still return a plausible-looking page."""
    cur = FakeCursor(
        {
            DEPOT_Q: THREE_DEPOTS,
            EXPOSURE_Q: [
                {
                    "depot_id": "DEP-001",
                    "urgent_vehicles_exposed": 12,
                    "total_vehicles_exposed": 40,
                    "distinct_campaigns": 5,
                }
            ],
            WORK_Q: [
                {"depot_id": "DEP-002", "outstanding_work_orders": 7, "overdue_work_orders": 3}
            ],
        }
    )
    install(monkeypatch, depots, cur)

    by_id = {r.depot_id: r for r in depot_risk(USER)}

    assert by_id["DEP-001"].urgent_vehicles_exposed == 12
    assert by_id["DEP-001"].distinct_campaigns == 5
    assert by_id["DEP-001"].outstanding_work_orders == 0  # no work-order row for this depot

    assert by_id["DEP-002"].outstanding_work_orders == 7
    assert by_id["DEP-002"].overdue_work_orders == 3
    assert by_id["DEP-002"].urgent_vehicles_exposed == 0  # no exposure row for this depot

    assert by_id["DEP-003"].total_vehicles_exposed == 0


def test_sorted_by_urgent_exposure_descending(monkeypatch):
    """The page's whole premise is 'which depots are worst off' — the ordering is the
    feature, not a presentation detail."""
    cur = FakeCursor(
        {
            DEPOT_Q: THREE_DEPOTS,
            EXPOSURE_Q: [
                {
                    "depot_id": "DEP-001",
                    "urgent_vehicles_exposed": 5,
                    "total_vehicles_exposed": 5,
                    "distinct_campaigns": 1,
                },
                {
                    "depot_id": "DEP-003",
                    "urgent_vehicles_exposed": 20,
                    "total_vehicles_exposed": 20,
                    "distinct_campaigns": 2,
                },
            ],
            WORK_Q: [],
        }
    )
    install(monkeypatch, depots, cur)

    assert [r.depot_id for r in depot_risk(USER)] == ["DEP-003", "DEP-001", "DEP-002"]


def test_every_depot_is_returned_even_with_no_activity_anywhere(monkeypatch):
    """This view is explicitly fleet-wide — all 60 depots, always, so they can be compared.
    An inner-join-shaped bug would silently drop the quiet ones, which are exactly the rows
    that make 'quiet' visible."""
    cur = FakeCursor({DEPOT_Q: THREE_DEPOTS, EXPOSURE_Q: [], WORK_Q: []})
    install(monkeypatch, depots, cur)

    assert {r.depot_id for r in depot_risk(USER)} == {"DEP-001", "DEP-002", "DEP-003"}


def test_exposure_row_for_an_unknown_depot_is_ignored_not_invented(monkeypatch):
    """The depot query is the source of truth for which depots exist. An exposure row whose
    depot is not in it (stale data, a mid-query change) must not conjure a row with no name
    or region."""
    cur = FakeCursor(
        {
            DEPOT_Q: THREE_DEPOTS,
            EXPOSURE_Q: [
                {
                    "depot_id": "DEP-999",
                    "urgent_vehicles_exposed": 99,
                    "total_vehicles_exposed": 99,
                    "distinct_campaigns": 9,
                }
            ],
            WORK_Q: [],
        }
    )
    install(monkeypatch, depots, cur)

    rows = depot_risk(USER)
    assert "DEP-999" not in {r.depot_id for r in rows}
    assert all(r.urgent_vehicles_exposed == 0 for r in rows)


def test_snapshot_mode_returns_empty_without_connecting(monkeypatch):
    monkeypatch.setattr(depots.snapshot, "is_snapshot", lambda: True)

    def explode(*a, **k):
        raise AssertionError("connect() must not be called in snapshot mode")

    monkeypatch.setattr(depots, "connect", explode)

    assert depot_risk(USER) == []
