"""The watchlist — campaigns the fleet safety team has flagged to keep an eye on.

Populated by the agent's `watch_campaign` write action (see `agent_actions.py`), executed
under the requesting user's own OBO token, never the agent's. This router is the read side
only: a plain list, no gold-layer round-trip (unlike `signals.py`'s `fleetguard_defect_signal`)
because there is no analytical use for this table yet — it is a bookmark list, not a detector
output.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import snapshot
from ..db import PG_SCHEMA, connect, rows_to_dicts
from ..deps import CurrentPrincipal

router = APIRouter(tags=["watchlist"])


class WatchlistEntry(BaseModel):
    watchlist_id: str
    campaign_id: str
    rationale: str
    watched_by: str
    status: str
    watched_at: datetime


@router.get("/watchlist", response_model=list[WatchlistEntry])
def list_watchlist(
    principal: CurrentPrincipal, limit: int = Query(50, ge=1, le=200)
) -> list[WatchlistEntry]:
    """Recently watched campaigns, most recent first."""
    if snapshot.is_snapshot():
        # No campaign has been watched on a read-only surface with no Databricks identity to
        # write under — same reasoning as approval.py's list_service_campaigns.
        return []

    with connect(principal) as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT watchlist_id, campaign_id, rationale, watched_by, status, watched_at
                FROM {PG_SCHEMA}.fleetguard_watchlist
                ORDER BY watched_at DESC
                LIMIT %(limit)s""",
            {"limit": limit},
        )
        return [WatchlistEntry(**r) for r in rows_to_dicts(cur)]
