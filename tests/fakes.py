"""A minimal fake Lakebase cursor/connection, shared by the router tests.

**What these tests are for, and what they are not.** The routers' SQL is exercised for real
by the live integration tests and by manual verification against Lakebase; re-asserting it
through a mock would only prove the mock matches the string. What regresses silently is the
*Python* around the SQL — WHERE-clause assembly, merging several result sets, defaulting a
depot that returned no rows, masking a VIN, honouring snapshot mode. That is what these fake
out the database to reach.

`script` maps a substring of a SQL statement to the rows that statement should return, so a
test states what the database contains rather than replaying an opaque sequence of calls.
Statements matching nothing return `[]`, which is the right default for INSERTs and for the
"this depot has no work orders" case that motivated several of these tests.
"""

from __future__ import annotations

from typing import Any


class FakeCursor:
    def __init__(
        self,
        script: dict[str, list[dict]] | None = None,
        raise_on: tuple[str, Exception] | None = None,
    ) -> None:
        self._script = script or {}
        self._raise_on = raise_on
        self._rows: list[dict] = []
        self.executed: list[tuple[str, Any]] = []
        self.description = True  # rows_to_dicts only checks that this is truthy

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        if self._raise_on and self._raise_on[0] in sql:
            raise self._raise_on[1]
        for needle, rows in self._script.items():
            if needle in sql:
                self._rows = rows
                return
        self._rows = []

    def executemany(self, sql: str, seq) -> None:
        self.executed.append((sql, list(seq)))

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def fetchone(self):
        """Returns a **tuple**, matching psycopg's default row factory.

        This matters: `rows_to_dicts` builds dicts using `cursor.description`, but code that
        calls `fetchone()` directly — `trends.py`'s `MAX(issued_at)` lookup, `work_orders.py`'s
        technician existence check — indexes positionally (`fetchone()[0]`). A fake returning
        dicts here passes `fetchall`-based tests while raising `KeyError: 0` on exactly the
        code paths it was meant to cover.
        """
        if not self._rows:
            return None
        row = self._rows[0]
        return tuple(row.values()) if isinstance(row, dict) else row

    # -- assertion helpers -------------------------------------------------------------

    def sql_for(self, needle: str) -> str:
        """The first executed statement containing `needle`. Raises if none did, so a test
        asserting on a query that never ran fails loudly instead of silently passing."""
        for sql, _ in self.executed:
            if needle in sql:
                return sql
        raise AssertionError(f"no executed statement contained {needle!r}")

    def params_for(self, needle: str) -> Any:
        for sql, params in self.executed:
            if needle in sql:
                return params
        raise AssertionError(f"no executed statement contained {needle!r}")

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def install(monkeypatch, module, cursor: FakeCursor) -> FakeConn:
    """Point a router module's `connect` and `rows_to_dicts` at the fake.

    Both are patched on the *router* module rather than on `db`, because routers import the
    names directly (`from ..db import connect`) — patching `db.connect` would not affect the
    already-bound reference.
    """
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(module, "rows_to_dicts", lambda cur: cur.fetchall())
    return conn
