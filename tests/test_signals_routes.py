"""The Emerging tab's contract, and specifically the column that made it lie for a week.

`fleet_vehicles` is the number this view ranks on, and until I-079 it was computed by an exact
make/model join across two vocabularies that do not agree — NHTSA's spelling in the detector,
vPIC's in the fleet registry. Two signals reported **0** against 2,418 and 766 real vehicles.
The fix reports the count *with its match tier*, because `MODEL_VARIANT` is genuinely weaker
than `EXACT` and a bare number cannot say so.

These tests cover the Python either side of that column: that the tier survives the trip from
row to response, that its absence degrades to "not recorded" rather than to a wrong tier, and
that the query still asks for it. The SQL itself is verified against live Lakebase — see
I-079's measured before/after — not re-asserted through a mock.
"""

from __future__ import annotations

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import signals
from fleetguard_api.routers.signals import get_signals

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

SUMMARY = [{"total": 48, "live": 9, "fleet_relevant": 4, "as_of_month": None}]


def _row(**over):
    """One detector row. Only the fields under test are interesting; the rest exist because
    the response model requires them."""
    return {
        "signal_id": "RAM|PROMASTER|ENGINE:2026-03",
        "series_key": "RAM|PROMASTER|ENGINE AND ENGINE COOLING",
        "make": "RAM",
        "model": "PROMASTER",
        "component": "ENGINE AND ENGINE COOLING",
        "run_start": None,
        "run_end": None,
        "run_len": 3,
        "max_z": 2.4,
        "complaint_count": 31,
        "harm_share": 0.1,
        "fleet_vehicles": 2418,
        "match_basis": "MODEL_VARIANT",
        "is_live": False,
        "status": "OPEN",
        "source": "DETECTOR",
        "opened_by": None,
    } | over


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(signals.snapshot, "is_snapshot", lambda: False)


def _cursor(rows):
    return FakeCursor({"COUNT(*) AS total": SUMMARY, "SELECT signal_id": rows})


class TestMatchBasis:
    def test_variant_tier_reaches_the_response(self, monkeypatch):
        """The 2,418-vehicle RAM PROMASTER match is a variant match, and the console renders a
        VARIANT badge from this field. If it silently became None the badge would vanish and
        the number would read as exact — which is the precise failure I-079 traded away."""
        cur = _cursor([_row()])
        install(monkeypatch, signals, cur)

        result = get_signals(USER, fleet_only=False, limit=50)

        assert result.signals[0].fleet_vehicles == 2418
        assert result.signals[0].match_basis == "MODEL_VARIANT"

    def test_exact_tier_is_preserved_not_flattened(self, monkeypatch):
        cur = _cursor([_row(match_basis="EXACT", fleet_vehicles=766)])
        install(monkeypatch, signals, cur)

        assert get_signals(USER, fleet_only=False, limit=50).signals[0].match_basis == "EXACT"

    def test_missing_column_degrades_to_none_not_an_error(self, monkeypatch):
        """The committed snapshot.json predates this column, and agent-opened rows never carry
        one. Both must render, so absence has to mean "not recorded" — a required field here
        would 500 the whole tab for one missing key, and a non-null default would assert a
        tier nobody measured."""
        row = _row()
        del row["match_basis"]
        cur = _cursor([row])
        install(monkeypatch, signals, cur)

        assert get_signals(USER, fleet_only=False, limit=50).signals[0].match_basis is None

    def test_explicit_null_is_carried_through(self, monkeypatch):
        """An AGENT row: `agent_actions.py` computes the tier for its reply but does not
        persist it. NULL must stay NULL rather than being coerced to 'NONE', which would claim
        the fleet was checked and found empty."""
        cur = _cursor([_row(source="AGENT", opened_by="ops@example.com", match_basis=None)])
        install(monkeypatch, signals, cur)

        assert get_signals(USER, fleet_only=False, limit=50).signals[0].match_basis is None

    def test_query_still_selects_the_tier(self, monkeypatch):
        """Asserted on the SQL because nothing else can catch its removal: dropping the column
        from the SELECT would make every row's tier None via the default above, and every test
        that does not pin the column would still pass."""
        cur = _cursor([])
        install(monkeypatch, signals, cur)

        get_signals(USER, fleet_only=False, limit=50)

        assert "match_basis" in cur.sql_for("SELECT signal_id")


class TestFleetOnlyFilter:
    def test_fleet_only_filters_on_the_count_not_the_tier(self, monkeypatch):
        """`fleet_only` must stay `fleet_vehicles > 0`. Narrowing it to EXACT would hide the
        very signals I-079 recovered — both of them are variant matches — and quietly restore
        the undercount the fix exists to remove."""
        cur = _cursor([])
        install(monkeypatch, signals, cur)

        get_signals(USER, fleet_only=True, limit=50)

        sql = cur.sql_for("SELECT signal_id")
        assert "fleet_vehicles > 0" in sql
        assert "match_basis =" not in sql

    def test_no_filter_when_fleet_only_is_false(self, monkeypatch):
        cur = _cursor([])
        install(monkeypatch, signals, cur)

        get_signals(USER, fleet_only=False, limit=50)

        assert "WHERE fleet_vehicles > 0" not in cur.sql_for("SELECT signal_id")
