"""Live Lakebase schema assertions — the layer that was missing when I-091 shipped.

Skipped by default (they hit live Postgres). Run with:

    pytest -m integration --run-integration

**Why this file exists.** I-091: `signals.py` was changed to `SELECT ... match_basis ...` while
the column existed only in `src/lakebase/14_load_signals.py`'s ALTER block, which had not been
run. The Emerging tab returned **500 on every request** — the entire proactive half of the demo —
and *nothing caught it*:

- the 348-test unit suite passed, because the router tests use fakes and never issue SQL;
- `tests/test_data_quality.py` queries the **SQL warehouse**, not Lakebase, so no test had ever
  connected to Postgres at all;
- CI has no credentials by design, so it could not have caught it either.

The bug was found by curling thirteen routes and noticing one of them was not 200.

**The check is the query itself, not a model of it.** The column list is read out of
`signals.py`'s own source and executed against the live table, so it cannot drift from the code:
adding a column to that SELECT automatically extends this test, and removing the table's column
fails it with Postgres's own `UndefinedColumn`. Asserting against a hand-copied list here would
reproduce exactly the two-places-to-update problem that caused I-091.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

os.environ.setdefault("PSYCOPG_IMPL", "")  # let db.py decide; see I-045

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_ROUTER = ROOT / "app/backend/fleetguard_api/routers/signals.py"

PROFILE = "abhi"
ENDPOINT = "projects/summer-bootcamp-2026-v2/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"


def _connect():
    import psycopg
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=PROFILE)
    host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
    return psycopg.connect(
        host=host,
        user=w.current_user.me().user_name,
        password=token,
        dbname="databricks_postgres",
        sslmode="require",
    )


def signal_row_columns() -> list[str]:
    """The column list `get_signals` selects for each row, read from the router's source.

    Deliberately narrow: it matches the row SELECT (the one starting at `signal_id`), not the
    summary aggregate above it, which selects expressions rather than bare columns.
    """
    src = SIGNALS_ROUTER.read_text()
    match = re.search(r"SELECT\s+(signal_id\b.*?)\s+FROM", src, re.DOTALL)
    assert match, "could not locate the row SELECT in signals.py — has the query been rewritten?"
    cols = [c.strip() for c in match.group(1).replace("\n", " ").split(",")]
    assert all(re.fullmatch(r"\w+", c) for c in cols), f"unexpected non-column token in {cols}"
    return cols


def test_the_router_column_list_was_actually_parsed():
    """Guard the guard. A regex that silently matched nothing would make the real test below
    pass vacuously — which is the same class of false pass as I-012's empty `_rescued_data`."""
    cols = signal_row_columns()
    assert len(cols) > 10, f"suspiciously few columns parsed: {cols}"
    assert "match_basis" in cols, "the I-091 column is no longer in the SELECT — check why"


def test_signals_row_query_runs_against_live_lakebase():
    """Every column `signals.py` selects must exist. This is I-091, encoded.

    Executed rather than compared: Postgres is the authority on whether the query is valid, and
    a `SELECT` that runs is stronger evidence than a set-difference against
    `information_schema` that agrees with itself.
    """
    cols = ", ".join(signal_row_columns())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM {PG_SCHEMA}.fleetguard_defect_signal LIMIT 1")
        assert cur.description is not None


def test_match_basis_is_nullable_because_agent_rows_do_not_persist_it():
    """`agent_actions.py` computes the tier for its reply but does not write it here, so a NOT
    NULL column would reject exactly the rows the write path creates (ARCHITECTURE §7.1)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT is_nullable FROM information_schema.columns
               WHERE table_schema = %s AND table_name = 'fleetguard_defect_signal'
                 AND column_name = 'match_basis'""",
            (PG_SCHEMA,),
        )
        row = cur.fetchone()
        assert row, "match_basis is missing — this is I-091 recurring"
        assert row[0] == "YES", "match_basis must stay nullable"
