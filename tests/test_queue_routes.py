"""The operator's primary surface — and, until 2026-09-07, the largest untested router.

`queue.py` carries three decisions that are easy to break silently and impossible to notice
from a passing build: the depot scope predicate, VIN masking, and the snapshot short-circuit.
It also owns the ranking rule the whole queue exists to express — consequence before volume —
which lives in `ORDER BY` and so is asserted here as "the handler does not re-sort", the only
part of it Python can get wrong.
"""

from __future__ import annotations

import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import queue
from fleetguard_api.routers.queue import get_campaign, get_queue, get_queue_summary

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

CAMPAIGN_HEAD = [
    {
        "campaign_id": "17V629000",
        "component": "STEERING",
        "park_it": True,
        "consequence": "Loss of steering control.",
        "remedy": "Tighten the pinch bolt.",
    }
]


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(queue.snapshot, "is_snapshot", lambda: False)


class TestGetQueue:
    def test_returns_rows_in_the_order_the_database_gave_them(self, monkeypatch):
        """Ranking is consequence-before-volume and lives in the SQL's ORDER BY. The handler
        must not re-sort — a 25-vehicle do-not-drive defect outranks a 1,801-vehicle label
        recall, and sorting by count in Python would silently undo that judgement."""
        rows = [
            {
                "campaign_id": "PARKIT-SMALL",
                "component": "STEERING",
                "park_it": True,
                "do_not_drive": True,
                "vehicles_exposed": 25,
                "depots_affected": 22,
                "consequence": "c",
            },
            {
                "campaign_id": "LABEL-HUGE",
                "component": "LABEL",
                "park_it": False,
                "do_not_drive": False,
                "vehicles_exposed": 1801,
                "depots_affected": 60,
                "consequence": "c",
            },
        ]
        cur = FakeCursor({"fleetguard_vehicle_exposure": rows})
        install(monkeypatch, queue, cur)

        result = get_queue(USER, depot_id=None, limit=50)

        assert [r.campaign_id for r in result] == ["PARKIT-SMALL", "LABEL-HUGE"]

    def test_no_depot_id_applies_no_scope_predicate(self, monkeypatch):
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id=None, limit=50)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "depot_id = %(depot_id)s" not in sql
        assert "depot_id" not in (cur.params_for("fleetguard_vehicle_exposure") or {})

    def test_depot_id_binds_as_a_parameter_not_string_interpolation(self, monkeypatch):
        """The scope predicate must arrive as a bound parameter. `Scope` returns a fragment
        plus params precisely so a depot id can never be concatenated into SQL."""
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id="DEP-053", limit=50)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "v.depot_id = %(depot_id)s" in sql
        assert "DEP-053" not in sql  # the value itself never reaches the statement
        assert cur.params_for("fleetguard_vehicle_exposure")["depot_id"] == "DEP-053"

    def test_limit_is_passed_through_as_a_parameter(self, monkeypatch):
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id=None, limit=7)

        assert cur.params_for("fleetguard_vehicle_exposure")["limit"] == 7

    def test_snapshot_mode_never_touches_the_database(self, monkeypatch):
        """Snapshot mode must short-circuit before `connect()`. A snapshot deployment holds
        no Databricks credential, so reaching the connection is not a slow path — it is an
        error."""
        monkeypatch.setattr(queue.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(queue.snapshot, "queue", lambda limit, launched_only=False: [])

        def explode(*a, **k):
            raise AssertionError("connect() must not be called in snapshot mode")

        monkeypatch.setattr(queue, "connect", explode)

        assert get_queue(USER, depot_id=None, limit=50) == []

    def test_launched_campaign_carries_its_service_campaign_id(self, monkeypatch):
        """Before this, the queue looked identical whether a recall had already been launched
        or not — the only way to find out was clicking Approve and hitting the 409 from the
        one-active-campaign-per-recall uniqueness index (I-063). The LEFT JOIN must surface
        that state instead of requiring the user to trigger the error to discover it."""
        rows = [
            {
                "campaign_id": "ALREADY-LAUNCHED",
                "component": "STEERING",
                "park_it": False,
                "do_not_drive": False,
                "vehicles_exposed": 10,
                "depots_affected": 3,
                "consequence": "c",
                "service_campaign_id": "SC-ALREADY-LAUNCHED-abc123",
            },
            {
                "campaign_id": "NOT-LAUNCHED",
                "component": "BRAKES",
                "park_it": False,
                "do_not_drive": False,
                "vehicles_exposed": 5,
                "depots_affected": 2,
                "consequence": "c",
                # No key at all — a plain SQL row with no match wouldn't include the column
                # as an explicit None either; the model's default must cover both shapes.
            },
        ]
        cur = FakeCursor({"fleetguard_vehicle_exposure": rows})
        install(monkeypatch, queue, cur)

        result = get_queue(USER, depot_id=None, limit=50)

        by_id = {r.campaign_id: r for r in result}
        assert by_id["ALREADY-LAUNCHED"].service_campaign_id == "SC-ALREADY-LAUNCHED-abc123"
        assert by_id["NOT-LAUNCHED"].service_campaign_id is None

    def test_launched_only_filters_before_the_limit_via_having(self, monkeypatch):
        """The whole point of `launched_only`: it must filter BEFORE `LIMIT`, not after — a
        `WHERE`/client-side filter on an already-capped, ranked page is exactly the I-130-shaped
        bug this closes (a launched campaign ranked outside the default top 50 was invisible
        under any filter, found live 2026-10-04). `HAVING` is the only clause that can see
        `MAX(sc.service_campaign_id)` (it's a post-aggregation value) and still run before
        `ORDER BY`/`LIMIT`, so asserting its presence AND its position is the real assertion —
        presence alone wouldn't catch a regression that moved the filter to the wrong clause."""
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id=None, limit=50, launched_only=True)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        having = "HAVING MAX(sc.service_campaign_id) IS NOT NULL"
        assert having in sql
        assert sql.index(having) < sql.index("ORDER BY")
        assert sql.index(having) < sql.index("LIMIT")

    def test_launched_only_false_omits_the_having_clause(self, monkeypatch):
        """`launched_only=False` explicitly, not relied on as a default — calling the handler
        directly (as every test here does) bypasses FastAPI's dependency resolution, so an
        unpassed `Query(False, ...)` parameter binds to the `Query` object itself (truthy),
        not the resolved `False` a real request gets. Same reason every other test in this
        file always passes `limit=` explicitly too."""
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id=None, limit=50, launched_only=False)

        assert "HAVING" not in cur.sql_for("fleetguard_vehicle_exposure")

    def test_queue_join_scopes_to_launched_status_only(self, monkeypatch):
        """A CANCELLED prior attempt must not read as launched — `campaign_id` is not unique
        in `fleetguard_service_campaign` on its own, only `(campaign_id) WHERE status =
        'LAUNCHED'` is (I-063). Asserting the SQL text is the only way to catch a future edit
        that widens or drops this scope, since a fake row set can't express "this join
        condition was wrong" the way a live status column can."""
        cur = FakeCursor({"fleetguard_vehicle_exposure": []})
        install(monkeypatch, queue, cur)

        get_queue(USER, depot_id=None, limit=50)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "LEFT JOIN" in sql
        assert "sc.status = 'LAUNCHED'" in sql


class TestGetCampaign:
    def _script(self, vehicles):
        return {
            "WHERE c.campaign_id = %(cid)s": CAMPAIGN_HEAD,
            "COUNT(DISTINCT e.vin) AS n": [
                {"depot_id": "DEP-001", "n": 2},
                {"depot_id": "DEP-002", "n": 1},
            ],
            "e.vin, v.depot_id, v.make": vehicles,
        }

    def test_unknown_campaign_is_404(self, monkeypatch):
        cur = FakeCursor({"WHERE c.campaign_id = %(cid)s": []})
        install(monkeypatch, queue, cur)

        with pytest.raises(HTTPException) as exc:
            get_campaign(USER, "NOPE", depot_id=None, sample=25)
        assert exc.value.status_code == 404

    def test_service_campaign_id_passes_through_when_already_launched(self, monkeypatch):
        """Same gap as the queue list: the detail page showed an Approve form with no hint
        that approving would 409, because nothing here ever asked whether a launch already
        existed."""
        head_with_launch = [{**CAMPAIGN_HEAD[0], "service_campaign_id": "SC-17V629000-xyz"}]
        cur = FakeCursor({**self._script([]), "WHERE c.campaign_id = %(cid)s": head_with_launch})
        install(monkeypatch, queue, cur)

        detail = get_campaign(USER, "17V629000", depot_id=None, sample=25)

        assert detail.service_campaign_id == "SC-17V629000-xyz"

    def test_service_campaign_id_is_none_when_not_launched(self, monkeypatch):
        cur = FakeCursor(self._script([]))
        install(monkeypatch, queue, cur)

        detail = get_campaign(USER, "17V629000", depot_id=None, sample=25)

        assert detail.service_campaign_id is None

    def test_vehicles_exposed_is_summed_from_the_depot_breakdown(self, monkeypatch):
        """`vehicles_exposed` is derived, not selected — it must equal the by-depot totals, or
        the header contradicts the table directly beneath it."""
        cur = FakeCursor(
            self._script(
                [
                    {
                        "vin": "1" * 17,
                        "depot_id": "DEP-001",
                        "make": "M",
                        "model": "X",
                        "model_year": 2020,
                    }
                ]
            )
        )
        install(monkeypatch, queue, cur)

        detail = get_campaign(USER, "17V629000", depot_id=None, sample=25)

        assert detail.by_depot == {"DEP-001": 2, "DEP-002": 1}
        assert detail.vehicles_exposed == 3

    def test_vins_are_unmasked_for_an_unscoped_caller(self, monkeypatch):
        """`resolve_scope` returns `mask_vin=False` for every caller today. Pinned so that a
        future change to masking is a deliberate edit here, not a silent one — the operator
        needs the VIN to identify the unit."""
        vin = "3AKJGLDRXHS199804"
        cur = FakeCursor(
            self._script(
                [{"vin": vin, "depot_id": "DEP-001", "make": "M", "model": "X", "model_year": 2020}]
            )
        )
        install(monkeypatch, queue, cur)

        detail = get_campaign(USER, "17V629000", depot_id=None, sample=25)

        assert detail.sample_vehicles[0].vin == vin

    def test_scope_predicate_reaches_both_the_breakdown_and_the_sample(self, monkeypatch):
        """Two separate queries read vehicle rows. Scoping one and not the other would show a
        depot-scoped operator a correct total beside another depot's vehicles."""
        cur = FakeCursor(self._script([]))
        install(monkeypatch, queue, cur)

        get_campaign(USER, "17V629000", depot_id="DEP-001", sample=25)

        assert "v.depot_id = %(depot_id)s" in cur.sql_for("COUNT(DISTINCT e.vin) AS n")
        assert "v.depot_id = %(depot_id)s" in cur.sql_for("e.vin, v.depot_id, v.make")

    def test_sample_size_is_bound_as_a_parameter(self, monkeypatch):
        cur = FakeCursor(self._script([]))
        install(monkeypatch, queue, cur)

        get_campaign(USER, "17V629000", depot_id=None, sample=5)

        assert cur.params_for("e.vin, v.depot_id, v.make")["sample"] == 5


class TestQueueSummary:
    """I-130. The stat cards were derived from the fetched page, so they reported the API's
    own `limit` as a campaign count and summed `vehicles_exposed` across campaigns — counting
    a VIN once per campaign it matched. Measured live: 393 campaigns / 11,323 distinct VINs,
    displayed as 50 / 51,615. These pin the properties that made that impossible to notice."""

    def test_it_counts_distinct_vins_not_a_sum_over_campaigns(self, monkeypatch):
        """The whole defect in one assertion. `COUNT(DISTINCT e.vin)` is also how
        `fleet_exposure_metrics` defines `Vehicles Exposed`, so the console and the AI/BI
        dashboard agree by construction rather than by coincidence."""
        cur = FakeCursor(
            {
                "fleetguard_vehicle_exposure": [
                    {"campaigns": 393, "vehicles_exposed": 11323, "urgent_campaigns": 6}
                ]
            }
        )
        install(monkeypatch, queue, cur)

        out = get_queue_summary(USER, depot_id=None)

        assert out.vehicles_exposed == 11323
        assert out.campaigns == 393
        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "COUNT(DISTINCT e.vin)" in sql
        assert "SUM(" not in sql

    def test_it_has_no_limit(self, monkeypatch):
        """A totals query that paginates is not a totals query. This is the bug itself: the
        cards were counting a 50-row page."""
        cur = FakeCursor(
            {
                "fleetguard_vehicle_exposure": [
                    {"campaigns": 1, "vehicles_exposed": 1, "urgent_campaigns": 0}
                ]
            }
        )
        install(monkeypatch, queue, cur)

        get_queue_summary(USER, depot_id=None)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "LIMIT" not in sql.upper()
        assert "limit" not in cur.params_for("fleetguard_vehicle_exposure")

    def test_depot_scope_still_applies_and_binds_as_a_parameter(self, monkeypatch):
        """Totals must respect depot containment exactly as the list does, or a scoped
        operator reads a fleet-wide number off a scoped page."""
        cur = FakeCursor(
            {
                "fleetguard_vehicle_exposure": [
                    {"campaigns": 2, "vehicles_exposed": 9, "urgent_campaigns": 1}
                ]
            }
        )
        install(monkeypatch, queue, cur)

        get_queue_summary(USER, depot_id="DEP-053")

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        assert "v.depot_id = %(depot_id)s" in sql
        assert "DEP-053" not in sql
        assert cur.params_for("fleetguard_vehicle_exposure")["depot_id"] == "DEP-053"

    def test_it_joins_the_same_three_tables_as_the_list(self, monkeypatch):
        """Totals describing a different population from the rows beneath them would be a
        subtler version of the same bug."""
        cur = FakeCursor(
            {
                "fleetguard_vehicle_exposure": [
                    {"campaigns": 1, "vehicles_exposed": 1, "urgent_campaigns": 0}
                ]
            }
        )
        install(monkeypatch, queue, cur)

        get_queue_summary(USER, depot_id=None)

        sql = cur.sql_for("fleetguard_vehicle_exposure")
        for table in (
            "fleetguard_vehicle_exposure",
            "fleetguard_vehicle",
            "fleetguard_recall_campaign",
        ):
            assert table in sql

    def test_snapshot_mode_never_touches_the_database(self, monkeypatch):
        monkeypatch.setattr(queue.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            queue.snapshot,
            "queue_summary",
            lambda: {"campaigns": 393, "vehicles_exposed": 11323, "urgent_campaigns": 6},
        )

        def explode(*a, **k):
            raise AssertionError("connect() must not be called in snapshot mode")

        monkeypatch.setattr(queue, "connect", explode)

        assert get_queue_summary(USER, depot_id=None).campaigns == 393
