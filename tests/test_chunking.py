"""Unit tests for chunk arithmetic.

The centrepiece is `test_regression_i034_*`: the shipped-and-caught bug where chunk count
was driven by the stride instead of the window, producing single-character chunks. It ran
clean and produced a plausible table, which is exactly why it needs a regression test.
"""

import pytest

from fleetguard.chunking import (
    DEFAULT_STRIDE,
    DEFAULT_WINDOW,
    chunk_count,
    chunk_spans,
    is_embeddable,
)


class TestRegressionI034:
    """A text that fits in one window is one chunk, however close to the boundary."""

    @pytest.mark.parametrize("length", [1, 20, 500, 1791, 1792, 1793, 2047, 2048])
    def test_anything_within_one_window_is_a_single_chunk(self, length):
        assert chunk_count(length) == 1

    def test_the_exact_bug_1793_chars(self):
        """ceil(1793/1792) = 2 — the wrong answer, and the one that shipped.

        The second chunk started at offset 1792 and contained one character.
        """
        assert chunk_count(1793) == 1
        wrong = -(-1793 // DEFAULT_STRIDE)  # the old formula
        assert wrong == 2, "sanity: this is what the buggy formula produced"

    def test_no_chunk_is_shorter_than_the_stride_except_the_last(self):
        """A one-character trailing chunk is the visible symptom of the I-034 bug."""
        for length in range(1, 6000, 37):
            spans = chunk_spans(length)
            for start, end in spans[:-1]:
                assert end - start == DEFAULT_WINDOW
            if spans:
                assert spans[-1][1] == length


class TestChunkCount:
    def test_zero_length_produces_no_chunks(self):
        assert chunk_count(0) == 0
        assert chunk_count(-5) == 0

    @pytest.mark.parametrize(
        ("length", "expected"),
        [
            (2049, 2),  # one character past the window
            (2048 + 1792, 2),  # exactly two windows' worth of stride
            (2048 + 1792 + 1, 3),
            (2048 + 2 * 1792, 3),
        ],
    )
    def test_boundaries(self, length, expected):
        assert chunk_count(length) == expected

    def test_matches_the_measured_corpus(self):
        """CDESCR is CHAR(2048), so the max observed narrative was 2,132 characters.

        That is why the corpus yields ~1.0006 chunks per complaint — chunking is nearly
        a no-op on complaints, and the splitter earns its place on longer sources.
        """
        assert chunk_count(517) == 1  # mean narrative
        assert chunk_count(1477) == 1  # p95
        assert chunk_count(2132) == 2  # max observed — the only real split
        assert chunk_count(5940) == 4  # longest investigation summary

    def test_count_is_monotonic_in_length(self):
        counts = [chunk_count(n) for n in range(1, 8000, 13)]
        assert counts == sorted(counts)


class TestChunkSpans:
    def test_span_count_matches_chunk_count(self):
        for length in (1, 100, 2048, 2049, 5000, 12000):
            assert len(chunk_spans(length)) == chunk_count(length)

    def test_spans_never_exceed_the_text(self):
        for length in (1, 2049, 5000):
            assert all(end <= length for _, end in chunk_spans(length))

    def test_consecutive_spans_overlap_by_the_configured_amount(self):
        spans = chunk_spans(10_000)
        overlap = DEFAULT_WINDOW - DEFAULT_STRIDE
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:], strict=False):
            assert e1 - s2 == overlap, "overlap protects text split across a seam"

    def test_full_text_is_covered(self):
        length = 7000
        covered = set()
        for start, end in chunk_spans(length):
            covered.update(range(start, end))
        assert covered == set(range(length))


class TestIsEmbeddable:
    @pytest.mark.parametrize("value", [None, "", "   ", "too short", "x" * 19])
    def test_rejects_text_with_no_retrievable_signal(self, value):
        assert not is_embeddable(value)

    @pytest.mark.parametrize("value", ["x" * 20, "brake pedal went to the floor"])
    def test_accepts_real_narratives(self, value):
        assert is_embeddable(value)

    def test_whitespace_does_not_count_toward_the_minimum(self):
        assert not is_embeddable("  " + "x" * 10 + "        ")
