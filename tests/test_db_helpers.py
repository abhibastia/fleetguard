"""Unit tests for db.py's pure helpers.

`rows_to_dicts` runs on every SELECT this API executes — psycopg returns tuples, the API
returns objects, and this is the one place that conversion happens. Untested until this
review despite that reach.
"""

from fleetguard_api.db import rows_to_dicts


class _FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCursor:
    """The subset of psycopg.Cursor's interface rows_to_dicts actually touches."""

    def __init__(self, columns: list[str], rows: list[tuple]) -> None:
        self.description = [_FakeColumn(c) for c in columns]
        self._rows = rows

    def fetchall(self):
        return self._rows


def test_zips_columns_to_values_in_order():
    cur = _FakeCursor(["vin", "depot_id"], [("1FT...", "DEP-01"), ("2FT...", "DEP-02")])
    assert rows_to_dicts(cur) == [
        {"vin": "1FT...", "depot_id": "DEP-01"},
        {"vin": "2FT...", "depot_id": "DEP-02"},
    ]


def test_empty_result_set_is_an_empty_list_not_none():
    cur = _FakeCursor(["vin"], [])
    assert rows_to_dicts(cur) == []


def test_null_values_become_python_none():
    cur = _FakeCursor(["component", "remedy"], [("STEERING", None)])
    assert rows_to_dicts(cur) == [{"component": "STEERING", "remedy": None}]


def test_column_order_is_preserved_even_when_it_looks_unsorted():
    # A caller relies on key names, not position, but a silent column/value transposition
    # would still pass a naive equality check if it happened to sort keys the same way —
    # this uses distinguishable, non-alphabetical values to catch that class of bug.
    cur = _FakeCursor(["z_col", "a_col"], [("first", "second")])
    row = rows_to_dicts(cur)[0]
    assert row["z_col"] == "first"
    assert row["a_col"] == "second"
