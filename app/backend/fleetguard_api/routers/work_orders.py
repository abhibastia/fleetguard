"""Work orders — closing the loop past "approve."

`approval.py` creates one `fleetguard_work_order` row per exposed vehicle when a service
campaign launches, but until now nothing ever read or updated them again after that moment —
`list_service_campaigns` only exposes an aggregate `COUNT(...) FILTER (WHERE status='OPEN')`.
For an operator, "did the vehicle actually get fixed" is the whole point of the recall→repair
loop; this router is what makes that visible.

Status and assignment changes are gated the same way approving a campaign is —
`auth_routes.may_approve`, unconditional on `principal.source` (fixed 2026-09-04,
`tests/test_approval_gate.py`) — because marking a safety-recall work order "completed" when
it wasn't, or reassigning it to someone who never touched it, is a real compliance risk, not
casual data entry. Reading the list is open to any signed-in identity, same asymmetry as the
rest of the console.

Assignment validates against `fleetguard_technician` (`src/lakebase/
17_create_technician_roster.py`), not a free-text name — specifically, that the chosen
technician belongs to the *same depot* as the work order. A technician picker that let you
assign DEP-051's work to a DEP-042 technician would be decorative, not a real constraint; the
check happens here in the handler, not just as a UI filter, for the same reason the rest of
this codebase never trusts the frontend to enforce anything it can enforce itself.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal
from ..scoping import resolve_scope
from . import auth_routes

router = APIRouter(tags=["work-orders"])

STATUSES = ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED")


class WorkOrderOut(BaseModel):
    wo_id: str
    service_campaign_id: str | None
    vin: str
    depot_id: str
    assigned_to: str | None
    assigned_to_name: str | None
    due_date: date | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    actual_cost: float | None


class WorkOrderUpdate(BaseModel):
    """Any subset of the three fields may be omitted — status, assignment, and cost are driven
    by three separate controls in the UI. `assigned_to: null` is a deliberate unassign, distinct
    from omitting the field entirely; `model_fields_set` (not "is it None") is what tells the
    two apart, since a default and an explicit null are otherwise indistinguishable. The same
    applies to `actual_cost: null` — clearing a mis-logged cost is a real, distinct action from
    not touching it.
    """

    status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"] | None = None
    assigned_to: str | None = None
    actual_cost: float | None = None


WORK_ORDER_SELECT = """
    SELECT w.wo_id, w.service_campaign_id, w.vin, w.depot_id, w.assigned_to,
           t.name AS assigned_to_name, w.due_date, w.status, w.created_at, w.completed_at,
           w.actual_cost
    FROM {schema}.fleetguard_work_order w
    LEFT JOIN {schema}.fleetguard_technician t ON t.technician_id = w.assigned_to
"""


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    principal: CurrentPrincipal,
    service_campaign_id: str | None = Query(None),
    depot_id: str | None = Query(None, description="narrow to one depot, voluntary not enforced"),
    status_: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> list[WorkOrderOut]:
    """Individual work orders — the thing `list_service_campaigns`' aggregate count can't show."""
    if snapshot.is_snapshot():
        # Same rule as list_service_campaigns: nothing has been created on a read-only
        # surface, and that is the truth, not an error.
        return []

    # column="w.depot_id" - the query below joins fleetguard_technician, which also has its
    # own depot_id column, so the bare column name resolve_scope defaults to would be
    # ambiguous once both tables are in scope.
    scope = resolve_scope(principal, depot_id, column="w.depot_id")
    conditions: list[str] = []
    params: dict = {}
    if scope.predicate:
        conditions.append(scope.predicate)
        params.update(scope.params)
    if service_campaign_id:
        conditions.append("w.service_campaign_id = %(service_campaign_id)s")
        params["service_campaign_id"] = service_campaign_id
    if status_:
        conditions.append("w.status = %(status)s")
        params["status"] = status_
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params["limit"] = limit

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            WORK_ORDER_SELECT.format(schema=PG_SCHEMA)
            + f"{where} ORDER BY w.created_at DESC LIMIT %(limit)s",
            params,
        )
        return [WorkOrderOut(**r) for r in rows_to_dicts(cur)]


class CostBreakdownRow(BaseModel):
    key: str
    total_actual_cost: float
    costed_count: int
    total_work_orders: int


class CostBreakdown(BaseModel):
    by_component: list[CostBreakdownRow]
    by_depot: list[CostBreakdownRow]


@router.get("/cost-breakdown", response_model=CostBreakdown, tags=["work-orders"])
def cost_breakdown(principal: CurrentPrincipal) -> CostBreakdown:
    """Real logged repair cost, grouped two ways — never a single blended average.

    A flat per-vehicle cost assumption was tried and rejected (see `docs/ARCHITECTURE.md`): a
    steering-rack repair on a Class 8 tractor and a brake job on a pickup are not the same
    cost, so summing what was actually logged and breaking it down by component and by depot
    is the honest replacement. `costed_count` vs `total_work_orders` is reported on every row
    so a caller can see coverage rather than averaging over work orders with no cost logged
    as if they cost nothing.
    """
    if snapshot.is_snapshot():
        return CostBreakdown(by_component=[], by_depot=[])
    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT rc.component AS key,
                       COALESCE(SUM(w.actual_cost), 0) AS total_actual_cost,
                       COUNT(w.wo_id) FILTER (WHERE w.actual_cost IS NOT NULL) AS costed_count,
                       COUNT(w.wo_id) AS total_work_orders
                FROM {PG_SCHEMA}.fleetguard_work_order w
                JOIN {PG_SCHEMA}.fleetguard_service_campaign sc
                     ON sc.service_campaign_id = w.service_campaign_id
                JOIN {PG_SCHEMA}.fleetguard_recall_campaign rc ON rc.campaign_id = sc.campaign_id
                GROUP BY rc.component
                ORDER BY total_actual_cost DESC"""
        )
        by_component = [CostBreakdownRow(**r) for r in rows_to_dicts(cur)]

        cur.execute(
            f"""SELECT w.depot_id AS key,
                       COALESCE(SUM(w.actual_cost), 0) AS total_actual_cost,
                       COUNT(w.wo_id) FILTER (WHERE w.actual_cost IS NOT NULL) AS costed_count,
                       COUNT(w.wo_id) AS total_work_orders
                FROM {PG_SCHEMA}.fleetguard_work_order w
                GROUP BY w.depot_id
                ORDER BY total_actual_cost DESC"""
        )
        by_depot = [CostBreakdownRow(**r) for r in rows_to_dicts(cur)]

    return CostBreakdown(by_component=by_component, by_depot=by_depot)


@router.patch("/work-orders/{wo_id}", response_model=WorkOrderOut)
def update_work_order(
    principal: CurrentPrincipal, wo_id: str, body: WorkOrderUpdate
) -> WorkOrderOut:
    """Change a work order's status, its assignment, or both in one call.

    Same two refusals as `approve_campaign`, in the same order and for the same reasons:
    signing in proves identity, not authorisation to write, so this is restricted to
    `FLEETGUARD_APPROVERS`; and a deployment with no live Databricks credential has nothing to
    write to, so it refuses rather than faking a plausible-looking update.
    """
    # Identity and authorisation first, same order as approve_campaign - an unauthenticated
    # or unauthorised caller shouldn't learn anything about request-shape validation before
    # being refused for who they are.
    approver = principal.user_name
    if not approver:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "updating a work order requires an identified user; this token carries no identity",
        )
    if not auth_routes.may_approve(approver):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{approver} is signed in but not an approver on this deployment.",
        )

    fields_set = body.model_fields_set
    if not fields_set & {"status", "assigned_to", "actual_cost"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "at least one of status, assigned_to, or actual_cost must be provided",
        )
    if body.actual_cost is not None and body.actual_cost < 0:
        # Caught here for a clean 400 rather than letting the live DB constraint
        # (fg_wo_actual_cost_nonnegative, src/lakebase/18_add_work_order_actual_cost.py)
        # surface a raw psycopg error to the caller.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "actual_cost cannot be negative")

    if snapshot.is_snapshot():
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "This deployment runs on a data snapshot and cannot update work orders.",
        )

    with connect(principal, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT status, depot_id, assigned_to, actual_cost
                        FROM {PG_SCHEMA}.fleetguard_work_order
                        WHERE wo_id = %(wo_id)s FOR UPDATE""",
                    {"wo_id": wo_id},
                )
                current = rows_to_dicts(cur)
                if not current:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown work order {wo_id}")
                before = current[0]

                set_clauses: list[str] = []
                params: dict = {"wo_id": wo_id}

                if "status" in fields_set:
                    set_clauses.append("status = %(status)s")
                    set_clauses.append(
                        "completed_at = CASE WHEN %(status)s = 'COMPLETED' THEN now() ELSE NULL END"
                    )
                    params["status"] = body.status

                if "assigned_to" in fields_set:
                    # Assigning (not clearing) validates against the roster and, specifically,
                    # that the technician belongs to this work order's own depot — the check
                    # this feature exists for, not just a foreign-key-shaped decoration.
                    if body.assigned_to is not None:
                        cur.execute(
                            f"""SELECT 1 FROM {PG_SCHEMA}.fleetguard_technician
                                WHERE technician_id = %(tech)s AND depot_id = %(depot)s
                                  AND active = true""",
                            {"tech": body.assigned_to, "depot": before["depot_id"]},
                        )
                        if cur.fetchone() is None:
                            raise HTTPException(
                                status.HTTP_400_BAD_REQUEST,
                                f"{body.assigned_to} is not an active technician at depot "
                                f"{before['depot_id']}",
                            )
                    set_clauses.append("assigned_to = %(assigned_to)s")
                    params["assigned_to"] = body.assigned_to

                if "actual_cost" in fields_set:
                    set_clauses.append("actual_cost = %(actual_cost)s")
                    params["actual_cost"] = body.actual_cost

                cur.execute(
                    f"""UPDATE {PG_SCHEMA}.fleetguard_work_order
                        SET {", ".join(set_clauses)}
                        WHERE wo_id = %(wo_id)s""",
                    params,
                )

                # One audit row per changed dimension, not one row trying to describe both -
                # a reader asking "who reassigned this" shouldn't have to parse out an
                # unrelated status change bundled into the same entry, and vice versa.
                if "status" in fields_set:
                    cur.execute(
                        f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                            (entity_type, entity_id, action, actor_principal, before_state, after_state)
                            VALUES ('work_order', %(wo_id)s, 'STATUS_CHANGE', %(who)s, %(before)s, %(after)s)""",
                        {
                            "wo_id": wo_id,
                            "who": approver,
                            "before": json.dumps({"status": before["status"]}),
                            "after": json.dumps({"status": body.status}),
                        },
                    )
                if "assigned_to" in fields_set:
                    cur.execute(
                        f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                            (entity_type, entity_id, action, actor_principal, before_state, after_state)
                            VALUES ('work_order', %(wo_id)s, 'ASSIGNED', %(who)s, %(before)s, %(after)s)""",
                        {
                            "wo_id": wo_id,
                            "who": approver,
                            "before": json.dumps({"assigned_to": before["assigned_to"]}),
                            "after": json.dumps({"assigned_to": body.assigned_to}),
                        },
                    )
                if "actual_cost" in fields_set:
                    cur.execute(
                        f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                            (entity_type, entity_id, action, actor_principal, before_state, after_state)
                            VALUES ('work_order', %(wo_id)s, 'COST_LOGGED', %(who)s, %(before)s, %(after)s)""",
                        {
                            "wo_id": wo_id,
                            "who": approver,
                            "before": json.dumps(
                                {
                                    "actual_cost": float(before["actual_cost"])
                                    if before["actual_cost"] is not None
                                    else None
                                }
                            ),
                            "after": json.dumps({"actual_cost": body.actual_cost}),
                        },
                    )

                cur.execute(
                    WORK_ORDER_SELECT.format(schema=PG_SCHEMA) + "WHERE w.wo_id = %(wo_id)s",
                    {"wo_id": wo_id},
                )
                updated = rows_to_dicts(cur)[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return WorkOrderOut(**updated)
