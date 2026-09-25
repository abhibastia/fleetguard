"""The live recall API's own state — the reactive half, finally visible.

`api.nhtsa.gov/recalls/recallsByVehicle` has been polled since Phase 1 and consumed by
nothing: until 2026-09-20 neither `bronze_recall_api` nor `gold_recall_alert` was read by any
router, view, dashboard widget, agent tool or test. The polling notebook called the alert
table "the reactive product surface" and the only humans who ever saw it were whoever opened
that job's run output.

This route makes it a surface. Two things, deliberately in one response because they answer
one question — *is the live recall feed working, and has it found anything the daily flat
file has not?*

- **coverage / health** (`fleetguard_recall_api_poll`, one row per fleet combo) — always
  populated, so the panel has something to say even when there is no news.
- **alerts** (`fleetguard_recall_api_alert`) — campaigns the API knows about and the flat
  file does not, with fleet exposure attached.

**Zero alerts is a real answer, not an empty state.** For weeks every campaign the API
returned was already in the flat file. The client must say that in words; `summary.alerts`
being 0 alongside a healthy `success_rate_pct` means "the feed is working and there is no
news", which is different from "the feed is broken". I-032 is why this distinction is worth
the care: the first version of that derivation reported 9 novel campaigns that were all
already known.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["recall-api"])


class PollCombo(BaseModel):
    combo_key: str
    make: str
    model: str
    model_year: int
    fleet_vehicles: int
    last_polled_at: datetime | None
    last_status: str | None
    last_campaign_count: int | None


class RecallApiAlert(BaseModel):
    alert_key: str
    campaign_number: str
    make: str | None
    model: str | None
    model_year: int | None
    component: str | None
    park_it: bool
    park_outside: bool
    consequence: str | None
    vehicles_exposed: int
    depots_affected: int


class RecallApiSummary(BaseModel):
    combos: int
    combos_ok: int
    # NULL rather than 0 when nothing has been polled: "we have not run" and "everything
    # failed" must not render the same. Same rule as gold_agent_activity_daily's success rate.
    success_rate_pct: float | None
    fleet_vehicles_covered: int
    last_polled_at: datetime | None
    alerts: int
    alert_campaigns: int
    vehicles_exposed_by_alerts: int


class RecallApiStatus(BaseModel):
    summary: RecallApiSummary
    poll: list[PollCombo]
    alerts: list[RecallApiAlert]


_EMPTY = RecallApiStatus(
    summary=RecallApiSummary(
        combos=0,
        combos_ok=0,
        success_rate_pct=None,
        fleet_vehicles_covered=0,
        last_polled_at=None,
        alerts=0,
        alert_campaigns=0,
        vehicles_exposed_by_alerts=0,
    ),
    poll=[],
    alerts=[],
)


@router.get("/recall-api-status", response_model=RecallApiStatus)
def recall_api_status(
    principal: CurrentPrincipal,
    limit: int = Query(500, ge=1, le=500, description="max poll combos to return"),
) -> RecallApiStatus:
    """Coverage, health and novel-campaign alerts for the live recall feed."""
    if snapshot.is_snapshot():
        return _EMPTY

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*)                                          AS combos,
                   COUNT(*) FILTER (WHERE last_status = 'ok')        AS combos_ok,
                   COALESCE(SUM(fleet_vehicles), 0)                  AS fleet_vehicles_covered,
                   MAX(last_polled_at)                               AS last_polled_at
            FROM {PG_SCHEMA}.fleetguard_recall_api_poll
            """
        )
        head = rows_to_dicts(cur)[0]

        cur.execute(
            f"""
            SELECT COUNT(*)                          AS alerts,
                   COUNT(DISTINCT campaign_number)   AS alert_campaigns,
                   COALESCE(SUM(vehicles_exposed),0) AS vehicles_exposed_by_alerts
            FROM {PG_SCHEMA}.fleetguard_recall_api_alert
            """
        )
        atotals = rows_to_dicts(cur)[0]

        cur.execute(
            f"""
            SELECT combo_key, make, model, model_year, fleet_vehicles,
                   last_polled_at, last_status, last_campaign_count
            FROM {PG_SCHEMA}.fleetguard_recall_api_poll
            ORDER BY fleet_vehicles DESC, make, model, model_year
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        poll = [PollCombo(**r) for r in rows_to_dicts(cur)]

        cur.execute(
            f"""
            SELECT alert_key, campaign_number, make, model, model_year, component,
                   park_it, park_outside, consequence, vehicles_exposed, depots_affected
            FROM {PG_SCHEMA}.fleetguard_recall_api_alert
            ORDER BY vehicles_exposed DESC, campaign_number
            """
        )
        alerts = [RecallApiAlert(**r) for r in rows_to_dicts(cur)]

    combos = head["combos"] or 0
    return RecallApiStatus(
        summary=RecallApiSummary(
            combos=combos,
            combos_ok=head["combos_ok"] or 0,
            success_rate_pct=(
                round(100.0 * (head["combos_ok"] or 0) / combos, 1) if combos else None
            ),
            fleet_vehicles_covered=head["fleet_vehicles_covered"] or 0,
            last_polled_at=head["last_polled_at"],
            alerts=atotals["alerts"] or 0,
            alert_campaigns=atotals["alert_campaigns"] or 0,
            vehicles_exposed_by_alerts=atotals["vehicles_exposed_by_alerts"] or 0,
        ),
        poll=poll,
        alerts=alerts,
    )
