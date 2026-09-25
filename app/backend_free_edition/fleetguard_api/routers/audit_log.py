"""The audit log, made into something a human can actually read.

`fleetguard_audit_log` has recorded every campaign launch, work-order status change,
(re)assignment, and cost log since Phase 7 — but nothing has ever exposed it. For a
recall-response platform, "who approved this, and when" is the actual compliance artifact;
leaving it queryable only via a live Lakebase connection is the same gap `list_service_campaigns`
had before `ServiceCampaigns.tsx` existed. This router adds a JSON list (for an in-app view) and
a CSV export (for handing to someone who isn't going to open the console).

Read-only, so it follows the same asymmetry as every other fleet-data read in this app: open to
any signed-in identity, no `FLEETGUARD_APPROVERS` gate. The gate exists to stop someone from
*writing* a plausible-looking action into the log, not from *reading* what already happened.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["audit-log"])

# Leading characters a spreadsheet app (Excel, Sheets, LibreOffice) treats as a formula
# trigger rather than literal text — the OWASP CSV Injection set. Several columns here
# ultimately trace back to LLM- or user-supplied text (rationale, title, via after_state),
# so this is applied to every string cell rather than reasoned about per-column.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value: str) -> str:
    """A leading apostrophe is rendered as literal text by every major spreadsheet app rather
    than evaluated — the standard mitigation for CSV formula injection on untrusted export."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


AUDIT_LOG_SELECT = """
    SELECT audit_id, entity_type, entity_id, action, actor_principal,
           before_state, after_state, created_at
    FROM {schema}.fleetguard_audit_log
"""


class AuditLogEntry(BaseModel):
    audit_id: int
    entity_type: str
    entity_id: str
    action: str
    actor_principal: str
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    created_at: datetime


def _fetch(
    principal: CurrentPrincipal,
    entity_type: str | None,
    entity_id: str | None,
    action: str | None,
    limit: int,
) -> list[dict]:
    if snapshot.is_snapshot():
        return []  # nothing has been written on a read-only surface, and that is the truth

    conditions: list[str] = []
    params: dict = {"limit": limit}
    if entity_type:
        conditions.append("entity_type = %(entity_type)s")
        params["entity_type"] = entity_type
    if entity_id:
        conditions.append("entity_id = %(entity_id)s")
        params["entity_id"] = entity_id
    if action:
        conditions.append("action = %(action)s")
        params["action"] = action
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            AUDIT_LOG_SELECT.format(schema=PG_SCHEMA)
            + f"{where} ORDER BY created_at DESC LIMIT %(limit)s",
            params,
        )
        return rows_to_dicts(cur)


@router.get("/audit-log", response_model=list[AuditLogEntry])
def list_audit_log(
    principal: CurrentPrincipal,
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> list[AuditLogEntry]:
    return [AuditLogEntry(**r) for r in _fetch(principal, entity_type, entity_id, action, limit)]


@router.get("/audit-log/export.csv")
def export_audit_log_csv(
    principal: CurrentPrincipal,
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
) -> Response:
    """Same data as `list_audit_log`, as a file — for handing to someone who needs a record,
    not a live query. `before_state`/`after_state` are serialized to a JSON string per cell
    rather than split into columns, since the shape of each varies by `action`."""

    rows = _fetch(principal, entity_type, entity_id, action, limit)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "audit_id",
            "entity_type",
            "entity_id",
            "action",
            "actor_principal",
            "before_state",
            "after_state",
            "created_at",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["audit_id"],
                _csv_safe(r["entity_type"]),
                _csv_safe(r["entity_id"]),
                _csv_safe(r["action"]),
                _csv_safe(r["actor_principal"]),
                _csv_safe(json.dumps(r["before_state"])) if r["before_state"] is not None else "",
                _csv_safe(json.dumps(r["after_state"])) if r["after_state"] is not None else "",
                r["created_at"].isoformat(),
            ]
        )

    # datetime.now(UTC), not the deprecated utcnow() — the latter warns on 3.12+ and is
    # scheduled for removal. Formatted with a literal Z since strftime has no offset
    # directive that renders "Z" for UTC.
    filename = f"fleetguard_audit_log_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
