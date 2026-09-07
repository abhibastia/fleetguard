"""Technicians, signals, and trends — the three thin routers, covered for the first time.

Each is a single query with a little Python around it, so these tests stay proportionate:
filter assembly, the snapshot short-circuit, and the one or two derived values each endpoint
computes. Not an attempt to re-test SQL through a fake.
"""

from __future__ import annotations

from datetime import date

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import signals as signals_mod
from fleetguard_api.routers import technicians as tech_mod
from fleetguard_api.routers import trends as trends_mod
from fleetguard_api.routers.signals import get_signals
from fleetguard_api.routers.technicians import list_technicians
from fleetguard_api.routers.trends import recall_trend

USER = Principal(token="tok", user_name="ops@example.com", source="render-u2m")

TECH_Q = "fleetguard_technician"
SIGNAL_ROWS_Q = "signal_id, series_key"
SIGNAL_SUMMARY_Q = "COUNT(*) AS total"
TREND_Q = "EXTRACT(YEAR FROM c.issued_at)"
TREND_MAX_Q = "MAX(issued_at)"


class TestTechnicians:
    @pytest.fixture(autouse=True)
    def _live(self, monkeypatch):
        monkeypatch.setattr(tech_mod.snapshot, "is_snapshot", lambda: False)

    def test_active_only_defaults_on(self, monkeypatch):
        """The roster's default view is who can actually be assigned work today. A retired
        technician appearing in an assignment dropdown is the failure this prevents."""
        cur = FakeCursor({TECH_Q: []})
        install(monkeypatch, tech_mod, cur)

        list_technicians(USER, depot_id=None, active_only=True)

        assert "active = true" in cur.sql_for(TECH_Q)

    def test_active_only_false_drops_the_predicate(self, monkeypatch):
        cur = FakeCursor({TECH_Q: []})
        install(monkeypatch, tech_mod, cur)

        list_technicians(USER, depot_id=None, active_only=False)

        sql = cur.sql_for(TECH_Q)
        assert "active = true" not in sql
        assert "WHERE" not in sql

    def test_depot_filter_binds_and_combines_with_active(self, monkeypatch):
        cur = FakeCursor({TECH_Q: []})
        install(monkeypatch, tech_mod, cur)

        list_technicians(USER, depot_id="DEP-053", active_only=True)

        sql = cur.sql_for(TECH_Q)
        assert "depot_id = %(depot_id)s" in sql
        assert "active = true" in sql
        assert "AND" in sql
        assert "DEP-053" not in sql  # bound, never interpolated
        assert cur.params_for(TECH_Q)["depot_id"] == "DEP-053"

    def test_snapshot_returns_empty_without_connecting(self, monkeypatch):
        monkeypatch.setattr(tech_mod.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            tech_mod, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
        )
        assert list_technicians(USER, depot_id=None, active_only=True) == []


class TestSignals:
    SUMMARY = [{"total": 48, "live": 9, "fleet_relevant": 2, "as_of_month": date(2026, 8, 1)}]
    ROW = {
        "signal_id": "S1",
        "series_key": "FORD|F-250|STEERING",
        "make": "FORD",
        "model": "F-250",
        "component": "STEERING",
        "run_start": date(2026, 6, 1),
        "run_end": date(2026, 8, 1),
        "run_len": 3,
        "max_z": 4.2,
        "complaint_count": 31,
        "harm_share": 0.1,
        "fleet_vehicles": 12,
        "is_live": True,
        "status": "LIVE",
    }

    @pytest.fixture(autouse=True)
    def _live(self, monkeypatch):
        monkeypatch.setattr(signals_mod.snapshot, "is_snapshot", lambda: False)

    def test_counts_are_returned_alongside_the_rows(self, monkeypatch):
        """ "9 emerging across NHTSA, 2 affecting you" is a different statement from either
        number alone. If only the filtered list came back, an empty fleet result would look
        like a broken query rather than a real answer."""
        cur = FakeCursor({SIGNAL_SUMMARY_Q: self.SUMMARY, SIGNAL_ROWS_Q: [self.ROW]})
        install(monkeypatch, signals_mod, cur)

        out = get_signals(USER, fleet_only=False, limit=50)

        assert (out.total, out.live, out.fleet_relevant) == (48, 9, 2)
        assert len(out.signals) == 1

    def test_fleet_only_filters_the_rows_but_not_the_counts(self, monkeypatch):
        """The counts stay fleet-wide on purpose — they are the context the filtered list is
        read against.

        The invariant is asserted by running both ways and comparing the summary statement,
        rather than by looking for a predicate string. Substring checks are unusable here:
        the summary query contains both `FILTER (WHERE is_live)` and
        `FILTER (WHERE fleet_vehicles > 0)` legitimately, so every obvious needle also
        matches the counts it is supposed to exonerate — two earlier drafts of this
        assertion failed exactly that way.
        """
        unfiltered = FakeCursor({SIGNAL_SUMMARY_Q: self.SUMMARY, SIGNAL_ROWS_Q: [self.ROW]})
        install(monkeypatch, signals_mod, unfiltered)
        out_all = get_signals(USER, fleet_only=False, limit=50)

        filtered = FakeCursor({SIGNAL_SUMMARY_Q: self.SUMMARY, SIGNAL_ROWS_Q: [self.ROW]})
        install(monkeypatch, signals_mod, filtered)
        out_fleet = get_signals(USER, fleet_only=True, limit=50)

        # The rows query gains the filter...
        assert "WHERE fleet_vehicles > 0" in filtered.sql_for(SIGNAL_ROWS_Q)
        assert "WHERE fleet_vehicles > 0" not in unfiltered.sql_for(SIGNAL_ROWS_Q)
        # ...while the summary statement is byte-identical either way.
        assert filtered.sql_for(SIGNAL_SUMMARY_Q) == unfiltered.sql_for(SIGNAL_SUMMARY_Q)
        assert out_fleet.total == out_all.total == 48

    def test_no_fleet_filter_by_default(self, monkeypatch):
        cur = FakeCursor({SIGNAL_SUMMARY_Q: self.SUMMARY, SIGNAL_ROWS_Q: []})
        install(monkeypatch, signals_mod, cur)

        get_signals(USER, fleet_only=False, limit=50)

        assert "fleet_vehicles > 0" not in cur.sql_for(SIGNAL_ROWS_Q)


class TestTrends:
    POINTS = [
        {"year": 2024, "campaigns": 28, "urgent_campaigns": 0, "vehicles_exposed": 5358},
        {"year": 2025, "campaigns": 32, "urgent_campaigns": 1, "vehicles_exposed": 6019},
        {"year": 2026, "campaigns": 9, "urgent_campaigns": 0, "vehicles_exposed": 3419},
    ]

    @pytest.fixture(autouse=True)
    def _live(self, monkeypatch):
        monkeypatch.setattr(trends_mod.snapshot, "is_snapshot", lambda: False)

    def test_returns_points_and_the_latest_real_filing_date(self, monkeypatch):
        """`latest_issued_at` is what lets the chart mark the current year partial rather than
        drawing a short bar that reads as a decline. It comes from the data's own max date,
        never from `today` — the corpus can lag the calendar."""
        cur = FakeCursor({TREND_Q: self.POINTS, TREND_MAX_Q: [{"max": date(2026, 7, 30)}]})
        install(monkeypatch, trends_mod, cur)

        out = recall_trend(USER)

        assert [p.year for p in out.points] == [2024, 2025, 2026]
        assert out.latest_issued_at == date(2026, 7, 30)

    def test_years_with_no_campaigns_are_absent_not_zero_filled(self, monkeypatch):
        """The endpoint reports only years it actually found. Zero-filling would assert a
        count for a year never queried across a full calendar range."""
        sparse = [self.POINTS[0], self.POINTS[2]]  # 2025 missing entirely
        cur = FakeCursor({TREND_Q: sparse, TREND_MAX_Q: [{"max": date(2026, 7, 30)}]})
        install(monkeypatch, trends_mod, cur)

        assert [p.year for p in recall_trend(USER).points] == [2024, 2026]

    def test_snapshot_returns_empty_points_and_no_date(self, monkeypatch):
        monkeypatch.setattr(trends_mod.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            trends_mod, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
        )

        out = recall_trend(USER)
        assert out.points == []
        assert out.latest_issued_at is None
