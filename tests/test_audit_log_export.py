"""The audit log's JSON list and CSV export — the compliance artifact.

The CSV path does real work in Python: header, per-row serialisation, `None` handling for
`before_state` (a LAUNCH has no before), and the `Content-Disposition` filename. It is also
where a formula-injection question lives, which is why that is pinned explicitly below rather
than left to be rediscovered.

The filters are shared between the JSON and CSV endpoints via `_fetch`, so the WHERE-assembly
assertions here cover both.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from fakes import FakeCursor, install
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import audit_log
from fleetguard_api.routers.audit_log import export_audit_log_csv, list_audit_log

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")
Q = "fleetguard_audit_log"

LAUNCH_ROW = {
    "audit_id": 5,
    "entity_type": "service_campaign",
    "entity_id": "SC-17V629000-b3b9dfbd",
    "action": "LAUNCH",
    "actor_principal": "ops@example.com",
    "before_state": None,  # a launch has no prior state
    "after_state": {"campaign_id": "17V629000", "work_orders": 25},
    "created_at": __import__("datetime").datetime(2026, 9, 1, 21, 56, 26),
}
STATUS_ROW = {
    "audit_id": 6,
    "entity_type": "work_order",
    "entity_id": "WO-abc",
    "action": "STATUS_CHANGE",
    "actor_principal": "ops@example.com",
    "before_state": {"status": "OPEN"},
    "after_state": {"status": "COMPLETED"},
    "created_at": __import__("datetime").datetime(2026, 9, 4, 18, 51, 52),
}


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(audit_log.snapshot, "is_snapshot", lambda: False)


def _csv_rows(resp) -> list[list[str]]:
    return list(csv.reader(io.StringIO(resp.body.decode())))


class TestFilters:
    def test_no_filters_emits_no_where_clause(self, monkeypatch):
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        list_audit_log(USER, entity_type=None, entity_id=None, action=None, limit=200)

        assert "WHERE" not in cur.sql_for(Q)

    def test_filters_are_bound_parameters_not_interpolated(self, monkeypatch):
        """`entity_id` is caller-supplied and reaches SQL — it must bind, never concatenate."""
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        list_audit_log(
            USER, entity_type="work_order", entity_id="WO-abc", action="STATUS_CHANGE", limit=50
        )

        sql, params = cur.sql_for(Q), cur.params_for(Q)
        assert "entity_type = %(entity_type)s" in sql
        assert "entity_id = %(entity_id)s" in sql
        assert "action = %(action)s" in sql
        assert "WO-abc" not in sql  # the value never reaches the statement text
        assert params["entity_id"] == "WO-abc"
        assert params["limit"] == 50

    def test_one_filter_produces_a_single_predicate_with_no_stray_and(self, monkeypatch):
        """Asserted against the WHERE clause alone, not the whole statement — every column
        name also appears in the SELECT list, so a naive `"entity_id" not in sql` can never
        hold (it failed exactly that way on first run)."""
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        list_audit_log(USER, entity_type="work_order", entity_id=None, action=None, limit=200)

        where = cur.sql_for(Q).split("WHERE")[1].split("ORDER BY")[0]
        assert "entity_type = %(entity_type)s" in where
        assert "entity_id" not in where
        assert "action" not in where
        assert "AND" not in where

    def test_two_filters_are_joined_with_and(self, monkeypatch):
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        list_audit_log(USER, entity_type="work_order", entity_id="WO-abc", action=None, limit=200)

        where = cur.sql_for(Q).split("WHERE")[1].split("ORDER BY")[0]
        assert where.count("AND") == 1
        assert "entity_type = %(entity_type)s" in where
        assert "entity_id = %(entity_id)s" in where


class TestCsvExport:
    def test_header_and_row_count(self, monkeypatch):
        cur = FakeCursor({Q: [LAUNCH_ROW, STATUS_ROW]})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        assert rows[0] == [
            "audit_id",
            "entity_type",
            "entity_id",
            "action",
            "actor_principal",
            "before_state",
            "after_state",
            "created_at",
        ]
        assert len(rows) == 3  # header + 2 data rows

    def test_null_before_state_becomes_an_empty_cell_not_the_string_none(self, monkeypatch):
        """A LAUNCH has no prior state. `str(None)` would write the literal 'None' into a
        compliance export, which reads as a value rather than an absence."""
        cur = FakeCursor({Q: [LAUNCH_ROW]})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        assert rows[1][5] == ""  # before_state
        assert json.loads(rows[1][6]) == {"campaign_id": "17V629000", "work_orders": 25}

    def test_state_columns_are_valid_json_round_trippable(self, monkeypatch):
        """The export is meant to be machine-readable downstream, so the JSON must survive
        CSV quoting intact."""
        cur = FakeCursor({Q: [STATUS_ROW]})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        assert json.loads(rows[1][5]) == {"status": "OPEN"}
        assert json.loads(rows[1][6]) == {"status": "COMPLETED"}

    def test_free_text_stays_json_wrapped_so_it_cannot_start_a_formula(self, monkeypatch):
        """CSV formula injection, pinned deliberately.

        The only caller-controlled free text in this export (a campaign's `title` and
        `rationale`) travels *inside* `after_state`, so the cell begins with `{` and Excel or
        Sheets cannot read it as a formula. That protection is a side effect of the JSON
        wrapping rather than a deliberate guard — so if anyone later promotes `rationale` to
        its own column, this test is what should fail and prompt real escaping.
        """
        hostile = {
            **LAUNCH_ROW,
            "after_state": {"rationale": "=cmd|'/c calc'!A1", "campaign_id": "17V629000"},
        }
        cur = FakeCursor({Q: [hostile]})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        after = rows[1][6]
        assert after.startswith("{"), "free text escaped its JSON wrapper — re-check injection"
        assert json.loads(after)["rationale"] == "=cmd|'/c calc'!A1"  # preserved, not mangled

    def test_filename_is_a_utc_timestamp_and_attachment(self, monkeypatch):
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        resp = export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)

        disposition = resp.headers["content-disposition"]
        assert disposition.startswith("attachment; ")
        assert "fleetguard_audit_log_" in disposition
        assert disposition.rstrip('"').endswith("Z.csv")
        assert resp.media_type == "text/csv"

    def test_empty_log_still_returns_a_header_row(self, monkeypatch):
        """An empty export must be an empty *table*, not an empty file — otherwise it is
        indistinguishable from a failed download."""
        cur = FakeCursor({Q: []})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        assert len(rows) == 1
        assert rows[0][0] == "audit_id"


class TestCsvFormulaInjectionGuard:
    """`_csv_safe` — belt-and-suspenders over the JSON-wrapping protection above. That
    protection is a side effect of `before_state`/`after_state` being JSON (always `{`-led);
    this guard is the real one, applied to every cell, for the day any column carries raw
    free text instead."""

    def test_csv_safe_prefixes_a_leading_formula_character(self):
        for bad in ("=cmd|'/c calc'!A1", "+SUM(A1)", "-2+3", "@SUM(A1)"):
            assert audit_log._csv_safe(bad) == f"'{bad}"

    def test_csv_safe_leaves_ordinary_text_untouched(self):
        assert audit_log._csv_safe("LAUNCH") == "LAUNCH"
        assert audit_log._csv_safe("") == ""

    def test_a_raw_column_value_starting_with_a_formula_character_is_escaped_in_the_export(
        self, monkeypatch
    ):
        hostile = {**LAUNCH_ROW, "entity_id": "=cmd|'/c calc'!A1"}
        cur = FakeCursor({Q: [hostile]})
        install(monkeypatch, audit_log, cur)

        rows = _csv_rows(
            export_audit_log_csv(USER, entity_type=None, entity_id=None, action=None, limit=5000)
        )

        assert rows[1][2] == "'=cmd|'/c calc'!A1"


def test_snapshot_mode_returns_empty_without_connecting(monkeypatch):
    monkeypatch.setattr(audit_log.snapshot, "is_snapshot", lambda: True)

    def explode(*a, **k):
        raise AssertionError("connect() must not be called in snapshot mode")

    monkeypatch.setattr(audit_log, "connect", explode)

    assert list_audit_log(USER, entity_type=None, entity_id=None, action=None, limit=200) == []
