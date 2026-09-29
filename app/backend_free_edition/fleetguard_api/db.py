"""Lakebase access — one connection path, per-user identity.

FleetGuard's console is **transactional**: point lookups and small aggregations serving one
operator's session. That is Lakebase's job. Delta keeps the analytical surfaces (backtest
evidence, analyst patterns, Genie).

**Per-user identity is real here, not simulated.** `generate_database_credential` mints a
credential for *whoever calls it*, so calling it with the user's OBO token yields a
credential for that user. Measured 2026-09-01: 25 Databricks identities exist as Postgres
login roles, `current_user` resolves to the caller's own email, and `row_security` is `on`.
So depot scoping can be enforced by Postgres — §5.1's "the frontend cannot bypass it"
survives with the enforcement point moved from Unity Catalog to Postgres.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass

import certifi
from databricks.sdk import WorkspaceClient

from .auth.tokens import Principal

log = logging.getLogger(__name__)

PROJECT = os.getenv("FLEETGUARD_PG_PROJECT", "projects/summer-bootcamp-2026-v2")
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_DB = os.getenv("FLEETGUARD_PG_DATABASE", "databricks_postgres")
PG_SCHEMA = os.getenv("FLEETGUARD_PG_SCHEMA", "bootcamp_students")

# Databricks credentials last 60 minutes. Re-mint early so a request never starts with a
# credential that expires mid-transaction.
_CREDENTIAL_TTL_S = 45 * 60


def _select_psycopg_impl() -> None:
    """`psycopg[binary]` 3.3.5 aborts the kernel on Databricks serverless with a FIPS
    self-test failure (I-045). The pure-Python implementation avoids it by using the system
    libpq — but macOS has no system libpq, so forcing it locally raises ImportError.

    Hence: apply the workaround only where the problem exists.
    """
    if os.getenv("PSYCOPG_IMPL"):
        return  # caller has decided
    if os.getenv("DATABRICKS_RUNTIME_VERSION"):
        os.environ["PSYCOPG_IMPL"] = "python"


_select_psycopg_impl()
import psycopg  # noqa: E402 - must follow _select_psycopg_impl()

# Re-exported so routers can catch integrity errors *without* importing psycopg themselves.
# A bare `import psycopg` in a router is sorted into the third-party block, above the
# `from ..db import ...` line — so it would execute before `_select_psycopg_impl()` ever
# runs, silently defeating the I-045 workaround (psycopg[binary] aborts on Databricks
# serverless with a FIPS self-test failure). Importing the symbol from here makes the
# ordering guarantee impossible to get wrong at the call site.
UniqueViolation = psycopg.errors.UniqueViolation


@dataclass(frozen=True)
class _CachedCredential:
    token: str
    host: str
    user: str
    minted_at: float

    @property
    def stale(self) -> bool:
        return (time.time() - self.minted_at) > _CREDENTIAL_TTL_S


# Entries are never evicted, only treated as stale on read (see _CachedCredential.stale) —
# fine at current scale since token rotation is infrequent, but unbounded if that changes.
# Add a sweep rather than assume one exists.
_cache: dict[str, _CachedCredential] = {}
_lock = threading.Lock()


def _workspace_client(principal: Principal) -> WorkspaceClient:
    """A client acting as the *caller*, not as the app.

    This is what makes per-user Postgres identity work: the credential inherits the token
    used to request it.
    """
    host = os.getenv("DATABRICKS_HOST")
    if not host:
        raise RuntimeError("DATABRICKS_HOST is required to mint a Lakebase credential")
    # `auth_type="pat"` is required, not stylistic, and only matters on ONE of the three
    # surfaces — which is why it survived local and Render testing.
    #
    # Databricks Apps auto-injects DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET for the
    # app's own service principal. The SDK's Config then sees the ambient OAuth credentials
    # AND the token passed here, and refuses to guess:
    #   ValueError: validate: more than one authorization method configured: oauth and pat
    # Locally and on Render those variables do not exist, so the same call succeeds there.
    #
    # Pinning the strategy says what this function already claims in its docstring: use the
    # caller's token and nothing else. Falling back to the app's service principal would be
    # the worst possible resolution — every write would silently land as the app instead of
    # the human, and `opened_by` would stop meaning anything.
    return WorkspaceClient(host=host, token=principal.token, auth_type="pat")


def _credential(principal: Principal) -> _CachedCredential:
    # Keyed on a digest of the token itself, not just the claimed username. A cache hit must
    # prove possession of the same token that earned the entry — keying on user_name alone
    # meant any request carrying that username got a live Lakebase session on a hit without
    # _workspace_client (the call that actually validates the token) ever running.
    token_digest = hashlib.sha256(principal.token.encode()).hexdigest()
    key = f"{principal.user_name or ''}:{token_digest}"
    with _lock:
        hit = _cache.get(key)
        if hit and not hit.stale:
            return hit

    w = _workspace_client(principal)
    cred = _CachedCredential(
        token=w.postgres.generate_database_credential(endpoint=ENDPOINT).token,
        host=w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host,
        # The Postgres role name IS the Databricks username (verified: 25 such roles).
        user=principal.user_name or w.current_user.me().user_name,
        minted_at=time.time(),
    )
    with _lock:
        _cache[key] = cred
    log.info("minted Lakebase credential for %s", cred.user)
    return cred


def connect(principal: Principal, *, autocommit: bool = True):
    """Open a connection **as the calling user**.

    `autocommit=False` for the approval gate, which must write the service campaign, its
    work orders, and the audit row in one transaction — a partial approval is worse than a
    failed one.
    """
    cred = _credential(principal)
    return psycopg.connect(
        host=cred.host,
        user=cred.user,
        password=cred.token,
        dbname=PG_DB,
        sslmode="verify-full",
        # `sslrootcert="system"` was tried first and fails on this endpoint (measured
        # 2026-09-17: "SSL error: certificate verify failed" even though the endpoint's
        # Let's Encrypt chain verifies fine via `openssl s_client` and the system trust
        # store) — psycopg[binary]'s vendored libpq does not reliably consult the OS trust
        # store the way `openssl s_client` does. `certifi`'s bundled CA file verifies
        # correctly and is portable across the local macOS dev path and the Databricks Apps
        # runtime, unlike a platform-specific trust-store lookup.
        sslrootcert=certifi.where(),
        autocommit=autocommit,
        connect_timeout=15,
    )


def rows_to_dicts(cursor) -> list[dict]:
    """psycopg returns tuples; the API returns objects."""
    cols = [d.name for d in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]
