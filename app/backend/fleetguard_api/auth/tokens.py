"""The auth seam (E-13).

FleetGuard runs on two surfaces that differ in **exactly one** way: how the caller's
Databricks token arrives.

    Render (§8.7 phase 1)   U2M OAuth redirect — our code obtains and stores the token
    Databricks Apps         `X-Forwarded-Access-Token` header — the platform supplies it

Everything downstream is identical: the SQL, the Lakebase calls, the agent invocation, and
crucially the Unity Catalog ABAC evaluation that §5.1 depends on. So the difference is
confined to this module, behind one protocol.

**No route handler may read a header, cookie or session directly.** That rule is the whole
point: with the seam, the September move to Databricks Apps is a config change; without it,
the change is spread across every handler and lands in the week before the demo, on the code
path carrying every authorisation guarantee in §5.

This module is deliberately free of FastAPI and Databricks imports so it can be unit-tested
off-platform, like `fleetguard.vin` and `fleetguard.chunking`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

# Matches auth_routes.SESSION_TTL_S — the cookie's own max_age. Kept as a plain constant
# rather than imported from auth_routes because this module is deliberately free of FastAPI
# imports (see the module docstring); auth_routes imports FROM here, not the reverse.
DEFAULT_SESSION_TTL_S = 8 * 3600

# Lowercase by convention; HTTP headers are case-insensitive and every ASGI server this
# runs behind normalises to lowercase before we see them.
FORWARDED_TOKEN_HEADER = "x-forwarded-access-token"


class AuthError(Exception):
    """No usable caller identity. Always a 401 — never fall back to a service principal.

    Falling back would silently execute a user's request under a principal with broader
    grants, which is precisely the bypass §5.1 promises cannot happen.
    """


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    `token` is a Databricks OAuth token belonging to the *user*, not to the app. Every
    downstream query runs with it so Unity Catalog evaluates row filters and column masks
    under the user's own identity.
    """

    token: str
    user_name: str | None = None
    source: str = "unknown"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # Never let a token reach a log, a traceback, or an error page.
        return f"Principal(user_name={self.user_name!r}, source={self.source!r}, token=<redacted>)"


class TokenProvider(Protocol):
    """Resolves the caller's Databricks token from a request's headers.

    Takes a plain mapping rather than a framework request object so implementations stay
    testable and the seam does not leak FastAPI into the rest of the application.
    """

    def resolve(self, headers: dict[str, str]) -> Principal: ...


class ForwardedHeaderTokenProvider:
    """Databricks Apps: the platform injects the user's token as a request header.

    Used only when the app is running inside Databricks Apps ingress. The header is
    trustworthy *because* of that ingress — which is exactly why this provider must never be
    selected on Render, where any client could set it.
    """

    def __init__(self, header: str = FORWARDED_TOKEN_HEADER) -> None:
        self._header = header.lower()

    def resolve(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        token = (lowered.get(self._header) or "").strip()
        if not token:
            raise AuthError(
                f"missing {self._header}. This provider is only valid behind Databricks "
                "Apps ingress; on Render use the U2M provider."
            )
        return Principal(
            token=token,
            user_name=lowered.get("x-forwarded-email") or lowered.get("x-forwarded-user"),
            source="databricks-apps",
        )


class SessionTokenProvider:
    """Render: the token came from the U2M OAuth code exchange (Path D, §8.7).

    The session store is injected rather than imported so this stays testable and so the
    storage decision (cookie, Redis, signed JWT) can change without touching the seam.
    """

    def __init__(
        self,
        session_lookup,
        cookie_name: str = "fg_session",
        session_ttl_s: float = DEFAULT_SESSION_TTL_S,
        now_fn=time.time,
    ) -> None:
        self._lookup = session_lookup
        self._cookie_name = cookie_name
        self._ttl_s = session_ttl_s
        self._now = now_fn

    def resolve(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        session_id = _cookie_value(lowered.get("cookie", ""), self._cookie_name)
        if not session_id:
            raise AuthError("no session cookie; the caller must complete the U2M OAuth flow")

        session = self._lookup(session_id)
        if not session or not session.get("access_token"):
            raise AuthError("session unknown or expired; re-authenticate")
        # Server-side expiry, not just the cookie's client-side max_age. Without this, a
        # session outlives the browser's willingness to send it — the cookie's `max_age` is
        # a client-side courtesy, not an access control (I-062: found in the 2026-09-02 repo
        # review; a captured or replayed session value was honoured by the server forever).
        _check_not_expired(session, self._ttl_s, self._now)

        return Principal(
            token=session["access_token"],
            user_name=session.get("user_name"),
            source="render-u2m",
        )


class AppLoginTokenProvider:
    """Render with an app-owned login: identity without a Databricks token.

    This is the one provider whose `Principal` carries **no** Databricks credential, because
    on this deployment none can exist — service-principal creation is admin-only, personal
    access tokens are disabled for this account, and Lakebase roles are all OAuth-based
    (measured 2026-09-02). The app authenticates *who you are*; the data behind it comes from
    a committed snapshot.

    That makes an invariant load-bearing: **this provider is only valid alongside
    `FLEETGUARD_DATA_MODE=snapshot`.** `build_token_provider` refuses otherwise rather than
    handing a Lakebase query an empty token and letting it fail somewhere less obvious.
    """

    def __init__(
        self,
        session_lookup,
        cookie_name: str = "fg_session",
        session_ttl_s: float = DEFAULT_SESSION_TTL_S,
        now_fn=time.time,
    ) -> None:
        self._lookup = session_lookup
        self._cookie_name = cookie_name
        self._ttl_s = session_ttl_s
        self._now = now_fn

    def resolve(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        session_id = _cookie_value(lowered.get("cookie", ""), self._cookie_name)
        if not session_id:
            raise AuthError("sign in to continue")

        session = self._lookup(session_id)
        if not session or not session.get("user_name"):
            raise AuthError("session unknown or expired; sign in again")
        # See SessionTokenProvider — same server-side expiry gap, same fix (I-062). This is
        # the provider Render actually runs, so this check is the one that matters live.
        _check_not_expired(session, self._ttl_s, self._now)

        return Principal(
            token="",  # deliberate: there is no Databricks credential on this surface
            user_name=session["user_name"],
            source="app-login",
        )


class StaticTokenProvider:
    """Local development only — a token supplied by the developer.

    Exists so the MVP can proceed if U2M slips (see the cut order in ENHANCEMENTS.md): the
    *implementation* may be cut, the *indirection* may not. Refuses to run unless explicitly
    enabled, so it cannot be reached by accident in a deployed environment.
    """

    def __init__(self, token: str, user_name: str | None = None) -> None:
        if not token:
            raise AuthError("StaticTokenProvider requires a token")
        self._principal = Principal(token=token, user_name=user_name, source="static-dev")

    def resolve(self, headers: dict[str, str]) -> Principal:  # noqa: ARG002 - by design
        return self._principal


def _check_not_expired(session: dict, ttl_s: float, now_fn) -> None:
    """Enforce server-side session lifetime — the cookie's `max_age` only controls when the
    browser stops sending it, not how long the server honours it.

    Fails closed on a session with no `created` timestamp, rather than treating unknown age
    as valid. Every session this codebase creates (`routers/auth_routes.py::callback`)
    stamps `created` at creation; a session missing it did not come from that path, and
    trusting it indefinitely would be exactly the "fall back to a broader principal" this
    module's own rules forbid.
    """
    created = session.get("created")
    if created is None or (now_fn() - created) > ttl_s:
        raise AuthError("session expired; sign in again")


def _cookie_value(cookie_header: str, name: str) -> str | None:
    """Minimal cookie parse — avoids a dependency for one header."""
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value or None
    return None


def build_token_provider(env: dict[str, str] | None = None, session_lookup=None) -> TokenProvider:
    """Select the provider for this deployment. The only place the choice is made.

    `FLEETGUARD_AUTH_MODE` is read explicitly rather than inferred from the presence of a
    Databricks environment variable: inference would mean a misconfiguration silently
    selects the wrong trust model, and the forwarded-header provider is only safe behind
    Databricks Apps ingress.
    """
    env = os.environ if env is None else env
    mode = (env.get("FLEETGUARD_AUTH_MODE") or "").strip().lower()

    if mode == "databricks-apps":
        return ForwardedHeaderTokenProvider()
    if mode == "render-u2m":
        if session_lookup is None:
            raise AuthError("render-u2m mode requires a session_lookup")
        return SessionTokenProvider(session_lookup)
    if mode == "app-login":
        if session_lookup is None:
            raise AuthError("app-login mode requires a session_lookup")
        # The invariant that makes a token-less Principal safe. Checked here, at construction,
        # so a misconfigured deployment fails at startup rather than on the first query.
        if (env.get("FLEETGUARD_DATA_MODE") or "").strip().lower() != "snapshot":
            raise AuthError(
                "app-login mode requires FLEETGUARD_DATA_MODE=snapshot — it issues no "
                "Databricks credential, so live Lakebase reads cannot work behind it."
            )
        return AppLoginTokenProvider(session_lookup)
    if mode == "static-dev":
        token = env.get("FLEETGUARD_DEV_TOKEN", "")
        if not token:
            raise AuthError("static-dev mode requires FLEETGUARD_DEV_TOKEN")
        return StaticTokenProvider(token, env.get("FLEETGUARD_DEV_USER"))

    raise AuthError(
        "FLEETGUARD_AUTH_MODE must be one of: databricks-apps, app-login, render-u2m, "
        "static-dev. "
        "It is required rather than defaulted — an unset value must not silently pick a "
        "trust model."
    )
