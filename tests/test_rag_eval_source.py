"""Source-level guards on `src/search/28_rag_eval.py`.

The notebook cannot be imported — it needs `spark`, a `WorkspaceClient` and a live AI Search
index — so its logic cannot be unit-tested the way `retrieval_metrics.py` is. What *can* be
asserted is the shape of the code, and one property is worth spending a test file on:

**The topical relevance pool must come from the corpus, never from the retrieved hits.**

The first version derived it from the hits, which made Recall@10 exactly 1.0 whenever anything
on target came back (I-117, caught by external review). That defect is invisible in a results
table — the number looks excellent — and it is an attractive shortcut, because it saves a
query. So the guard belongs in CI rather than in a comment.

Same approach as `tests/test_provision_search.py`: read the file, assert what it does. Crude,
and the alternative here is no coverage at all.
"""

from __future__ import annotations

import re
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parents[1] / "src/search/28_rag_eval.py"


def _code() -> str:
    """The notebook's Python, with `# MAGIC` markdown cells stripped out.

    The prose explains the bug at length and names the pattern being avoided, so searching the
    raw file for it would match the explanation and pass regardless of the code.
    """
    return "\n".join(
        line
        for line in NOTEBOOK.read_text().splitlines()
        if not line.lstrip().startswith("# MAGIC")
    )


def test_the_topical_pool_is_built_by_a_query_not_from_the_hits() -> None:
    code = _code()
    assert "POOLS" in code, "the corpus-derived relevance pool is missing"
    assert "collect_set(c.complaint_id)" in code, "the pool query is missing"
    assert "POOLS.get(row.campaign_id" in code, "scoring must read the pool, not the hits"


def test_relevance_is_never_derived_from_the_retrieved_hits() -> None:
    """The exact regression. A comprehension over `hits` that lands in `relevant` is the
    shortcut that made the metric unfalsifiable."""
    code = _code()
    offending = re.search(r"relevant\s*=\s*\{[^}]*for\s+h\s+in\s+hits", code, re.S)
    assert offending is None, (
        "relevance is being derived from the retrieved hits again — Recall@k becomes 1.0 by "
        "construction. Build the pool from the corpus; see tests/test_retrieval_metrics.py::"
        "TestTheCircularRelevanceTrap."
    )


def test_the_pool_join_uses_the_projects_two_tier_model_match() -> None:
    """Not a fresh approximation of it. Four paths agreeing and a fifth not is how all 2,116
    F-250s came to be missing from the retrieval corpus (I-115); a sixth spelling of the
    comparison here would score that fix as a failure."""
    code = _code()
    assert "LIKE p.model || ' %'" in code
    assert "LIKE c.model || ' %'" in code


def test_the_pool_size_is_published_next_to_recall() -> None:
    """A pool of thousands makes Recall@10 tiny by arithmetic — ten slots cannot cover three
    thousand documents. Without the denominator beside it, a correct small number reads as
    broken retrieval."""
    code = _code()
    assert "relevant_pool_size" in code
    assert code.count("relevant_pool_size") >= 3, "computed, printed and persisted"


def test_a_probe_with_no_ground_truth_is_dropped_not_scored_zero() -> None:
    """A probe with an empty pool is a defective probe, not a retrieval failure. Folding it in
    as a zero would understate the system for a reason unrelated to retrieval — and the
    distinction only stays true if it is asserted."""
    code = _code()
    assert "dropped += 1" in code
    assert "dropped_probes" in code
