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

import logging
import os
import threading
import time
from dataclasses import dataclass

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


@dataclass(frozen=True)
class _CachedCredential:
    token: str
    host: str
    user: str
    minted_at: float

    @property
    def stale(self) -> bool:
        return (time.time() - self.minted_at) > _CREDENTIAL_TTL_S


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
    return WorkspaceClient(host=host, token=principal.token)


def _credential(principal: Principal) -> _CachedCredential:
    key = principal.user_name or principal.token[-16:]
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
        sslmode="require",
        autocommit=autocommit,
        connect_timeout=15,
    )


def rows_to_dicts(cursor) -> list[dict]:
    """psycopg returns tuples; the API returns objects."""
    cols = [d.name for d in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]
