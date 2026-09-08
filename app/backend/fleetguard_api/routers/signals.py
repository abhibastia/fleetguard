"""Emerging defect signals — the proactive half of the console.

The queue answers *"what has NHTSA already recalled that we own?"*. This answers *"what is
going wrong that nobody has recalled yet?"* — which is the claim the whole project rests on,
and until now it existed only as a number on the evidence page.

Signals come from `gold_emerging_signal` via Lakebase (§4.4 operational). The detector is the
one the backtest measured: volume anomaly, `z >= 3.0`, sustained ≥2 months, at
`(make, model, component)` grain. **No harm weighting** (I-051) — `harm_share` is triage
context, not a detection input, and this module must never present it as one.

Ordering is fleet-relevance first, then recency. An operator's question is not "what is the
most anomalous series in America", it is "does any of this touch my trucks".
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["signals"])


class Signal(BaseModel):
    signal_id: str
    series_key: str | None
    make: str | None
    model: str | None
    component: str
    run_start: date | None
    run_end: date | None
    run_len: int | None
    max_z: float | None
    complaint_count: int | None
    harm_share: float | None
    fleet_vehicles: int | None
    is_live: bool | None
    status: str
    # 'DETECTOR' (batch z-score run) or 'AGENT' (opened by the assistant, see
    # agent_actions.py). The console badges these differently because a NULL max_z on an
    # agent row is an absence of measurement, not a quiet detector run.
    #
    # Defaulted so the committed snapshot.json — captured 2026-09-02, before this column
    # existed — still validates. That default is correct rather than merely convenient:
    # every row in that file came from the detector.
    source: str = "DETECTOR"
    # Who opened it. Meaningful only on AGENT rows: the agent has no database access, so the
    # console performs its write under the *caller's own* OBO token and records the human
    # here. That is the write path's central claim (ARCHITECTURE §7.1), and until now it was
    # true but invisible — recorded correctly, shown only in the Audit log a tab away.
    # A detector row has no opener and correctly stays NULL.
    #
    # Defaulted for the same snapshot reason as `source`, and correct for the same reason:
    # every row in that file is DETECTOR-sourced, so none of them has an opener.
    opened_by: str | None = None


class SignalSummary(BaseModel):
    total: int
    live: int
    fleet_relevant: int
    as_of_month: date | None
    signals: list[Signal]


@router.get("/signals", response_model=SignalSummary)
def get_signals(
    principal: CurrentPrincipal,
    fleet_only: bool = Query(False, description="only signals touching fleet vehicles"),
    limit: int = Query(50, ge=1, le=200),
) -> SignalSummary:
    """Detected defect ramps, fleet-relevant first.

    The counts are returned alongside the rows on purpose. "9 emerging across NHTSA, 2
    affecting you" is a materially different statement from either number alone, and a panel
    showing only the filtered list would make an empty fleet result look like a broken query.
    """
    if snapshot.is_snapshot():
        return SignalSummary(**snapshot.signals(fleet_only, limit))

    where = "WHERE fleet_vehicles > 0" if fleet_only else ""
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE is_live) AS live,
                       COUNT(*) FILTER (WHERE fleet_vehicles > 0) AS fleet_relevant,
                       MAX(as_of_month) AS as_of_month
                FROM {PG_SCHEMA}.fleetguard_defect_signal"""
        )
        summary = rows_to_dicts(cur)[0]

        cur.execute(
            f"""SELECT signal_id, series_key, make, model, component,
                       run_start, run_end, run_len, max_z, complaint_count,
                       harm_share, fleet_vehicles, is_live, status, source, opened_by
                FROM {PG_SCHEMA}.fleetguard_defect_signal
                {where}
                ORDER BY fleet_vehicles DESC NULLS LAST, run_end DESC NULLS LAST, max_z DESC
                LIMIT %(limit)s""",
            {"limit": limit},
        )
        rows = [Signal(**r) for r in rows_to_dicts(cur)]

    return SignalSummary(**summary, signals=rows)
