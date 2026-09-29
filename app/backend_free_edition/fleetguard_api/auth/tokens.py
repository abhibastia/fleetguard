"""The auth seam (E-13).

FleetGuard runs on two surfaces that differ in **exactly one** way: how the caller's
Databricks token arrives.

    Databricks Apps    `X-Forwarded-Access-Token` header — the platform supplies it
    Local server       a token the developer supplies in the environment

Everything downstream is identical: the SQL, the Lakebase calls, the agent invocation, and
crucially the Unity Catalog ABAC evaluation that §5.1 depends on. So the difference is
confined to this module, behind one protocol.

**No route handler may read a header, cookie or session directly.** That rule is the whole
point: with the seam, moving between surfaces is a config change; without it, the change is
spread across every handler and lands on the code path carrying every authorisation guarantee
in §5.

The seam once carried two more providers — a cookie-session provider for a Databricks U2M
OAuth flow this app ran itself, and an app-owned GitHub login that issued no Databricks
credential at all. Both existed only to host the console outside Databricks Apps ingress, on
Render; both were removed with Render on 2026-09-10 and are preserved on the `deploy/render`
branch. That is why neither surviving mode has any notion of a session: on Apps the ingress
authenticates, and locally the developer does.

This module is deliberately free of FastAPI and Databricks imports so it can be unit-tested
off-platform, like `fleetguard.vin` and `fleetguard.chunking`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

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

    The header is trustworthy *because* of the Apps ingress — which is exactly why this
    provider must never be selected on a surface where any client could set it itself.
    """

    def __init__(self, header: str = FORWARDED_TOKEN_HEADER) -> None:
        self._header = header.lower()

    def resolve(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        token = (lowered.get(self._header) or "").strip()
        if not token:
            raise AuthError(
                f"missing {self._header}. This provider is only valid behind Databricks "
                "Apps ingress, which injects the header; nothing else may be trusted to."
            )
        return Principal(
            token=token,
            user_name=lowered.get("x-forwarded-email") or lowered.get("x-forwarded-user"),
            source="databricks-apps",
        )


class StaticTokenProvider:
    """Local development only — a token supplied by the developer.

    See `scripts/run_local_static_dev.sh`, which mints one from `databricks auth token`.
    The token is *static* by design: Databricks access tokens live one hour, so a long
    session ends with every Lakebase-backed endpoint returning 500s, and the fix is to
    restart the server rather than to add refresh logic here.

    Refuses to run unless explicitly enabled, so it cannot be reached by accident in a
    deployed environment.
    """

    def __init__(self, token: str, user_name: str | None = None) -> None:
        if not token:
            raise AuthError("StaticTokenProvider requires a token")
        self._principal = Principal(token=token, user_name=user_name, source="static-dev")

    def resolve(self, headers: dict[str, str]) -> Principal:  # noqa: ARG002 - by design
        return self._principal


def build_token_provider(env: dict[str, str] | None = None) -> TokenProvider:
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
    if mode == "static-dev":
        token = env.get("FLEETGUARD_DEV_TOKEN", "")
        if not token:
            raise AuthError("static-dev mode requires FLEETGUARD_DEV_TOKEN")
        return StaticTokenProvider(token, env.get("FLEETGUARD_DEV_USER"))

    raise AuthError(
        "FLEETGUARD_AUTH_MODE must be one of: databricks-apps, static-dev. "
        "It is required rather than defaulted — an unset value must not silently pick a "
        "trust model."
    )
