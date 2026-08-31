# Pipeline expectations — what is enforced where

Three layers of checking, deliberately doing different jobs. The distinction matters
because the obvious one is the weakest.

## 1. Unit tests (`tests/`) — logic, off platform

`pytest`, no Databricks required. Covers the arithmetic that has already been wrong once:
VIN check digits, chunk-count boundaries, table-name safety. Run with `pytest`.

These encode **regressions**, not aspirations. `test_regression_i034_*` is the
single-character-chunk bug; `test_transliteration_is_many_to_one` documents a VIN checksum
property the suite itself discovered.

## 2. LDP expectations (`*.sql`) — row-level, in the pipeline

Declarative constraints on streaming tables. Two patterns are in use, and they are not
interchangeable:

| Pattern | Used for | Behaviour |
|---|---|---|
| `EXPECT (...)` with no action | Post-routing invariants | Records a metric. Should never fire — if it does, the *pipeline logic* is broken, not the data. |
| Explicit `_dq_failures` split | Real data defects | Row goes to `*_quarantine` with a reason. Nothing is dropped silently (§6). |

**Why not `ON VIOLATION DROP ROW` for defects?** Because it drops silently. The proposal
requires quarantine routing with reasons, so failure reasons are computed **once** in a
staging view and drive both outputs — the silver predicate and the quarantine predicate
cannot drift apart. An earlier version duplicated each predicate in two places and
dropped recall/investigation rows with no quarantine at all.

## 3. Data-quality assertions (`tests/test_data_quality.py`) — live tables

Cross-table invariants that only hold against real data, run with
`pytest -m integration --run-integration`. This is where **`_rescued_data` is explicitly
not trusted**: I-012 showed it stays 0 while the parse is silently wrong, so the
assertions check known column cardinalities instead.

## The rule this codebase follows

> A check that cannot fail is not a check.

Every assertion here has either failed at least once during the build, or exists because
something adjacent to it failed silently. Zero rescued rows, a clean pipeline run, and a
plausible row count were all true while the data was wrong.
