"""FleetGuard API — skeleton.

MVP surface (see ENHANCEMENTS.md): the work queue, exposure detail, and the approval gate.
Only `/healthz` and `/me` are implemented here; the domain routers land next.

Runs identically on Render and Databricks Apps. The single difference — how the caller's
token arrives — is confined to `auth/tokens.py` (E-13).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .deps import CurrentPrincipal
from .routers import approval, queue

app = FastAPI(
    title="FleetGuard API",
    version="0.1.0",
    description="Vehicle defect early warning and recall response.",
)

# The React dev server needs an origin during MVP. Read from env rather than hardcoded, so
# the deployed surfaces do not carry a localhost allowance.
_origins = [o for o in os.getenv("FLEETGUARD_CORS_ORIGINS", "").split(",") if o]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


class Health(BaseModel):
    status: str
    auth_mode: str


class Me(BaseModel):
    user_name: str | None
    token_source: str


app.include_router(queue.router)
app.include_router(approval.router)


@app.get("/healthz", response_model=Health, tags=["ops"])
def healthz() -> Health:
    """Unauthenticated liveness check.

    Reports the configured auth mode — not the token — so a misconfigured deployment is
    diagnosable without a valid session. Render's free tier spins down on inactivity, so
    this is also the endpoint to warm before a demo.
    """
    return Health(status="ok", auth_mode=os.getenv("FLEETGUARD_AUTH_MODE", "<unset>"))


@app.get("/me", response_model=Me, tags=["ops"])
def me(principal: CurrentPrincipal) -> Me:
    """Proves the auth seam end to end on whichever surface this is running.

    Returns the identity and where the token came from, never the token itself.
    """
    return Me(user_name=principal.user_name, token_source=principal.source)
