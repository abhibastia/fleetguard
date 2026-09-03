"""Databricks U2M OAuth (PKCE) — the mechanics for the `render-u2m` login (Path D, revived).

Companion to `tokens.py`'s `SessionTokenProvider`, which already expects exactly the session
shape this module produces: `{"access_token", "user_name", "created"}`. That provider is
built and unit-tested (`tests/test_auth_seam.py`); what was missing was a way to *populate*
a session with a real Databricks token from a browser login, which is what this module and
`routers/databricks_auth_routes.py` add.

Deliberately framework-free (no FastAPI import) so the OAuth mechanics — PKCE generation,
the authorize URL, the token exchange, refresh — are unit-testable without a server, same
rationale as `tokens.py`'s module docstring.

**Needs an account-admin action this project does not yet have**: a custom OAuth app
integration registered in the Databricks *account* console (`databricks account
custom-app-integration create`, or Account Console -> App connections), which needs
account-admin rights (CLAUDE.md, "Databricks U2M OAuth"). `ENHANCEMENTS.md` E-14 retired
this path for that reason and because Databricks Apps OBO is the stronger story when it's
available. It is being revived here as the live-data path for Render specifically, for a
window where Databricks Apps is not being used — the registration is still outstanding.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTHORIZE_PATH = "/oidc/v1/authorize"
TOKEN_PATH = "/oidc/v1/token"

# CLAUDE.md ("Databricks U2M OAuth"): documented supported scopes for a custom app
# integration are all-apis, sql, offline_access, openid, profile, email. Narrower scopes
# than all-apis are explicitly unconfirmed on this endpoint, so all-apis is the working
# default rather than an attempt to scope down. offline_access is what makes silent
# refresh possible — without it there is no refresh_token and the session dies with the
# 1-hour access token regardless of the cookie's own TTL.
SCOPES = "all-apis offline_access"

# Documented access-token lifetime is 1 hour. Refresh this many seconds early so a request
# already in flight never straddles the boundary.
REFRESH_SKEW_S = 120


class OAuthError(Exception):
    """The Databricks OIDC exchange failed. Callers treat this as 'not signed in', never as
    a reason to fall back to a broader or cached credential."""


def _client_id() -> str:
    return os.getenv("DATABRICKS_CLIENT_ID", "")


def _client_secret() -> str:
    return os.getenv("DATABRICKS_CLIENT_SECRET", "")


def _host() -> str:
    host = os.getenv("DATABRICKS_HOST", "").rstrip("/")
    if not host:
        raise OAuthError("DATABRICKS_HOST is required for the Databricks OAuth login")
    return host


def enabled() -> bool:
    """Whether this deployment has what it needs to attempt the flow at all. Does not mean
    the account-registered OAuth app exists — that can only be found out by trying."""
    return bool(_client_id() and _client_secret() and os.getenv("DATABRICKS_HOST"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge), S256. PKCE is mandatory on this endpoint per
    Databricks' own documented example, not an optional hardening."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(*, redirect_uri: str, state: str, code_challenge: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{_host()}{AUTHORIZE_PATH}?{urlencode(params)}"


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float


def _post_token(data: dict) -> TokenSet:
    try:
        resp = httpx.post(
            f"{_host()}{TOKEN_PATH}",
            data=data,
            # Confidential-client authentication (CLAUDE.md: "Register as a confidential
            # client ... gets a client secret"). HTTP Basic is the standard OAuth2
            # mechanism for this and is independent of the body params the docs list.
            auth=(_client_id(), _client_secret()),
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
    except httpx.RequestError as exc:
        raise OAuthError(f"Databricks token endpoint unreachable: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        # Never echo the response body: it can carry the code/verifier back in an error
        # description, and this text can reach a client via HTTPException.
        raise OAuthError(f"Databricks token endpoint returned {resp.status_code}")

    body = resp.json()
    access = body.get("access_token")
    if not access:
        raise OAuthError("Databricks token endpoint returned no access_token")

    expires_in = float(body.get("expires_in") or 3600)
    return TokenSet(
        access_token=access,
        refresh_token=body.get("refresh_token"),
        expires_at=time.time() + expires_in,
    )


def exchange_code(*, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": _client_id(),
        }
    )


def refresh(refresh_token: str) -> TokenSet:
    """CLAUDE.md flags the exact refresh-grant shape as **not explicitly confirmed** for
    this endpoint — standard OAuth2 shape, empirically untested here. A failure surfaces as
    `OAuthError`, which `resolve()` turns into 'session gone, sign in again' rather than a
    crash or a stale-token retry loop."""
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _client_id(),
        }
    )


def current_user_name(access_token: str) -> str:
    """Identity behind the token, via the SDK already used for Lakebase credentials
    (`db.py`) rather than hand-rolling a SCIM call for the same information."""
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=_host(), token=access_token).current_user.me().user_name


def resolve(session_id: str, sessions: dict) -> dict | None:
    """`session_lookup` for `FLEETGUARD_AUTH_MODE=render-u2m`.

    Refreshes the stored Databricks token in place when it is close to expiry, then returns
    the (possibly updated) session dict for `SessionTokenProvider` to read `access_token`
    from — unchanged from how it already reads a GitHub-populated session in `app-login`.

    Refresh failure returns `None` (fails closed to "no session") rather than handing back a
    token Databricks is about to reject, or silently keeping the caller signed in on a dead
    credential — consistent with this codebase's rule that failure is always an error, never
    a fallback to a broader or stale principal (tokens.py's module docstring).
    """
    session = sessions.get(session_id)
    if not session:
        return None

    expires_at = session.get("expires_at")
    if expires_at is not None and (expires_at - time.time()) < REFRESH_SKEW_S:
        token = session.get("refresh_token")
        if not token:
            return None
        try:
            fresh = refresh(token)
        except OAuthError:
            return None
        session["access_token"] = fresh.access_token
        session["refresh_token"] = fresh.refresh_token or token
        session["expires_at"] = fresh.expires_at
        sessions[session_id] = session

    return session
