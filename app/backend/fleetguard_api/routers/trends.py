"""Historical trend — the one dimension nothing in the console has shown yet.

Every other view answers "what's true right now." This answers "is it getting better or
worse" — how many recall campaigns have hit the fleet each year since 2014, and how many
vehicles they touched, using `fleetguard_recall_campaign.issued_at` (the real NHTSA filing
date, not a demo timestamp) joined to the fleet's own real exposure match. No SQL Warehouse
call needed: `issued_at` and the exposure join are both already in Lakebase, loaded from gold
in Phase 2.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["trends"])


class RecallTrendPoint(BaseModel):
    year: int
    campaigns: int
    urgent_campaigns: int
    vehicles_exposed: int


class RecallTrend(BaseModel):
    points: list[RecallTrendPoint]
    # The most recent real filing date behind the data — the client uses this to flag the
    # current year's bar as partial rather than let a short bar for an in-progress year read
    # as a decline. Not derived from "today", since the corpus's own freshness may lag it.
    latest_issued_at: date | None


@router.get("/recall-trend", response_model=RecallTrend)
def recall_trend(principal: CurrentPrincipal) -> RecallTrend:
    """One row per year that has at least one fleet-relevant recall campaign. Years with zero
    are simply absent rather than zero-filled — the chart renders whatever years exist, and a
    caller wanting continuous years can fill gaps itself rather than this endpoint asserting
    a "0" for a year it never actually queried a full calendar range for."""
    if snapshot.is_snapshot():
        return RecallTrend(points=[], latest_issued_at=None)

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT EXTRACT(YEAR FROM c.issued_at)::int AS year,
                       COUNT(DISTINCT c.campaign_id) AS campaigns,
                       COUNT(DISTINCT c.campaign_id) FILTER (WHERE c.park_it OR c.do_not_drive)
                           AS urgent_campaigns,
                       COUNT(DISTINCT e.vin) AS vehicles_exposed
                FROM {PG_SCHEMA}.fleetguard_recall_campaign c
                JOIN {PG_SCHEMA}.fleetguard_vehicle_exposure e ON e.campaign_id = c.campaign_id
                WHERE c.issued_at IS NOT NULL
                GROUP BY year
                ORDER BY year"""
        )
        points = [RecallTrendPoint(**r) for r in rows_to_dicts(cur)]

        cur.execute(
            f"SELECT MAX(issued_at)::date FROM {PG_SCHEMA}.fleetguard_recall_campaign"
            f" WHERE issued_at IS NOT NULL"
        )
        latest_issued_at = cur.fetchone()[0]

    return RecallTrend(points=points, latest_issued_at=latest_issued_at)
