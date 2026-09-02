"""The measured backtest — the one route that is deliberately unauthenticated.

Everything else in this API answers under the caller's identity. This route answers to
anyone, because it serves a *published result* about NHTSA data: no fleet, no VINs, no
customer, nothing an identity could scope. It is the reason the public URL exists (§8a).

It reads a committed snapshot rather than querying Unity Catalog per request. That is not
laziness — a request-time read would require a Databricks credential on a public host, which
§8a forbids. `scripts/export_evidence.py` regenerates the snapshot from
`gold_lead_time_summary` with provenance attached; re-run it whenever the backtest is re-run.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter(tags=["evidence"])

SNAPSHOT = Path(__file__).resolve().parent.parent / "evidence.json"


class Arm(BaseModel):
    n: int
    detected: int
    rate_pct: float
    median_lead_days: float


class ModelB(BaseModel):
    model_version: int
    golden_set_size: int
    golden_set_positive: int
    threshold: float
    precision: float
    recall: float
    roc_auc: float
    test_set_size: int


class EvidenceOut(BaseModel):
    real: Arm
    placebo: Arm
    lift: float
    z: float
    p_value: float
    source_table: str
    statement: str
    model_b: ModelB
    generated_at: str


@router.get("/evidence", response_model=EvidenceOut)
def evidence() -> EvidenceOut:
    if not SNAPSHOT.exists():
        # Better a loud 503 than a page rendering zeros, which would read as "the method
        # found nothing" rather than "the numbers failed to load" (I-050's lesson, applied
        # to a page instead of a tool).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evidence snapshot missing; run scripts/export_evidence.py.",
        )
    return EvidenceOut(**json.loads(SNAPSHOT.read_text()))
