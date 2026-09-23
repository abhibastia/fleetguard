"""Pulls the portable SQL-building helpers out of `src/lakebase/21_cdf_to_gold_facts.py`.

Same reasoning as `sql_extract.py`: the notebook cannot run here (module-level `spark.sql`,
`dbutils`, Unity Catalog table names), but the two functions that decide *how a change
stream is interpreted* are pure string builders with no Spark dependency at all. Those are
where I-080 lived, and where a content-drift bug would live too. Exec'ing the real
definitions means the tests cannot drift from the committed notebook.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

NOTEBOOK = Path(__file__).resolve().parents[2] / "src" / "lakebase" / "21_cdf_to_gold_facts.py"

WANTED = ("latest_per_key", "changed_keys_in_slice")


_STAR_EXCEPT = re.compile(r"SELECT \* EXCEPT \(\s*\w+\s*\)")


def patch_for_local_spark(sql: str) -> str:
    """`SELECT * EXCEPT (rn)` -> `SELECT *`.

    `* EXCEPT (...)` is Databricks SQL; the OSS Spark 3.5 parser this package runs against
    rejects it outright. Dropping the exclusion leaves the helper's `rn` column in the
    result, which is harmless here: every assertion in these tests either reads columns by
    name or fingerprints an explicit column list, so an extra column changes nothing about
    what is being checked. The alternative — reimplementing the ranking locally — is
    exactly the hand-copied drift this module exists to avoid.
    """
    return _STAR_EXCEPT.sub("SELECT *", sql)


def helpers() -> dict[str, Any]:
    """`{name: function}` for each pure SQL-building helper in the notebook.

    Parsed with `ast` and exec'd one definition at a time, rather than exec'ing the whole
    file — the notebook's module level calls `spark.sql(f"USE {UC}")` on line one of its
    second cell, which would need a real workspace.
    """
    source = NOTEBOOK.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ns: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(NOTEBOOK), "exec"), ns)
    missing = [n for n in WANTED if n not in ns]
    if missing:
        raise AssertionError(f"{NOTEBOOK.name} no longer defines {missing}")

    def wrap(fn):
        def inner(*a, **k):
            return patch_for_local_spark(fn(*a, **k))

        return inner

    return {name: wrap(ns[name]) for name in WANTED}


def fingerprint_sql(truth_view: str, fact_view: str, columns: list[str]) -> str:
    """The reconciliation's content fingerprint, as the notebook computes it.

    Kept in step with the notebook by `test_fingerprint_sql_matches_the_notebook`, which
    asserts the notebook still uses `bit_xor(xxhash64(to_json(struct(...))))` — a rewrite
    to some other digest would otherwise leave these tests quietly checking nothing.
    """
    cols = ", ".join(f"`{c}`" for c in sorted(columns))
    return f"""
        SELECT
          (SELECT bit_xor(xxhash64(to_json(struct({cols})))) FROM {truth_view}) AS truth_fp,
          (SELECT bit_xor(xxhash64(to_json(struct({cols})))) FROM {fact_view})  AS fact_fp
    """
