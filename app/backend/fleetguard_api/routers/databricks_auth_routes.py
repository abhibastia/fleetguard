"""Sign-in for the live-data deployment — Databricks U2M OAuth (Path D, revived).

Companion to `auth_routes.py`'s GitHub flow, not a replacement for it. Exactly one is wired
up per deployment, selected by `FLEETGUARD_AUTH_MODE`:

  - `app-login`   -> GitHub (`auth_routes.py`) — identity only, pairs with a data snapshot.
  - `render-u2m`  -> Databricks (this module) — a real per-user Databricks token, live data.

Both write into the same `SESSIONS` store under the same `fg_session` cookie, so
`/auth/status` and `/auth/logout` in `auth_routes.py` work unmodified regardless of which
flow populated the session — they only ever look at `user_name` and `created`.

**Still blocked on an account-admin action.** This needs a custom OAuth app integration
registered in the Databricks account console (see `auth/databricks_oauth.py`'s module
docstring and `ENHANCEMENTS.md` E-14). Until `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET`
exist for a registered app, `dbx.enabled()` is False and this router 503s rather than
attempting a request that can only fail. Both routes also 503 whenever
`FLEETGUARD_AUTH_MODE` isn't `render-u2m` — credentials being configured is not the same as
this flow being the deployment's selected one (see `_mode_is_active`), which matters
specifically during the switch-over window where the credentials may land before the mode
does.
"""

from __future__ import annotations

import os
import secrets
import time

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from ..auth import databricks_oauth as dbx
from ..deps import SESSIONS

router = APIRouter(prefix="/auth/databricks", tags=["auth"])

COOKIE = "fg_session"
SESSION_TTL_S = 8 * 3600

# Short-lived CSRF state, holding the PKCE verifier the callback needs. In-memory is
# adequate for the same reason auth_routes.py's GitHub state store is: it lives seconds,
# and a Render restart mid-login costs the user one retry.
_STATES: dict[str, dict] = {}
_STATE_TTL_S = 600


def _mode_is_active() -> bool:
    """Credentials being present is not the same as this flow being the selected one.

    Without this check, setting DATABRICKS_CLIENT_ID/SECRET in Render ahead of flipping
    FLEETGUARD_AUTH_MODE would make this router reachable while `app-login` is still the
    active provider — a real Databricks login would succeed and write a session, but
    `AppLoginTokenProvider` reads only `user_name` from it, ignoring `access_token`
    entirely. That's a working side door into the app-login signed-in state (still
    token-less, still snapshot-gated, still can't approve) that has no reason to exist
    before this mode is the one actually selected.
    """
    return (os.getenv("FLEETGUARD_AUTH_MODE") or "").strip().lower() == "render-u2m"


def _redirect_uri() -> str:
    """Must exactly match the value registered against the OAuth app — Databricks does not
    fuzzy-match redirect URIs. Read from config rather than derived from the request so a
    proxy rewriting `Host` can't silently produce a URI the registration doesn't recognise.
    """
    base = os.getenv("FLEETGUARD_PUBLIC_URL", "").rstrip("/")
    if not base:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "FLEETGUARD_PUBLIC_URL is not set; the Databricks OAuth redirect_uri must match "
            "the account-registered value exactly.",
        )
    return f"{base}/api/auth/databricks/callback"


@router.get("/login", include_in_schema=False)
def login() -> RedirectResponse:
    if not _mode_is_active():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This deployment is not running FLEETGUARD_AUTH_MODE=render-u2m.",
        )
    if not dbx.enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Databricks sign-in is not configured (DATABRICKS_HOST / DATABRICKS_CLIENT_ID / "
            "DATABRICKS_CLIENT_SECRET unset).",
        )

    _prune_states()
    state = secrets.token_urlsafe(24)
    verifier, challenge = dbx.new_pkce_pair()
    _STATES[state] = {"verifier": verifier, "created": time.time()}

    url = dbx.authorize_url(redirect_uri=_redirect_uri(), state=state, code_challenge=challenge)
    return RedirectResponse(url)


@router.get("/callback", include_in_schema=False)
def callback(code: str = "", state: str = "") -> RedirectResponse:
    if not _mode_is_active():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This deployment is not running FLEETGUARD_AUTH_MODE=render-u2m.",
        )
    if not dbx.enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Databricks sign-in is not configured."
        )

    # CSRF: the state must be one we issued, and single-use — same rule as the GitHub flow.
    _prune_states()
    entry = _STATES.pop(state, None) if state else None
    if entry is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired sign-in state.")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No authorization code returned.")

    try:
        tokens = dbx.exchange_code(
            code=code, code_verifier=entry["verifier"], redirect_uri=_redirect_uri()
        )
        user_name = dbx.current_user_name(tokens.access_token)
    except dbx.OAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except Exception as exc:
        # The SDK call raises its own exception types on a bad/expired token; treat any
        # failure here the same way — a 401, never a session with no verified identity.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Databricks declined the token.") from exc

    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
        "user_name": user_name,
        "created": time.time(),
    }

    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        COOKIE,
        sid,
        httponly=True,
        secure=True,
        samesite="lax",  # must be lax, not strict: set on a cross-site redirect from Databricks
        max_age=SESSION_TTL_S,
        path="/",
    )
    return resp


def _prune_states() -> None:
    cutoff = time.time() - _STATE_TTL_S
    for k in [k for k, v in _STATES.items() if v["created"] < cutoff]:
        _STATES.pop(k, None)
