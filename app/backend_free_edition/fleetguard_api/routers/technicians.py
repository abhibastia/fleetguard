"""The technician roster — what `work_orders.py`'s assignment validates against.

A real table (`fleetguard_technician`, `src/lakebase/17_create_technician_roster.py`), not a
free-text field on the work order: it has a `depot_id`, so an assignment can be checked against
the work order's own depot rather than trusted on faith, and it can answer "how many open work
orders does this person have" later without inventing a schema at that point.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["technicians"])


class TechnicianOut(BaseModel):
    technician_id: str
    name: str
    depot_id: str
    active: bool


@router.get("/technicians", response_model=list[TechnicianOut])
def list_technicians(
    principal: CurrentPrincipal,
    depot_id: str | None = Query(None, description="narrow to one depot"),
    active_only: bool = Query(True),
) -> list[TechnicianOut]:
    if snapshot.is_snapshot():
        return []

    conditions: list[str] = []
    params: dict = {}
    if depot_id:
        conditions.append("depot_id = %(depot_id)s")
        params["depot_id"] = depot_id
    if active_only:
        conditions.append("active = true")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT technician_id, name, depot_id, active
                FROM {PG_SCHEMA}.fleetguard_technician
                {where}
                ORDER BY depot_id, name""",
            params,
        )
        return [TechnicianOut(**r) for r in rows_to_dicts(cur)]
