"""FleetGuard API and console.

One service serves both the JSON API (under `/api`) and the built React console. That is
deliberate: a single origin means no CORS, and the session cookie the U2M flow sets is simply
sent with each request rather than needing SameSite/credentials gymnastics. It also means one
Render service instead of two, and the same arrangement works unchanged inside Databricks Apps.

Runs identically on both surfaces. The single difference — how the caller's token arrives —
is confined to `auth/tokens.py` (E-13).
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import snapshot
from .deps import CurrentPrincipal
from .routers import (
    approval,
    audit_log,
    auth_routes,
    chat,
    corpus,
    depots,
    evidence,
    queue,
    readyz,
    recall_api,
    signals,
    technicians,
    trends,
    watchlist,
    work_orders,
)

app = FastAPI(
    title="FleetGuard API",
    version="0.1.0",
    description="Vehicle defect early warning and recall response.",
)

api = APIRouter(prefix="/api")


class Health(BaseModel):
    status: str
    auth_mode: str
    console: bool
    data_mode: str
    snapshot_captured_at: str | None = None


class Me(BaseModel):
    user_name: str | None
    token_source: str
    # The AI/BI dashboard's host + id differ per deployment (separate workspace on Free
    # Edition, separate metastore entirely) and the console's JS bundle is shared byte-for-byte
    # between targets (see scripts/sync_free_edition_app.sh), so the URL cannot be a literal in
    # frontend source — that was the actual bug (Home.tsx pointed at prod's workspace, which a
    # Free Edition account has no login for). Lives here rather than on `/healthz`: `/healthz`
    # at the bare app URL is intercepted by the Databricks Apps ingress before it reaches this
    # process (measured 2026-09-27 — 200, empty body, no corresponding line in `apps logs`), so
    # anything the console actually needs to read must go under `/api`.
    dashboard_url: str | None = None


# `dist` is built by `npm run build` in app/frontend and copied here at deploy time. Absent
# during backend-only development, which must not be an error.
CONSOLE_DIR = Path(os.getenv("FLEETGUARD_CONSOLE_DIR", Path(__file__).parent / "console"))


def _json_safe(value):
    """Replace non-finite floats with their text form, recursively.

    JSON has no `NaN` or `Infinity`. Python's `json.dumps` emits them anyway unless told not
    to — but Starlette's `JSONResponse` sets `allow_nan=False`, so any such value in a
    response body raises *during serialisation*, after the handler has already returned.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422s must be serialisable, including when the rejected input is what broke JSON.

    **Found while fixing I-117, and not by the review that prompted it.** Adding
    `allow_inf_nan=False` to `actual_cost` correctly rejects `{"actual_cost": NaN}` — and then
    FastAPI's default handler echoes the offending value back under `input`, Starlette refuses
    to serialise it, and the caller gets a **500 with a stack trace instead of a 422**. So the
    schema fix on its own traded a silent data-poisoning bug for a loud server error, which is
    better but still wrong, and would look like a crash to anyone probing the API.

    Registered globally rather than on one route: any endpoint accepting a float can be sent a
    non-finite one, and the next one added should not have to rediscover this.
    """
    return JSONResponse(
        status_code=422,  # not the named constant: starlette renamed it, and pinning the number
        # keeps this working across the deprecation either way.
        content={"detail": _json_safe(exc.errors())},
    )


@app.get("/healthz", response_model=Health, tags=["ops"])
def healthz() -> Health:
    """Unauthenticated liveness check.

    Reports the configured auth mode — never the token — so a misconfigured deployment is
    diagnosable without a valid identity. A stopped Databricks App takes a moment to come
    back, so this is also the endpoint to warm before a demo.
    """
    # `data_mode` is reported for the same reason `auth_mode` is: a deployment serving a
    # snapshot must be diagnosable as such from outside, without reading its environment.
    captured = None
    if snapshot.is_snapshot():
        try:
            captured = snapshot.captured_at()
        except FileNotFoundError:
            captured = None

    return Health(
        status="ok",
        auth_mode=os.getenv("FLEETGUARD_AUTH_MODE", "<unset>"),
        console=(CONSOLE_DIR / "index.html").exists(),
        data_mode=snapshot.data_mode(),
        snapshot_captured_at=captured,
    )


@api.get("/me", response_model=Me, tags=["ops"])
def me(principal: CurrentPrincipal) -> Me:
    """Proves the auth seam end to end, and tells the console who it will attribute
    approvals to. Returns the identity, never the token."""
    return Me(
        user_name=principal.user_name,
        token_source=principal.source,
        # Falls back to prod's current published URL, so prod is unaffected by this env var
        # existing — see the field's docstring on `Me` for why this isn't on `/healthz`.
        dashboard_url=os.getenv(
            "FLEETGUARD_DASHBOARD_URL",
            "https://dbc-7b106152-caf3.cloud.databricks.com/dashboardsv3/01f1a7257e801a2ebb71bdc18fc2113a/published",
        ),
    )


api.include_router(queue.router)
api.include_router(recall_api.router)
api.include_router(approval.router)
api.include_router(chat.router)
api.include_router(evidence.router)
# Public like `evidence` and for the same reason — corpus scale carries no identity-scopable
# data. See routers/corpus.py; tests/test_evidence_route.py pins that these two and ONLY these
# two answer without a session.
api.include_router(corpus.router)
api.include_router(signals.router)
api.include_router(work_orders.router)
api.include_router(technicians.router)
api.include_router(audit_log.router)
api.include_router(depots.router)
api.include_router(trends.router)
api.include_router(watchlist.router)
api.include_router(auth_routes.router)
# `/readyz` answers a different question from `/healthz` above — see routers/readyz.py.
# It is authenticated and lives under `/api` because its Lakebase check must run as the
# caller; `/healthz` stays unauthenticated and always-200 as the container probe.
api.include_router(readyz.router)
app.include_router(api)


# Mounted last so it cannot shadow `/api` or `/healthz`.
if (CONSOLE_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=CONSOLE_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """Serve the console for any non-API path.

        The console is a single-page app, so a deep link like /campaigns/17V629000 must
        return index.html rather than 404 — the client router resolves it.
        """
        return FileResponse(CONSOLE_DIR / "index.html")
