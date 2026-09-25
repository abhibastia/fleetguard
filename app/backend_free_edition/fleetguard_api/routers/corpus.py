"""Corpus scale — the second deliberately-unauthenticated route.

**Why this exists.** Home told a visitor what FleetGuard does but never what it runs on, and
the lakehouse scale (8.4M bronze rows across four ODI datasets) is the most immediately legible
fact about the project. The obvious way to put it on the page is to type `2.24M` into the JSX.
That is exactly the pattern I-115 had just finished removing from `README.md` and
`Assistant.tsx`, where a rescope (I-111) left `1.75M` asserted on three user-facing surfaces
hours after it stopped being true. A hand-typed number on a landing page is a claim with no
owner and no way to notice it has gone stale.

So the figures are *derived* and *committed*, the same way the measured backtest is.

**Why a snapshot rather than a request-time query** — identical reasoning to `evidence.py`, and
it is a constraint rather than a preference: the public surface has no Databricks credential at
request time (§8a), so there is nothing to run `COUNT(*)` under. `scripts/export_corpus.py`
pulls the counts with a developer's own credentials and commits them with provenance.

**Why it is safe to serve without an identity.** Every count here is either public NHTSA
corpus scale, or a cardinality of the *synthetic* fleet registry (`gold_fleet_vehicle` is
20,000 generated VINs — see `src/fleet/04_build_fleet_registry.py`). There is no VIN, no depot
name, no campaign, and nothing an identity could scope. That is the same test `evidence.py`
applies, and it is applied here deliberately rather than inherited: `tests/test_corpus_route.py`
pins both that this route is public and that the fleet-detail routes are not.

**This is now the second public route, where there was one.** `tests/test_evidence_route.py`
previously asserted evidence was the *only* one. That assertion was doing real work — it is
what stops `/queue` being widened by accident — so it has been updated to name both rather
than loosened.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter(tags=["corpus"])

SNAPSHOT = Path(__file__).resolve().parent.parent / "corpus.json"


class CorpusOut(BaseModel):
    """Row counts, plus the provenance that makes them auditable.

    `rag_chunks` is the one figure here that is expected to MOVE: I-115 widened the AI Search
    source from exact make/model matching to the EXACT + MODEL_VARIANT tiers the rest of the
    system uses, which is a strict superset of the 115,499 measured for the old scope. Serving
    it from an export rather than a literal is the point — re-running the script in Run 2
    updates the page, instead of leaving another stale number to find later.
    """

    complaints: int
    tsbs: int
    recalls: int
    investigations: int
    fleet_vehicles: int
    fleet_depots: int
    rag_chunks: int
    bronze_total: int
    source_schema: str
    statement: str
    generated_at: str


@router.get("/corpus", response_model=CorpusOut)
def corpus() -> CorpusOut:
    if not SNAPSHOT.exists():
        # Loud 503 rather than zeros. A page rendering "0 complaints" reads as "this system
        # has no data" instead of "the numbers failed to load" — I-050's lesson, and the same
        # choice `evidence.py` makes one file over.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Corpus snapshot missing; run scripts/export_corpus.py.",
        )
    return CorpusOut(**json.loads(SNAPSHOT.read_text()))
