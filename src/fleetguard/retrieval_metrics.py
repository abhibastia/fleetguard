"""Retrieval evaluation arithmetic — the executable specification for `src/search/28_rag_eval.py`.

**Why this is a module and not fifteen lines inside the notebook.** The notebook cannot run
without a live AI Search index, which this project only has during a costed online window. If
the scoring logic lived there, every bug in it would surface with the meter running, in the
same hour the numbers are needed. Here it is ordinary Python with ordinary tests, and the
notebook's job is reduced to *fetching* results and calling these.

**The measurement.** For each probe: a question built from a real recall campaign, and the set
of complaint chunks that count as relevant to it. The retrieved list is ranked, so the metrics
that matter are rank-aware — Recall@k says whether the right thing was found at all, MRR says
how far down, and Precision@k says how much noise came with it. Publishing all three is
deliberate: any one of them alone can be flattered by a degenerate retriever.

**Relevance is defined here, and it is the part most likely to be wrong.** A chunk counts as
relevant when its component matches the campaign's *and* its vehicle matches, where "matches"
uses the same two-tier rule (`EXACT`, then `MODEL_VARIANT`) as the gold layer (I-030), the
agent write path (I-075), the emerging detector (I-079) and — since I-115 — the index source
itself. Four paths agreeing and the fifth not is exactly how all 2,116 of the fleet's F-250s
came to be missing from the retrieval corpus, so the scorer uses the same rule rather than a
sixth approximation of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

EXACT = "EXACT"
MODEL_VARIANT = "MODEL_VARIANT"
NONE = "NONE"


def model_match_basis(fleet_model: str | None, other_model: str | None) -> str:
    """`EXACT`, `MODEL_VARIANT` or `NONE` for one pair of model strings.

    The variant rule is the Python statement of the SQL in `10_emerging_signals.py:157-160`:
    one name is the other followed by a space and further words. NHTSA writes `F-250 SD`,
    vPIC writes `F-250`; `PROMASTER` matches `PROMASTER CITY`, which is an **over-match** and
    is documented as such — the tier is reported alongside every count so a consumer can see
    which rule answered, rather than having the two silently blended.

    Both directions are checked because neither corpus is consistently the longer one.
    """
    a = (fleet_model or "").strip().upper()
    b = (other_model or "").strip().upper()
    if not a or not b:
        return NONE
    if a == b:
        return EXACT
    if b.startswith(a + " ") or a.startswith(b + " "):
        return MODEL_VARIANT
    return NONE


def is_relevant(
    chunk: dict,
    *,
    make: str,
    model: str,
    component: str,
    allow_variant: bool = True,
) -> bool:
    """Does one retrieved chunk count as a hit for this probe?

    Component comparison is a containment test in both directions, not equality: `COMPDESC`
    is already a structured field but its values nest (`SERVICE BRAKES, HYDRAULIC` sits under
    `SERVICE BRAKES`), so demanding equality would score a correct retrieval as a miss.
    """
    if (chunk.get("make") or "").strip().upper() != (make or "").strip().upper():
        return False

    basis = model_match_basis(model, chunk.get("model"))
    if basis == NONE or (basis == MODEL_VARIANT and not allow_variant):
        return False

    want = (component or "").strip().upper()
    got = (chunk.get("component") or "").strip().upper()
    if not want or not got:
        return False
    return want in got or got in want


def _ranked_unique(retrieved: Iterable[str]) -> list[str]:
    """Rank order, first occurrence wins.

    Duplicates must not be counted twice. The index is chunk-grained and one complaint can
    produce several chunks, so a retriever returning three chunks of the same narrative has
    found **one** thing, not three — the same conflation `search_complaints` dedupes for
    (I-023), and scoring it as three would make a worse retriever look better.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in retrieved:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the relevant set found in the top k. `0.0` when nothing is relevant.

    Returning 0.0 rather than raising for an empty relevant set is a choice: a probe with no
    ground truth is a defective *probe*, and it should be filtered out when the set is built,
    not crash a scoring run twenty minutes into a billed window.
    """
    truth = set(relevant)
    if not truth or k <= 0:
        return 0.0
    top = _ranked_unique(retrieved)[:k]
    return len(truth.intersection(top)) / len(truth)


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the top k that is relevant.

    Denominator is `k`, not `len(top)`. A retriever that returns two results where ten were
    asked for has *not* earned precision 1.0 for getting both right — under-returning is a
    failure, and dividing by the shorter list would hide it.
    """
    truth = set(relevant)
    if k <= 0:
        return 0.0
    top = _ranked_unique(retrieved)[:k]
    return len(truth.intersection(top)) / k


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    """`1/rank` of the first relevant result, `0.0` if none appears."""
    truth = set(relevant)
    for position, item in enumerate(_ranked_unique(retrieved), start=1):
        if item in truth:
            return 1.0 / position
    return 0.0


def mean(values: Sequence[float]) -> float:
    """Mean over probes, `0.0` for none — so an empty sweep reports zero rather than raising
    in the middle of a costed window. The probe count is published alongside every figure, so
    a zero from no data is never mistakable for a zero from bad retrieval."""
    return sum(values) / len(values) if values else 0.0


def summarise(per_probe: Sequence[dict], k: int) -> dict:
    """Roll per-probe scores into the row written to `ops_rag_eval`.

    `probes_with_a_hit` is reported separately from `recall_at_k` on purpose. A mean recall of
    0.30 is ambiguous — it is consistent with every probe half-answered, and with a third of
    them answered perfectly while the rest returned nothing. Those are different systems and
    the distinction is the useful part of the result.
    """
    recalls = [p["recall_at_k"] for p in per_probe]
    return {
        "k": k,
        "probes": len(per_probe),
        "recall_at_k": mean(recalls),
        "precision_at_k": mean([p["precision_at_k"] for p in per_probe]),
        "mrr": mean([p["reciprocal_rank"] for p in per_probe]),
        "probes_with_a_hit": sum(1 for r in recalls if r > 0),
    }
