"""Sign-in for the public deployment — GitHub OAuth.

**Why GitHub rather than Databricks, for `app-login`.** Databricks U2M needs an
account-level OAuth app that this account could not register when this mode was built
(E-14), and every machine-credential route is closed too (service principals are
admin-only, PATs are disabled for this user). GitHub costs nothing, needs no admin, and —
importantly — means this app never stores a password. Where a registered OAuth app *is*
available, `databricks_auth_routes.py` is the sibling login for `FLEETGUARD_AUTH_MODE=
render-u2m`, carrying a real Databricks token instead of an app-only identity; `/auth/status`
below reports whichever one is configured.

**What the login actually protects.** `ARCHITECTURE.md` §8a says not to put a shared identity
on a public URL *"because the API has a write path, so anyone could approve service
campaigns"*. A login is precisely the mitigation that objection asks for: approvals stop being
open to anyone with the URL, and the audit row records which identity approved.

**Two tiers, deliberately.** Anyone who signs in can *read*. Only logins in
`FLEETGUARD_APPROVERS` can approve. Sign-in proves you are someone; it does not prove you
should be able to dispatch work orders against a fleet.
"""

from __future__ import annotations

import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..auth import databricks_oauth as dbx
from ..deps import SESSIONS

router = APIRouter(tags=["auth"])

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"

COOKIE = "fg_session"
SESSION_TTL_S = 8 * 3600

# Short-lived CSRF state. In-memory is adequate: it lives seconds, and a Render restart
# mid-login costs the user one retry.
_STATES: dict[str, float] = {}
_STATE_TTL_S = 600


def _client_id() -> str:
    return os.getenv("GITHUB_CLIENT_ID", "")


def _client_secret() -> str:
    return os.getenv("GITHUB_CLIENT_SECRET", "")


def enabled() -> bool:
    return bool(_client_id() and _client_secret())


def approvers() -> set[str]:
    """GitHub logins allowed to approve. Empty means nobody — read-only for everyone."""
    raw = os.getenv("FLEETGUARD_APPROVERS", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def may_approve(user_name: str | None) -> bool:
    return bool(user_name) and user_name.lower() in approvers()


class AuthStatus(BaseModel):
    enabled: bool
    signed_in: bool
    user_name: str | None
    may_approve: bool
    # Which login flow this deployment runs, and where to send the browser for it — the
    # console reads these instead of hardcoding a GitHub-shaped button, so it renders
    # correctly under either FLEETGUARD_AUTH_MODE without a frontend redeploy.
    provider: str
    login_url: str | None


def _active_provider() -> tuple[bool, str, str | None]:
    """(is_enabled, provider, login_url) for whichever flow FLEETGUARD_AUTH_MODE selects.

    Deliberately keyed off the auth mode rather than "whichever provider's env vars happen
    to be set" — two logins configured at once on one deployment is not a state this app is
    designed to run in, and guessing would hide a misconfiguration instead of surfacing it.
    """
    mode = (os.getenv("FLEETGUARD_AUTH_MODE") or "").strip().lower()
    if mode == "render-u2m":
        return dbx.enabled(), "databricks", "/api/auth/databricks/login"
    return enabled(), "github", "/api/auth/login"


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    """Unauthenticated: the console needs to know whether to render a sign-in button."""
    sid = request.cookies.get(COOKIE)
    session = SESSIONS.get(sid) if sid else None
    user = session.get("user_name") if session else None
    is_enabled, provider, login_url = _active_provider()
    return AuthStatus(
        enabled=is_enabled,
        signed_in=bool(user),
        user_name=user,
        may_approve=may_approve(user),
        provider=provider,
        login_url=login_url if is_enabled else None,
    )


@router.get("/auth/login", include_in_schema=False)
def login() -> RedirectResponse:
    if not enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Sign-in is not configured (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET unset).",
        )

    _prune_states()
    state = secrets.token_urlsafe(24)
    _STATES[state] = time.time()

    params = {
        "client_id": _client_id(),
        # No scopes requested. The default grant already returns the public profile, and
        # asking for more than the app uses is how consent screens teach people to click
        # through without reading.
        "scope": "",
        "state": state,
        "allow_signup": "false",
    }
    return RedirectResponse(f"{GITHUB_AUTHORIZE}?{urlencode(params)}")


@router.get("/auth/callback", include_in_schema=False)
def callback(code: str = "", state: str = "") -> RedirectResponse:
    if not enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is not configured.")

    # CSRF: the state must be one we issued, and single-use.
    _prune_states()
    if not state or _STATES.pop(state, None) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired sign-in state.")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No authorization code returned.")

    try:
        token_resp = httpx.post(
            GITHUB_TOKEN,
            data={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        access = token_resp.json().get("access_token")
        if not access:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "GitHub declined the code exchange.")

        user_resp = httpx.get(
            GITHUB_USER,
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            timeout=15.0,
        )
        login_name = user_resp.json().get("login")
    except httpx.RequestError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GitHub unreachable: {type(exc).__name__}"
        ) from exc

    if not login_name:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "GitHub returned no login.")

    sid = secrets.token_urlsafe(32)
    # The GitHub token is deliberately NOT stored. It grants nothing this app needs after
    # the identity is known, and holding credentials you do not use is how they leak.
    SESSIONS[sid] = {"user_name": login_name, "created": time.time()}

    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        COOKIE,
        sid,
        httponly=True,
        secure=True,
        samesite="lax",  # must be lax, not strict: the cookie is set on a cross-site redirect
        max_age=SESSION_TTL_S,
        path="/",
    )
    return resp


@router.post("/auth/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    sid = request.cookies.get(COOKIE)
    if sid:
        SESSIONS.pop(sid, None)
    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE, path="/")
    return resp


def _prune_states() -> None:
    cutoff = time.time() - _STATE_TTL_S
    for k in [k for k, t in _STATES.items() if t < cutoff]:
        _STATES.pop(k, None)
