"""FleetGuard API and console.

One service serves both the JSON API (under `/api`) and the built React console. That is
deliberate: a single origin means no CORS, and the session cookie the U2M flow sets is simply
sent with each request rather than needing SameSite/credentials gymnastics. It also means one
Render service instead of two, and the same arrangement works unchanged inside Databricks Apps.

Runs identically on both surfaces. The single difference — how the caller's token arrives —
is confined to `auth/tokens.py` (E-13).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import snapshot
from .deps import CurrentPrincipal
from .routers import (
    approval,
    audit_log,
    auth_routes,
    chat,
    databricks_auth_routes,
    depots,
    evidence,
    queue,
    signals,
    technicians,
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


# `dist` is built by `npm run build` in app/frontend and copied here at deploy time. Absent
# during backend-only development, which must not be an error.
CONSOLE_DIR = Path(os.getenv("FLEETGUARD_CONSOLE_DIR", Path(__file__).parent / "console"))


@app.get("/healthz", response_model=Health, tags=["ops"])
def healthz() -> Health:
    """Unauthenticated liveness check.

    Reports the configured auth mode — never the token — so a misconfigured deployment is
    diagnosable without a valid session. Render's free tier spins down on inactivity, so this
    is also the endpoint to warm before a demo.
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
    return Me(user_name=principal.user_name, token_source=principal.source)


api.include_router(queue.router)
api.include_router(approval.router)
api.include_router(chat.router)
api.include_router(evidence.router)
api.include_router(signals.router)
api.include_router(work_orders.router)
api.include_router(technicians.router)
api.include_router(audit_log.router)
api.include_router(depots.router)
api.include_router(auth_routes.router)
api.include_router(databricks_auth_routes.router)
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
