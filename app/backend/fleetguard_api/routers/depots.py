"""Depot-level risk — the fleet-wide view no single-campaign or single-work-order screen
gives you.

`fleetguard_depot` (60 rows) has existed since Phase 2 with nothing reading it except
`resolve_scope`'s depot-narrowing predicate. This router is its first real consumer: which
depots currently carry the most exposure, how urgent that exposure is, and how far behind
their proactive service work is.

Deliberately **no single blended "risk score."** A composite index with hidden weights is the
same mistake I-069 already caught once this project (a flat cost-per-vehicle multiplier that
looked data-driven but wasn't) — real component numbers, sorted and filtered by whichever one
matters to the viewer, tell the truth better than one invented number would. `urgent_vehicles`
(exposed to a Park It / Do Not Drive campaign) is the closest thing to a primary signal here,
and it's the same "consequence before volume" idea `queue.py` already ranks by — just rolled up
per depot instead of per campaign.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["depots"])


class DepotRisk(BaseModel):
    depot_id: str
    depot_name: str
    region: str
    city: str
    state: str
    fleet_size: int
    urgent_vehicles_exposed: int
    total_vehicles_exposed: int
    distinct_campaigns: int
    outstanding_work_orders: int
    overdue_work_orders: int


@router.get("/depot-risk", response_model=list[DepotRisk])
def depot_risk(principal: CurrentPrincipal) -> list[DepotRisk]:
    """Every depot, with its exposure and work-order backlog — always all 60, never scoped
    to one depot, since the point of this view is comparing depots against each other. A
    depot manager looking for their own row can filter client-side same as any other table."""
    if snapshot.is_snapshot():
        return []  # nothing has been loaded on a read-only surface, and that is the truth

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT d.depot_id, d.depot_name, d.region, d.city, d.state,
                       COUNT(DISTINCT v.vin) AS fleet_size
                FROM {PG_SCHEMA}.fleetguard_depot d
                LEFT JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.depot_id = d.depot_id
                GROUP BY d.depot_id, d.depot_name, d.region, d.city, d.state"""
        )
        depots = {r["depot_id"]: r for r in rows_to_dicts(cur)}

        cur.execute(
            f"""SELECT v.depot_id,
                       COUNT(DISTINCT e.vin) FILTER (WHERE c.park_it OR c.do_not_drive)
                           AS urgent_vehicles_exposed,
                       COUNT(DISTINCT e.vin) AS total_vehicles_exposed,
                       COUNT(DISTINCT e.campaign_id) AS distinct_campaigns
                FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
                JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
                JOIN {PG_SCHEMA}.fleetguard_recall_campaign c ON c.campaign_id = e.campaign_id
                GROUP BY v.depot_id"""
        )
        exposure = {r["depot_id"]: r for r in rows_to_dicts(cur)}

        cur.execute(
            f"""SELECT depot_id,
                       COUNT(*) FILTER (WHERE status IN ('OPEN', 'IN_PROGRESS'))
                           AS outstanding_work_orders,
                       COUNT(*) FILTER (
                           WHERE status NOT IN ('COMPLETED', 'CANCELLED')
                             AND due_date < CURRENT_DATE
                       ) AS overdue_work_orders
                FROM {PG_SCHEMA}.fleetguard_work_order
                GROUP BY depot_id"""
        )
        work = {r["depot_id"]: r for r in rows_to_dicts(cur)}

    results = []
    for depot_id, d in depots.items():
        e = exposure.get(depot_id, {})
        w = work.get(depot_id, {})
        results.append(
            DepotRisk(
                depot_id=depot_id,
                depot_name=d["depot_name"],
                region=d["region"],
                city=d["city"],
                state=d["state"],
                fleet_size=d["fleet_size"],
                urgent_vehicles_exposed=e.get("urgent_vehicles_exposed", 0),
                total_vehicles_exposed=e.get("total_vehicles_exposed", 0),
                distinct_campaigns=e.get("distinct_campaigns", 0),
                outstanding_work_orders=w.get("outstanding_work_orders", 0),
                overdue_work_orders=w.get("overdue_work_orders", 0),
            )
        )
    results.sort(key=lambda r: r.urgent_vehicles_exposed, reverse=True)
    return results
