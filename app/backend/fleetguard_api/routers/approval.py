"""The approval gate — the write path, and the reason FleetGuard is not a dashboard.

Everything here happens in **one transaction**: the service campaign, every work order, and
the audit row. A partial approval — a campaign with half its work orders, or work orders
with no audit trail — is worse than a failed one, because it looks like success.

This is also the §8.3 velocity demonstration: the commit lands in Lakebase, and CDF carries
it to Unity Catalog in a **measured 7.1–15.6 s** (I-046).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal
from ..scoping import resolve_scope

router = APIRouter(tags=["approval"])

DEFAULT_DUE_DAYS = 30


class ApprovalRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    rationale: str = Field(min_length=3, max_length=2000)
    due_in_days: int = Field(DEFAULT_DUE_DAYS, ge=1, le=365)
    depot_id: str | None = None


class ApprovalResult(BaseModel):
    service_campaign_id: str
    campaign_id: str
    work_orders_created: int
    approved_by: str
    due_date: date


@router.post(
    "/campaigns/{campaign_id}/service-campaign",
    response_model=ApprovalResult,
    status_code=status.HTTP_201_CREATED,
)
def approve_campaign(
    principal: CurrentPrincipal, campaign_id: str, body: ApprovalRequest
) -> ApprovalResult:
    """Launch a service campaign against a recall's exposed vehicles.

    The approver is `principal.user_name` — taken from the authenticated identity, never
    from the request body. Letting a client name its own approver would make the audit trail
    decorative.
    """
    approver = principal.user_name
    if not approver:
        # An unattributable approval cannot be audited, so it must not happen.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "approval requires an identified user; this token carries no identity",
        )

    scope = resolve_scope(principal, body.depot_id)
    sc_id = f"SC-{campaign_id}-{uuid.uuid4().hex[:8]}"
    due = date.today() + timedelta(days=body.due_in_days)

    # autocommit=False: all three writes commit together or none do.
    with connect(principal, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT campaign_id, component, park_it FROM {PG_SCHEMA}."
                    f"fleetguard_recall_campaign WHERE campaign_id = %(cid)s",
                    {"cid": campaign_id},
                )
                campaign = rows_to_dicts(cur)
                if not campaign:
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND, f"unknown campaign {campaign_id}"
                    )

                cur.execute(
                    f"""SELECT DISTINCT e.vin, v.depot_id
                        FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
                        JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
                        WHERE e.campaign_id = %(cid)s
                          {"AND " + scope.predicate if scope.predicate else ""}""",
                    {"cid": campaign_id, **scope.params},
                )
                exposed = rows_to_dicts(cur)
                if not exposed:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"campaign {campaign_id} exposes no vehicles in scope — nothing to do",
                    )

                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_service_campaign
                        (service_campaign_id, campaign_id, title, vehicle_count, status,
                         created_by, approved_by, approved_at, launched_at)
                        VALUES (%(sc)s, %(cid)s, %(title)s, %(n)s, 'LAUNCHED',
                                %(who)s, %(who)s, now(), now())""",
                    {
                        "sc": sc_id,
                        "cid": campaign_id,
                        "title": body.title,
                        "n": len(exposed),
                        "who": approver,
                    },
                )

                cur.executemany(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_work_order
                        (wo_id, service_campaign_id, vin, depot_id, due_date, status)
                        VALUES (%(wo)s, %(sc)s, %(vin)s, %(depot)s, %(due)s, 'OPEN')""",
                    [
                        {
                            "wo": f"WO-{uuid.uuid4().hex[:12]}",
                            "sc": sc_id,
                            "vin": r["vin"],
                            "depot": r["depot_id"],
                            "due": due,
                        }
                        for r in exposed
                    ],
                )

                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                        (entity_type, entity_id, action, actor_principal, after_state)
                        VALUES ('service_campaign', %(sc)s, 'LAUNCH', %(who)s, %(after)s)""",
                    {
                        "sc": sc_id,
                        "who": approver,
                        "after": json.dumps(
                            {
                                "campaign_id": campaign_id,
                                "component": campaign[0]["component"],
                                "park_it": campaign[0]["park_it"],
                                "work_orders": len(exposed),
                                "due_date": due.isoformat(),
                                "rationale": body.rationale,
                                "scope": scope.mode.value,
                            }
                        ),
                    },
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return ApprovalResult(
        service_campaign_id=sc_id,
        campaign_id=campaign_id,
        work_orders_created=len(exposed),
        approved_by=approver,
        due_date=due,
    )


@router.get("/service-campaigns", tags=["approval"])
def list_service_campaigns(principal: CurrentPrincipal, limit: int = 50) -> list[dict]:
    """Recently launched service campaigns — what the demo checks after approving."""
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT s.service_campaign_id, s.campaign_id, s.title, s.vehicle_count,
                       s.status, s.approved_by, s.approved_at,
                       COUNT(w.wo_id) FILTER (WHERE w.status = 'OPEN') AS open_work_orders
                FROM {PG_SCHEMA}.fleetguard_service_campaign s
                LEFT JOIN {PG_SCHEMA}.fleetguard_work_order w
                       ON w.service_campaign_id = s.service_campaign_id
                GROUP BY s.service_campaign_id, s.campaign_id, s.title, s.vehicle_count,
                         s.status, s.approved_by, s.approved_at
                ORDER BY s.approved_at DESC NULLS LAST LIMIT %(limit)s""",
            {"limit": limit},
        )
        return rows_to_dicts(cur)
