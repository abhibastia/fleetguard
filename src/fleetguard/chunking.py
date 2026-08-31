"""Narrative chunking arithmetic for the AI Search embedding source.

Extracted because the first implementation was wrong in a way that ran clean: it computed
`ceil(len / stride)` instead of accounting for the window, so a 1,793-character narrative
produced a second chunk containing a **single character**. That inflated the table to
1.0269 chunks per complaint (59,485 spurious splits) and would have paid to embed ~58,000
junk vectors. See docs/ISSUES.md I-034.

The SQL in `src/pipelines/silver/silver_complaint_chunk.sql` implements the same formula;
these functions are the executable specification for it.
"""

from __future__ import annotations

# 512 tokens at the usual ~4 chars/token heuristic.
DEFAULT_WINDOW = 2048
# 256-character overlap, so a defect description split at a seam still retrieves.
DEFAULT_STRIDE = 1792
# Below this a narrative carries no retrievable signal but still costs tokens to embed.
MIN_NARRATIVE_CHARS = 20


def chunk_count(length: int, window: int = DEFAULT_WINDOW, stride: int = DEFAULT_STRIDE) -> int:
    """Number of chunks a text of `length` characters produces.

    The count is driven by the WINDOW, not the stride:

        chunks = max(1, ceil((length - window) / stride) + 1)

    Any text that fits inside one window is exactly one chunk, however close to the
    boundary it sits. `ceil(length / stride)` is the wrong formula and is what I-034 was.
    """
    if length <= 0:
        return 0
    if length <= window:
        return 1
    # ceil division without floating point, so no rounding surprises at the boundary
    return -(-(length - window) // stride) + 1


def chunk_spans(
    length: int, window: int = DEFAULT_WINDOW, stride: int = DEFAULT_STRIDE
) -> list[tuple[int, int]]:
    """Return `(start, end)` character offsets for each chunk, 0-based and end-exclusive.

    The final chunk is clamped to `length`, so no span runs past the end of the text.
    """
    n = chunk_count(length, window, stride)
    return [(i * stride, min(i * stride + window, length)) for i in range(n)]


def is_embeddable(narrative: str | None, min_chars: int = MIN_NARRATIVE_CHARS) -> bool:
    """Whether a narrative is worth embedding at all.

    The corpus contains narratives as short as one character. They are still billable.
    """
    return narrative is not None and len(narrative.strip()) >= min_chars
