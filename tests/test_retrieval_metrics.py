"""Scoring arithmetic for the RAG evaluation.

These run offline because the notebook that uses them cannot: it needs a live AI Search index,
which exists only inside a costed window. Every bug caught here is one that would otherwise
have surfaced with the meter running, in the hour the numbers were needed.

The cases below are the ones that flatter a broken retriever if they are wrong — duplicate
chunks from one complaint, `k` larger than the result list, an empty relevant set, and the
F-250 variant spelling that has now caused the same bug in four separate places.
"""

from __future__ import annotations

from fleetguard.retrieval_metrics import (
    EXACT,
    MODEL_VARIANT,
    NONE,
    is_relevant,
    mean,
    model_match_basis,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarise,
)

# --- the variant rule: the bug that has recurred four times ------------------------------


def test_the_f250_case_by_name() -> None:
    """I-030, I-075, I-079 and finally I-115 were all this one comparison. NHTSA writes
    `F-250 SD`, vPIC writes `F-250`, and every path that compared them with `=` silently
    excluded all 2,116 of the fleet's F-250s."""
    assert model_match_basis("F-250", "F-250 SD") == MODEL_VARIANT
    assert model_match_basis("F-250 SD", "F-250") == MODEL_VARIANT


def test_exact_beats_variant() -> None:
    assert model_match_basis("SILVERADO", "SILVERADO") == EXACT
    assert model_match_basis(" silverado ", "SILVERADO") == EXACT


def test_a_prefix_without_a_word_boundary_is_not_a_match() -> None:
    """`F-25` must not match `F-250`, and `PROMASTER` must not match `PROMASTERX`. The rule is
    "the same name plus further words", not "starts with"."""
    assert model_match_basis("F-25", "F-250") == NONE
    assert model_match_basis("PROMASTER", "PROMASTERCITY") == NONE


def test_the_documented_over_match_is_preserved_not_fixed() -> None:
    """`PROMASTER` -> `PROMASTER CITY` is a real over-match and the project documents it as
    one. It is kept because the tier travels with the count, so a consumer can see which rule
    answered. Narrowing it here would make this scorer disagree with the four paths it exists
    to match."""
    assert model_match_basis("PROMASTER", "PROMASTER CITY") == MODEL_VARIANT


def test_empty_model_is_never_a_match() -> None:
    assert model_match_basis("", "F-250") == NONE
    assert model_match_basis("F-250", None) == NONE


# --- relevance ----------------------------------------------------------------------------


def _chunk(**kw) -> dict:
    base = {"make": "FORD", "model": "F-250 SD", "component": "SERVICE BRAKES, HYDRAULIC"}
    base.update(kw)
    return base


def test_a_variant_spelled_chunk_is_relevant() -> None:
    assert is_relevant(_chunk(), make="FORD", model="F-250", component="SERVICE BRAKES")


def test_variants_can_be_excluded_to_measure_the_exact_tier_alone() -> None:
    assert not is_relevant(
        _chunk(), make="FORD", model="F-250", component="SERVICE BRAKES", allow_variant=False
    )


def test_the_wrong_make_is_never_relevant() -> None:
    """Make is compared exactly, deliberately: `VOLVO` covers both Class 8 tractors and
    passenger cars in this data, so loosening it would pull in a different vehicle entirely."""
    assert not is_relevant(_chunk(make="RAM"), make="FORD", model="F-250", component="SERVICE BRAKES")


def test_component_nesting_counts_in_both_directions() -> None:
    """`COMPDESC` values nest — demanding equality would score a correct retrieval as a miss."""
    assert is_relevant(_chunk(), make="FORD", model="F-250 SD", component="SERVICE BRAKES")
    assert is_relevant(
        _chunk(component="SERVICE BRAKES"),
        make="FORD",
        model="F-250 SD",
        component="SERVICE BRAKES, HYDRAULIC",
    )


def test_an_unrelated_component_is_not_relevant() -> None:
    assert not is_relevant(
        _chunk(component="ELECTRICAL SYSTEM"),
        make="FORD",
        model="F-250 SD",
        component="SERVICE BRAKES",
    )


def test_a_missing_component_is_not_silently_relevant() -> None:
    assert not is_relevant(_chunk(component=None), make="FORD", model="F-250 SD", component="X")


# --- the metrics --------------------------------------------------------------------------


def test_recall_counts_the_relevant_set_not_the_results() -> None:
    assert recall_at_k(["a", "b", "c"], {"a", "z"}, 3) == 0.5


def test_k_truncates_the_ranked_list() -> None:
    assert recall_at_k(["x", "y", "a"], {"a"}, 2) == 0.0
    assert recall_at_k(["x", "y", "a"], {"a"}, 3) == 1.0


def test_k_larger_than_the_result_list_is_not_an_error() -> None:
    assert recall_at_k(["a"], {"a"}, 10) == 1.0


def test_duplicate_chunks_from_one_complaint_count_once() -> None:
    """The index is chunk-grained and one narrative can produce several chunks. Three chunks of
    the same complaint is ONE thing found, not three — the conflation `search_complaints`
    dedupes for (I-023). Scoring it as three makes a worse retriever look better."""
    assert precision_at_k(["a", "a", "a", "b"], {"a"}, 4) == 0.25
    assert reciprocal_rank(["z", "a", "a"], {"a"}) == 0.5


def test_precision_divides_by_k_not_by_what_came_back() -> None:
    """A retriever asked for 10 that returns 2 correct results has not earned precision 1.0.
    Under-returning is a failure and dividing by the shorter list would hide it."""
    assert precision_at_k(["a", "b"], {"a", "b"}, 10) == 0.2


def test_reciprocal_rank_is_the_first_hit() -> None:
    assert reciprocal_rank(["x", "y", "a"], {"a", "y"}) == 0.5
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_an_empty_relevant_set_scores_zero_rather_than_raising() -> None:
    """A probe with no ground truth is a defective probe. It should be filtered out when the
    set is built — not crash a scoring run inside a billed window."""
    assert recall_at_k(["a"], [], 5) == 0.0
    assert reciprocal_rank([], []) == 0.0
    assert mean([]) == 0.0


def test_summarise_separates_mean_recall_from_how_many_probes_scored() -> None:
    """The distinction the mean alone destroys: 0.5 mean recall is consistent with every probe
    half-answered AND with half the probes answered perfectly and the rest returning nothing.
    Those are different systems."""
    per_probe = [
        {"recall_at_k": 1.0, "precision_at_k": 0.2, "reciprocal_rank": 1.0},
        {"recall_at_k": 0.0, "precision_at_k": 0.0, "reciprocal_rank": 0.0},
    ]
    out = summarise(per_probe, k=5)
    assert out == {
        "k": 5,
        "probes": 2,
        "recall_at_k": 0.5,
        "precision_at_k": 0.1,
        "mrr": 0.5,
        "probes_with_a_hit": 1,
    }
