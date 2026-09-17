"""`silver_complaint_chunk` (src/pipelines/silver/silver_complaint_chunk.sql) — the chunk-
count math this file's own comment documents as previously wrong: `ceil(len/stride)` (driven
by the 1792-char stride) inflated the table to 1.0269 chunks/complaint and produced spurious
1-character chunks at certain lengths; the fix is `max(1, ceil((len-window)/stride) + 1)`,
driven by the 2048-char window. These tests pin the corrected formula against the exact
narrative lengths where the two formulas disagree, plus the short-narrative exclusion filter.
"""

from __future__ import annotations

from sql_extract import select_body

RELATIVE_PATH = "silver/silver_complaint_chunk.sql"
WINDOW = 2048
STRIDE = 1792


def _silver_complaint_row(narrative: str, **over) -> dict:
    row = {
        "complaint_id": "C1",
        "odi_number": "O1",
        "product_type": "V",
        "make": "RAM",
        "model": "2500",
        "model_year": 2024,
        "component": "BRAKES",
        "received_date": "2026-01-01T00:00:00",
        "incident_date": "2025-12-15T00:00:00",
        "crash": False,
        "fire": False,
        "injured": 0,
        "deaths": 0,
        "medical_attention": False,
        "police_report": False,
        "narrative": narrative,
        "_ingested_at": "2026-01-01T00:00:00",
    }
    row.update(over)
    return row


def _run(spark, register, rows):
    register("silver_complaint", rows)
    spark.sql(
        f"SELECT * FROM ({select_body(RELATIVE_PATH, 'silver_complaint_chunk')})"
    ).createOrReplaceTempView("chunk_result")
    result = sorted(
        spark.sql("SELECT * FROM chunk_result").collect(), key=lambda r: r["chunk_index"]
    )
    spark.catalog.dropTempView("chunk_result")
    return result


def test_narrative_shorter_than_the_window_is_exactly_one_chunk(spark, register):
    rows = _run(spark, register, [_silver_complaint_row("x" * 500)])
    assert len(rows) == 1
    assert rows[0]["chunk_total"] == 1
    assert rows[0]["chunk_index"] == 0
    assert rows[0]["chunk_text"] == "x" * 500


def test_narrative_exactly_one_window_long_is_still_one_chunk(spark, register):
    """The boundary the window-driven formula must get right: len == WINDOW exactly."""
    rows = _run(spark, register, [_silver_complaint_row("x" * WINDOW)])
    assert len(rows) == 1
    assert rows[0]["chunk_total"] == 1


def test_narrative_one_char_over_the_window_is_two_chunks_not_a_spurious_split(spark, register):
    """The documented bug: the old stride-driven formula produced a second chunk holding a
    single character at lengths just over the window. The corrected formula's second chunk
    here should hold the real remainder (narrative_len - stride chars), not 1 char."""
    narrative = "x" * (WINDOW + 1)
    rows = _run(spark, register, [_silver_complaint_row(narrative)])

    assert len(rows) == 2
    assert {r["chunk_total"] for r in rows} == {2}
    assert rows[0]["chunk_index"] == 0
    assert rows[1]["chunk_index"] == 1
    # Second chunk starts at offset chunk_index * STRIDE + 1 = 1793, so it holds
    # len(narrative) - STRIDE characters, not a lone leftover character.
    assert len(rows[1]["chunk_text"]) == len(narrative) - STRIDE
    assert len(rows[1]["chunk_text"]) > 1


def test_chunk_ids_are_complaint_id_dash_index(spark, register):
    rows = _run(spark, register, [_silver_complaint_row("x" * (WINDOW + 1), complaint_id="C7")])
    assert {r["chunk_id"] for r in rows} == {"C7-0", "C7-1"}


def test_narrative_under_twenty_characters_is_excluded_entirely(spark, register):
    rows = _run(spark, register, [_silver_complaint_row("short")])
    assert rows == []


def test_narrative_at_exactly_twenty_characters_is_included(spark, register):
    rows = _run(spark, register, [_silver_complaint_row("x" * 20)])
    assert len(rows) == 1


def test_any_harm_is_true_when_injured_count_is_positive(spark, register):
    (row,) = _run(
        spark, register, [_silver_complaint_row("x" * 500, crash=False, fire=False, injured=1)]
    )
    assert row["any_harm"] is True


def test_any_harm_is_false_when_nothing_indicates_harm(spark, register):
    (row,) = _run(
        spark,
        register,
        [_silver_complaint_row("x" * 500, crash=False, fire=False, injured=0, deaths=0)],
    )
    assert row["any_harm"] is False
