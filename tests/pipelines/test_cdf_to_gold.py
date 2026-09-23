"""The CDF→gold reconciliation: counting rows is not checking them.

The notebook's guard against a drifting watermark used to be `fact_rows == live_keys`.
These tests pin the case that check cannot see — **the right number of rows holding the
wrong values** — and the I-080 ranking rule it sits on top of.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cdf_extract import NOTEBOOK, fingerprint_sql, helpers

H = helpers()
latest_per_key = H["latest_per_key"]
changed_keys_in_slice = H["changed_keys_in_slice"]

KEY = "signal_id"
COLUMNS = ["signal_id", "status", "_pg_change_type", "_sort_by"]


def _event(sid: str, status: str, ct: str, sort_by: int) -> dict:
    return {"signal_id": sid, "status": status, "_pg_change_type": ct, "_sort_by": sort_by}


@pytest.fixture()
def history(register):
    """Two keys. `S1` was opened then completed; `S2` was opened and left alone."""
    register(
        "hist",
        [
            _event("S1", "OPEN", "insert", 10),
            _event("S1", "OPEN", "update_preimage", 20),
            _event("S1", "COMPLETED", "update_postimage", 21),
            _event("S2", "OPEN", "insert", 30),
        ],
    )
    return "hist"


# ------------------------------------------------------------------- the truth


def test_latest_per_key_takes_the_postimage(spark, history):
    rows = {r[KEY]: r["status"] for r in spark.sql(latest_per_key(history, KEY)).collect()}
    assert rows == {"S1": "COMPLETED", "S2": "OPEN"}


def test_deleted_keys_are_not_resurrected(spark, register):
    """I-080, pinned. Filtering deletes before ranking returns the key's own insert."""
    register("h2", [_event("S1", "OPEN", "insert", 10), _event("S1", "OPEN", "delete", 11)])
    assert spark.sql(latest_per_key("h2", KEY)).count() == 0


# ------------------------------------------- the case a row count cannot see


def test_stale_value_has_matching_counts_but_a_different_fingerprint(spark, history, register):
    """The whole point of this change.

    `gold` holds one row per live key — the same number the history says is live — but
    `S1`'s status is the superseded `OPEN`. Every cardinality check passes; the fact table
    is wrong.
    """
    register("gold_stale", [_event("S1", "OPEN", "insert", 10), _event("S2", "OPEN", "insert", 30)])
    spark.sql(latest_per_key(history, KEY)).createOrReplaceTempView("truth")

    assert spark.table("gold_stale").count() == spark.table("truth").count(), (
        "the fixture must have matching counts, or it is not testing what it claims to"
    )

    fp = spark.sql(fingerprint_sql("truth", "gold_stale", COLUMNS)).collect()[0]
    assert fp.truth_fp != fp.fact_fp, "content drift must be detectable when counts agree"


def test_a_correct_fact_table_fingerprints_equal(spark, history, register):
    register(
        "gold_ok",
        [_event("S1", "COMPLETED", "update_postimage", 21), _event("S2", "OPEN", "insert", 30)],
    )
    spark.sql(latest_per_key(history, KEY)).createOrReplaceTempView("truth")
    fp = spark.sql(fingerprint_sql("truth", "gold_ok", COLUMNS)).collect()[0]
    assert fp.truth_fp == fp.fact_fp


def test_fingerprint_is_independent_of_row_order(spark, history, register):
    """`bit_xor` is commutative; a rewrite to an order-sensitive digest would make this
    job fail at random depending on how Spark happened to return rows."""
    register(
        "gold_rev",
        [_event("S2", "OPEN", "insert", 30), _event("S1", "COMPLETED", "update_postimage", 21)],
    )
    spark.sql(latest_per_key(history, KEY)).createOrReplaceTempView("truth")
    fp = spark.sql(fingerprint_sql("truth", "gold_rev", COLUMNS)).collect()[0]
    assert fp.truth_fp == fp.fact_fp


def test_two_empty_tables_agree(spark, register):
    """`bit_xor` over no rows is NULL on both sides. NULL == NULL is not true in SQL, so
    this is compared in Python — where it is — and an empty pair must not read as drift."""
    register("h_empty", [_event("S1", "OPEN", "insert", 10), _event("S1", "OPEN", "delete", 11)])
    register("gold_empty_src", [_event("S1", "OPEN", "insert", 10)])
    spark.sql(latest_per_key("h_empty", KEY)).createOrReplaceTempView("truth")
    spark.sql("SELECT * FROM gold_empty_src WHERE 1=0").createOrReplaceTempView("gold_empty")
    fp = spark.sql(fingerprint_sql("truth", "gold_empty", COLUMNS)).collect()[0]
    assert fp.truth_fp is None and fp.fact_fp is None
    assert fp.truth_fp == fp.fact_fp


# ----------------------------------------------------------------- the slice


def test_slice_keeps_tombstones_for_the_merge(spark, register):
    """`changed_keys_in_slice` must surface a delete so the MERGE can apply it; filtering
    it here would leave the key in the fact forever."""
    register("h3", [_event("S1", "OPEN", "insert", 10), _event("S1", "OPEN", "delete", 40)])
    rows = spark.sql(changed_keys_in_slice("h3", KEY, 20)).collect()
    assert [r["_pg_change_type"] for r in rows] == ["delete"]


# ------------------------------------------------- the extractor's own guard


def test_fingerprint_sql_matches_the_notebook():
    """These tests exercise a transcription of the notebook's fingerprint expression. If
    the notebook changes how it digests a row, this fails rather than silently testing an
    expression the job no longer uses."""
    source = Path(NOTEBOOK).read_text(encoding="utf-8")
    assert "bit_xor(xxhash64(to_json(struct(" in source
    assert "fingerprints(fact, history, key)" in source
