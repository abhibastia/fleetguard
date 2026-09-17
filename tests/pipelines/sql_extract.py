"""Pulls the portable transformation logic out of `src/pipelines/**/*.sql`.

**Why this exists.** These files are Lakeflow Declarative Pipeline SQL — `STREAM(...)`,
`CREATE OR REFRESH STREAMING TABLE`, and `EXPECT` constraints are pipeline-runtime syntax
with no equivalent in a plain local Spark session, so the DDL statements themselves cannot
run here at all. What CAN run locally is the `SELECT` logic underneath — the parsing,
normalisation, and `_dq_failures` quarantine-routing rules, which is also where the actual
bugs this project has hit lived (I-022's case-sensitive Yes/No compare, I-012's quote
handling downstream of this, the chunk-count math). Extracting that logic here means the
tests exercise the real, committed SQL text, not a hand-copied reimplementation that could
silently drift from it.

**How.** `--` line comments are stripped first — several of these files use full sentences
in prose comments, and at least one (`silver_recall.sql`) has a literal `;` inside one
("...received; ODATE is 98.1%.") that would otherwise split a statement in half. What's
left is split on top-level `;` (none of these files have a `;` inside a *string literal* —
verified by reading all nine — so a naive split is safe here, not in general). `FROM
STREAM(x)` becomes `FROM x` so a plain batch temp view stands in for the streaming source.
A file's staging view (`CREATE TEMPORARY VIEW stg_x AS ...`) is usable directly once
patched; a file with no staging view (`silver_tsb.sql`) needs the `SELECT` body sliced out
from between the DDL header and the trailing `FROM STREAM(...)`.

If a pipeline file's structure changes enough that these assumptions break, the fix belongs
here, not in a workaround at each call site — every test in this package goes through this
module's two functions.
"""

from __future__ import annotations

import re
from pathlib import Path

PIPELINES_DIR = Path(__file__).resolve().parents[2] / "src" / "pipelines"

_STREAM_REF = re.compile(r"\bFROM\s+STREAM\s*\(\s*(\w+)\s*\)")


def _patch_stream_refs(sql: str) -> str:
    """`FROM STREAM(bronze_x)` -> `FROM bronze_x`, so a plain temp view can stand in."""
    return _STREAM_REF.sub(r"FROM \1", sql)


def _strip_line_comments(text: str) -> str:
    """Truncate each line at its first `--`. Not string-literal-aware in general, but none
    of these nine files use `--` inside a string literal, only in prose comments."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def statements(relative_path: str) -> list[str]:
    """Every top-level statement in a pipeline SQL file, in file order, with `--` comments
    removed first so a `;` inside one (e.g. `silver_recall.sql`'s "...received; ODATE...")
    can't be mistaken for a statement terminator."""
    text = _strip_line_comments((PIPELINES_DIR / relative_path).read_text())
    return [s.strip() for s in text.split(";") if s.strip()]


def staging_view_sql(relative_path: str, view_name: str) -> str:
    """The `CREATE TEMPORARY VIEW <view_name> AS ...` statement, with `STREAM(...)` refs
    patched to plain table reads and `OR REPLACE` added so re-running it across tests in
    the same shared Spark session doesn't collide with the previous test's view. Raises if
    no such statement exists in the file — a silently-empty result would make every test
    using it fail for the wrong reason.

    Matched by substring, not prefix: the file's own leading `--` comment block shares a
    split segment with this statement (no `;` separates them), so the statement text does
    not literally start with `CREATE`.
    """
    marker = f"CREATE TEMPORARY VIEW {view_name} AS"
    for stmt in statements(relative_path):
        idx = stmt.find(marker)
        if idx != -1:
            patched = stmt[idx:].replace(
                "CREATE TEMPORARY VIEW", "CREATE OR REPLACE TEMPORARY VIEW", 1
            )
            return _patch_stream_refs(patched)
    raise AssertionError(f"no {marker!r} statement found in {relative_path}")


def select_body(relative_path: str, table_name: str) -> str:
    """For a file with no separate staging view (`silver_tsb.sql`): the `SELECT ...` body
    of `CREATE OR REFRESH STREAMING TABLE <table_name> (...) ... AS SELECT ... FROM
    STREAM(x)`, wrapped so it can be run as `SELECT * FROM (<this>)`. Sliced from the last
    standalone `AS` line before the final `SELECT` — the constraint list's own commas and
    parens never produce a line that is just `AS`, which is what makes this safe here.
    Matched by substring for the same reason as `staging_view_sql`.
    """
    marker_prefix = f"CREATE OR REFRESH STREAMING TABLE {table_name}"
    for stmt in statements(relative_path):
        idx = stmt.find(marker_prefix)
        # The table name must end here, not be a prefix of a longer identifier — the next
        # char is whitespace or `(` (a constraint list), never a name continuation.
        if idx == -1 or (
            idx + len(marker_prefix) < len(stmt) and stmt[idx + len(marker_prefix)] not in " \n\t("
        ):
            continue
        parts = stmt[idx:].rsplit("\nAS\n", 1)
        if len(parts) != 2:
            raise AssertionError(
                f"expected a standalone 'AS' line before the final SELECT in "
                f"{relative_path}'s {table_name!r} statement — got {len(parts)} part(s)"
            )
        return _patch_stream_refs(parts[1])
    raise AssertionError(f"no {marker_prefix!r} statement found in {relative_path}")
