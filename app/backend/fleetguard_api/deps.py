"""FastAPI wiring for the auth seam.

This is the *only* module that turns a FastAPI request into a `Principal`. Handlers depend
on `CurrentPrincipal`; they never touch `request.headers` themselves (E-13).
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from .auth import databricks_oauth
from .auth.tokens import (
    AuthError,
    Principal,
    TokenProvider,
    build_token_provider,
    session_is_live,
)

# In-memory session store for the Render U2M flow. Deliberately trivial for MVP; swapping it
# for Lakebase-backed sessions touches only this dict and the lookup below, because the seam
# takes `session_lookup` as an injected callable.
_SESSIONS: dict[str, dict] = {}

# Public alias: the auth routes write sessions, this module reads them. One dict, one owner.
# In-memory is adequate for a single Render instance — a restart signs everyone out, which is
# an acceptable cost for a demo surface and is documented rather than pretended away.
SESSIONS = _SESSIONS


def session_lookup(session_id: str) -> dict | None:
    return _SESSIONS.get(session_id)


def prune_sessions(now_fn=time.time) -> int:
    """Drop sessions past their server-side TTL. Returns how many were removed.

    Rejecting an expired session on read (`tokens.session_is_live`) keeps it from being
    *honoured*, but does not remove it — so without this the dict only ever grows, and a
    session nobody explicitly logs out of (the common case: people close the tab) stays
    resident forever. I-062 named this unbounded-growth half of the problem explicitly; the
    fix at the time only covered the access-control half, so it is closed here (I-071).

    Called from `auth_status`, which the console hits on mount: frequent enough to keep the
    dict bounded, and well off the hot path of every data request.

    `list(...)` around `.items()` is load-bearing, not style. FastAPI runs sync endpoints in
    a threadpool, so a login completing on another worker can insert into `_SESSIONS` while
    this iterates — which raises `RuntimeError: dictionary changed size during iteration`.
    Snapshotting the items first makes the read atomic with respect to that; `pop(..., None)`
    then tolerates a key another thread removed first (a concurrent logout).
    """
    stale = [sid for sid, s in list(_SESSIONS.items()) if not session_is_live(s, now_fn=now_fn)]
    for sid in stale:
        _SESSIONS.pop(sid, None)
    return len(stale)


def _session_lookup_for(mode: str):
    """`render-u2m` sessions hold a real Databricks token that expires in ~1 hour — far
    inside the 8-hour session cookie's lifetime — so that mode's lookup must refresh it in
    place before `SessionTokenProvider` reads `access_token`. Every other mode's session
    never expires independently of the cookie, so the plain dict lookup is enough.
    """
    if mode == "render-u2m":
        return lambda session_id: databricks_oauth.resolve(session_id, _SESSIONS)
    return session_lookup


@lru_cache(maxsize=1)
def get_token_provider() -> TokenProvider:
    """Built once per process. Cached because the choice cannot change at runtime."""
    mode = (os.environ.get("FLEETGUARD_AUTH_MODE") or "").strip().lower()
    return build_token_provider(dict(os.environ), session_lookup=_session_lookup_for(mode))


def current_principal(request: Request) -> Principal:
    """Resolve the caller. 401 on failure — never a service-principal fallback.

    A fallback would run a user's request under broader grants than they hold, which is the
    exact bypass §5.1 promises is impossible. Failing closed is the only correct behaviour.
    """
    try:
        return get_token_provider().resolve(dict(request.headers))
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
