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
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import snapshot
from ..authz import may_approve
from ..db import PG_SCHEMA, UniqueViolation, connect, rows_to_dicts
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

    # Two refusals before any work, both stated plainly rather than mimed.
    #
    # 1. Signing in proves you are someone. It does not prove you may dispatch work orders
    #    against a fleet, so approval is restricted to an explicit allowlist — checked
    #    regardless of *how* the caller authenticated. This used to be conditional on whether
    #    an app-owned login flow was configured, which meant a principal carrying a real
    #    Databricks token skipped the allowlist entirely. That was fine when "has a Databricks
    #    identity in this workspace" implied "is a trusted operator" — it stopped being fine
    #    once the workspace turned out to be shared with the judges/cohort too (found
    #    2026-09-03): every one of them would have been able to launch service campaigns, not
    #    just view them. `FLEETGUARD_APPROVERS` unset now means nobody can approve, on any
    #    surface — an explicit decision, not a silent default.
    if not may_approve(approver):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{approver} is signed in but not an approver on this deployment.",
        )

    # 2. The public deployment has no Databricks credential, so there is nothing to write to.
    #    Refusing is the honest answer: a simulated approval that returned a plausible
    #    service-campaign id would be a lie told by the safety-critical path (I-050's lesson,
    #    applied to a write instead of a read).
    if snapshot.is_snapshot():
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "This deployment runs on a data snapshot and cannot create work orders. "
            "The approval path is real and runs against live Lakebase where the console "
            "has a Databricks identity.",
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

                # I-063: one *active* service campaign per recall. This check is for the
                # error message — it names what already exists so the operator can go look
                # at it — not for enforcement. Enforcement is the partial unique index
                # (`ux_fg_service_campaign_active`, src/lakebase/
                # 19_add_service_campaign_uniqueness.py), because the case being defended
                # against is a double-click: two requests milliseconds apart, where both
                # would read "nothing there" before either inserts. See the UniqueViolation
                # handler below, which is what actually catches that.
                cur.execute(
                    f"""SELECT service_campaign_id, approved_at
                        FROM {PG_SCHEMA}.fleetguard_service_campaign
                        WHERE campaign_id = %(cid)s AND status = 'LAUNCHED'""",
                    {"cid": campaign_id},
                )
                active = rows_to_dicts(cur)
                if active:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"{campaign_id} already has an active service campaign "
                        f"({active[0]['service_campaign_id']}, launched "
                        f"{active[0]['approved_at']:%Y-%m-%d %H:%M} UTC). Cancel it before "
                        f"launching another, or open it to see its work orders.",
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

                try:
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
                except UniqueViolation as exc:
                    # The double-click actually landing. The pre-check above passed because a
                    # concurrent request had not committed yet; the index is what serialises
                    # them. Same 409 as the pre-check — from the caller's side these are the
                    # same refusal, and which one fired is a timing detail they cannot act on.
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"{campaign_id} was approved concurrently by another request. "
                        f"Reload to see the service campaign that was created.",
                    ) from exc

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


class ServiceCampaignOut(BaseModel):
    service_campaign_id: str
    campaign_id: str
    title: str
    vehicle_count: int
    status: str
    approved_by: str | None
    approved_at: datetime | None
    open_count: int
    in_progress_count: int
    completed_count: int
    cancelled_count: int
    total_actual_cost: float
    costed_count: int


@router.get("/service-campaigns", response_model=list[ServiceCampaignOut], tags=["approval"])
def list_service_campaigns(
    # le=500, not 200: WorkOrders.tsx calls this with limit=500 to populate its campaign
    # filter dropdown with every launched campaign, not just the most recent page — matching
    # the same 500 ceiling routers/work_orders.py already uses for the same reason. A tighter
    # bound here 422s that legitimate caller (found live, browser-testing the useFetch
    # refactor, 2026-09-17) rather than protecting anything the original finding cared about.
    principal: CurrentPrincipal,
    limit: int = Query(50, ge=1, le=500),
) -> list[ServiceCampaignOut]:
    """Recently launched service campaigns, with a per-status work-order breakdown.

    A string literal placed after the snapshot-mode return below is NOT a docstring to the
    interpreter — it silently becomes a dead no-op statement, and FastAPI's generated docs
    lose this endpoint's description with no error anywhere. Caught in the 2026-09-02 repo
    review; the docstring belongs here, before any code.
    """
    if snapshot.is_snapshot():
        return []  # nothing has been approved on a read-only surface, and that is the truth
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT s.service_campaign_id, s.campaign_id, s.title, s.vehicle_count,
                       s.status, s.approved_by, s.approved_at,
                       COUNT(w.wo_id) FILTER (WHERE w.status = 'OPEN') AS open_count,
                       COUNT(w.wo_id) FILTER (WHERE w.status = 'IN_PROGRESS') AS in_progress_count,
                       COUNT(w.wo_id) FILTER (WHERE w.status = 'COMPLETED') AS completed_count,
                       COUNT(w.wo_id) FILTER (WHERE w.status = 'CANCELLED') AS cancelled_count,
                       COALESCE(SUM(w.actual_cost), 0) AS total_actual_cost,
                       COUNT(w.wo_id) FILTER (WHERE w.actual_cost IS NOT NULL) AS costed_count
                FROM {PG_SCHEMA}.fleetguard_service_campaign s
                LEFT JOIN {PG_SCHEMA}.fleetguard_work_order w
                       ON w.service_campaign_id = s.service_campaign_id
                GROUP BY s.service_campaign_id, s.campaign_id, s.title, s.vehicle_count,
                         s.status, s.approved_by, s.approved_at
                ORDER BY s.approved_at DESC NULLS LAST LIMIT %(limit)s""",
            {"limit": limit},
        )
        return rows_to_dicts(cur)
