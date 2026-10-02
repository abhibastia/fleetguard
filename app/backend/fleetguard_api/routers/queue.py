"""Work queue and campaign detail — the operator's primary surface.

All reads here are **transactional**: point lookups and small aggregations over
`fleetguard_vehicle_exposure` (118,323 rows) serving one operator's session. They belong in
Lakebase, not on a SQL warehouse (§4.4 operational / §4.6 analytics).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal
from ..scoping import mask_vin, resolve_scope

router = APIRouter(tags=["queue"])


class QueueItem(BaseModel):
    campaign_id: str
    component: str | None
    park_it: bool
    do_not_drive: bool
    vehicles_exposed: int
    depots_affected: int
    consequence: str | None
    # Non-null only when a LAUNCHED service campaign already exists for this recall (the same
    # `status = 'LAUNCHED'` scope the uniqueness index enforces, see I-063) — without this the
    # queue looked identical whether a campaign had already been approved or not, and the only
    # way to find out was clicking Approve and hitting the resulting 409.
    service_campaign_id: str | None = None


class QueueSummary(BaseModel):
    """Totals for the queue's stat cards — **not** derivable from the page the cards sit above.

    This exists because deriving them client-side was wrong in both directions (I-130).
    `Campaigns` was `items.length`, which is the *fetch limit* (50) rather than a count, and
    `Vehicles exposed` summed each campaign's `vehicles_exposed`, counting a VIN once per
    campaign it appears in. Measured against live Lakebase: 393 campaigns and 11,323 distinct
    VINs, displayed as 50 and 51,615.
    """

    campaigns: int
    vehicles_exposed: int
    urgent_campaigns: int


class ExposedVehicle(BaseModel):
    vin: str | None
    depot_id: str
    make: str
    model: str
    model_year: int | None


class CampaignDetail(BaseModel):
    campaign_id: str
    component: str | None
    park_it: bool
    consequence: str | None
    remedy: str | None
    vehicles_exposed: int
    by_depot: dict[str, int]
    sample_vehicles: list[ExposedVehicle]
    # Same meaning as QueueItem.service_campaign_id — present so the detail page can point at
    # the existing launch instead of showing an Approve form that can only 409.
    service_campaign_id: str | None = None


@router.get("/queue", response_model=list[QueueItem])
def get_queue(
    principal: CurrentPrincipal,
    depot_id: str | None = Query(None, description="narrow to one depot"),
    limit: int = Query(50, ge=1, le=200),
) -> list[QueueItem]:
    """Campaigns affecting the fleet, most urgent first.

    Ranking is **Park It, then exposed-vehicle count** — a do-not-drive order on 25 vehicles
    outranks a label recall on 1,801, because the ranking encodes consequence rather than
    volume. `DO_NOT_DRIVE` covers only 211 of 15,211 campaigns (I-014), so it is a genuine
    discriminator rather than a always-true flag.
    """
    # Snapshot mode short-circuits before any Lakebase call. The branch is here, at the top
    # of the handler, rather than hidden behind a store abstraction: one greppable line per
    # endpoint is easier to audit than a layer that could silently pick the wrong source.
    if snapshot.is_snapshot():
        return [QueueItem(**r) for r in snapshot.queue(limit)]

    scope = resolve_scope(principal, depot_id)
    # LEFT JOIN, not inner: a campaign with no launched service campaign is the common case,
    # not a missing-data case, and an inner join would silently drop it from the queue.
    # Scoped to status = 'LAUNCHED' to match the one partial unique index that actually
    # enforces "one active campaign per recall" (I-063) — a CANCELLED prior attempt must not
    # make this look launched.
    sql = f"""
        SELECT c.campaign_id,
               c.component,
               c.park_it,
               c.do_not_drive,
               COUNT(DISTINCT e.vin)      AS vehicles_exposed,
               COUNT(DISTINCT v.depot_id) AS depots_affected,
               c.consequence,
               MAX(sc.service_campaign_id) AS service_campaign_id
        FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
        JOIN {PG_SCHEMA}.fleetguard_vehicle v          ON v.vin = e.vin
        JOIN {PG_SCHEMA}.fleetguard_recall_campaign c  ON c.campaign_id = e.campaign_id
        LEFT JOIN {PG_SCHEMA}.fleetguard_service_campaign sc
               ON sc.campaign_id = c.campaign_id AND sc.status = 'LAUNCHED'
        {scope.where()}
        GROUP BY c.campaign_id, c.component, c.park_it, c.do_not_drive, c.consequence
        ORDER BY c.park_it DESC, c.do_not_drive DESC, vehicles_exposed DESC
        LIMIT %(limit)s
    """
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(sql, {**scope.params, "limit": limit})
        return [QueueItem(**r) for r in rows_to_dicts(cur)]


@router.get("/queue/summary", response_model=QueueSummary)
def get_queue_summary(
    principal: CurrentPrincipal,
    depot_id: str | None = Query(None, description="narrow to one depot"),
) -> QueueSummary:
    """Totals over the whole scoped exposure set, with no `LIMIT`.

    **Why an endpoint and not a field on `/queue`.** That route returns a bare
    `list[QueueItem]`; wrapping it in an envelope would break the snapshot path, the frontend
    and `tests/test_queue_routes.py` at once. Additive is the cheap, reversible shape.

    **The measures are copied from the `fleet_exposure_metrics` metric view, deliberately.**
    `Vehicles Exposed` is `COUNT(DISTINCT vin)` there and `COUNT(DISTINCT vin)` here, so the
    console and the AI/BI dashboard now agree *by construction* rather than by coincidence —
    which is the actual defect in I-130, where the two surfaces disagreed by 4.6x and the
    dashboard was the one telling the truth.

    The counts will still differ in magnitude between the two, and that is correct: the metric
    view reads Delta `gold_fleet_exposure` (~989k rows, the full match set) while this reads the
    Lakebase operational subset (118,323 rows). Same semantics over different populations.

    Joins are identical to `get_queue`'s, so the totals cannot describe a different population
    from the rows beneath them, and `resolve_scope` applies the same depot containment.
    """
    if snapshot.is_snapshot():
        return QueueSummary(**snapshot.queue_summary())

    scope = resolve_scope(principal, depot_id)
    sql = f"""
        SELECT COUNT(DISTINCT e.campaign_id) AS campaigns,
               COUNT(DISTINCT e.vin)         AS vehicles_exposed,
               COUNT(DISTINCT CASE WHEN c.park_it OR c.do_not_drive
                                   THEN e.campaign_id END) AS urgent_campaigns
        FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
        JOIN {PG_SCHEMA}.fleetguard_vehicle v          ON v.vin = e.vin
        JOIN {PG_SCHEMA}.fleetguard_recall_campaign c  ON c.campaign_id = e.campaign_id
        {scope.where()}
    """
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(sql, scope.params)
        return QueueSummary(**rows_to_dicts(cur)[0])


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(
    principal: CurrentPrincipal,
    campaign_id: str,
    depot_id: str | None = Query(None),
    sample: int = Query(25, ge=1, le=200),
) -> CampaignDetail:
    """Campaign detail plus its exposure, grouped by depot.

    Returns a *sample* of vehicles, not all of them: a campaign can touch 1,801 vehicles and
    the operator acts on depot totals, not a 1,801-row scroll. The full set is what the
    approval gate writes work orders against.
    """
    if snapshot.is_snapshot():
        d = snapshot.campaign(campaign_id)
        if not d:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{campaign_id} is not in the demo snapshot — open one from the queue's top rows.",
            )
        return CampaignDetail(**d)

    scope = resolve_scope(principal, depot_id)
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.campaign_id, c.component, c.park_it, c.consequence, c.remedy,
                       sc.service_campaign_id
                FROM {PG_SCHEMA}.fleetguard_recall_campaign c
                LEFT JOIN {PG_SCHEMA}.fleetguard_service_campaign sc
                       ON sc.campaign_id = c.campaign_id AND sc.status = 'LAUNCHED'
                WHERE c.campaign_id = %(cid)s""",
            {"cid": campaign_id},
        )
        head = rows_to_dicts(cur)
        if not head:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown campaign {campaign_id}")

        cur.execute(
            f"""SELECT v.depot_id, COUNT(DISTINCT e.vin) AS n
                FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
                JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
                WHERE e.campaign_id = %(cid)s
                  {scope.where("AND")}
                GROUP BY v.depot_id ORDER BY n DESC""",
            {"cid": campaign_id, **scope.params},
        )
        by_depot = {r["depot_id"]: r["n"] for r in rows_to_dicts(cur)}

        cur.execute(
            f"""SELECT e.vin, v.depot_id, v.make, v.model, v.model_year
                FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
                JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
                WHERE e.campaign_id = %(cid)s
                  {scope.where("AND")}
                ORDER BY v.depot_id, e.vin LIMIT %(sample)s""",
            {"cid": campaign_id, "sample": sample, **scope.params},
        )
        vehicles = [
            ExposedVehicle(**{**r, "vin": mask_vin(r["vin"], mask=scope.mask_vin)})
            for r in rows_to_dicts(cur)
        ]

    return CampaignDetail(
        **head[0],
        vehicles_exposed=sum(by_depot.values()),
        by_depot=by_depot,
        sample_vehicles=vehicles,
    )
