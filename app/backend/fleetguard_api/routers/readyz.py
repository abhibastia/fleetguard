"""Demo readiness — a different question from liveness, and the reason `/healthz` was not enough.

`/healthz` answers *is this process up?* It returns `ok` with the AI Search index deleted, the
agent endpoint stopped and Lakebase unreachable — which is **exactly** the state this system
sits in between the two online windows. A judge pointing a browser at it learns nothing about
whether the demo will work, and that gap is what I-115 filed.

So this route answers the question that actually matters before a demo: **would it work right
now, and if not, which part is down?** Four independent checks, each reporting its own status,
latency and a one-line detail. Every one of them is wrapped, because the whole value here is
telling someone *which* dependency failed — one raising must not hide the other three.

**None of these checks costs money, and that is a design constraint rather than a happy
accident.** The agent is checked with `serving_endpoints.get`, which reads state from the
control plane; calling `query()` instead would wake a scale-to-zero Small CPU container and
bill until it idled down again, turning a readiness probe into a recurring charge. The index is
checked the same way, with `get_index`. So `/readyz` is safe to poll on the live deployment.

**Authenticated, unlike `/healthz`.** The Lakebase check has to run under the *caller's* token:
this app holds no privileges of its own (§8a), `db.connect` mints a per-user credential, and a
readiness check that proved the *app's* access would be testing something the product does not
do. The cost is that this route cannot be a container probe — which is fine, `/healthz` is, and
it stays untouched.

**503 when anything is down.** A readiness endpoint that returns 200 with the index deleted is
the defect, not a convenience. The body is identical either way, so the status code and the
detail never disagree.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from .. import db
from ..deps import CurrentPrincipal
from .chat import AGENT_ENDPOINT

log = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

#: Overridable for the same reason `AGENT_ENDPOINT` is — a fork pointing at another schema
#: should not have to edit code to make readiness mean something.
SEARCH_INDEX = os.getenv(
    "FLEETGUARD_SEARCH_INDEX", "bootcamp_students.fleetguard.complaint_chunk_idx"
)

_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent
SNAPSHOTS = {"evidence": _SNAPSHOT_DIR / "evidence.json", "corpus": _SNAPSHOT_DIR / "corpus.json"}

OK = "ok"
DOWN = "down"


class Check(BaseModel):
    """One dependency's verdict.

    `detail` is always populated, including on success — "ready, 115,499 rows" is what makes
    this useful 20 minutes before a demo, where "ok" alone is not.
    """

    name: str
    status: str
    detail: str
    latency_ms: int


class Readiness(BaseModel):
    ready: bool
    checks: list[Check]


def _timed(name: str, fn) -> Check:
    """Run one check, never letting it escape.

    An unexpected exception is a `down`, not a 500: the caller asked which dependencies work,
    and "the readiness endpoint crashed" answers that question far worse than "lakebase: down,
    connection refused" does.
    """
    started = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - deliberate: any failure is a `down`, not a 500
        log.warning("readiness check %s failed: %s", name, exc)
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    return Check(
        name=name,
        status=OK if ok else DOWN,
        detail=detail,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _check_lakebase(principal) -> tuple[bool, str]:
    """`SELECT 1` as the caller — the same path every other route takes.

    Deliberately not a `COUNT(*)`: this proves the credential mints, the endpoint is awake and
    the role can execute, which is the whole dependency. Anything heavier would make a
    readiness probe pay for itself in warehouse time.
    """
    with db.connect(principal) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return True, f"connected as {principal.user_name or 'unknown'}"


def _check_agent(principal) -> tuple[bool, str]:
    """State only. See the module note — `query()` here would cost money on every poll."""
    w: WorkspaceClient = db._workspace_client(principal)
    ep = w.serving_endpoints.get(name=AGENT_ENDPOINT)
    state = ep.state
    ready = getattr(getattr(state, "ready", None), "value", None) or str(getattr(state, "ready", ""))
    config = getattr(getattr(state, "config_update", None), "value", None) or ""
    # `ready` is the only field that distinguishes serving from stopped. `scale_to_zero_enabled`
    # reads True in BOTH idle states and so distinguishes nothing (I-092) — it is not consulted.
    is_ready = ready == "READY"
    entity = ""
    served = ep.config.served_entities if ep.config else None
    if served:
        entity = f", serving v{served[0].entity_version}"
    return is_ready, f"{AGENT_ENDPOINT}: ready={ready or '?'}{entity}{f', {config}' if config else ''}"


def _check_index(principal) -> tuple[bool, str]:
    """Also state only — and the check most likely to be `down`, by design.

    Between the two online windows the index does not exist at all, and reporting that as
    healthy is precisely what `/healthz` did wrong.
    """
    w: WorkspaceClient = db._workspace_client(principal)
    st = w.vector_search_indexes.get_index(index_name=SEARCH_INDEX).status
    rows = getattr(st, "indexed_row_count", None)
    ready = bool(getattr(st, "ready", False))
    return ready, f"{SEARCH_INDEX}: ready={ready}, indexed_row_count={rows if rows is not None else '?'}"


def _check_snapshots() -> tuple[bool, str]:
    """The two public routes read committed files, so their readiness is a file check.

    Cheap, but not pointless: these are what an anonymous visitor sees, and a missing snapshot
    makes `/api/evidence` and `/api/corpus` return 503 while every authenticated route works —
    a failure that is invisible to anyone signed in.
    """
    import json

    missing = [name for name, path in SNAPSHOTS.items() if not path.exists()]
    if missing:
        return False, f"missing snapshot(s): {', '.join(sorted(missing))}"
    stamps = []
    for name, path in sorted(SNAPSHOTS.items()):
        generated = json.loads(path.read_text()).get("generated_at", "?")
        stamps.append(f"{name} {generated}")
    return True, "; ".join(stamps)


@router.get("/readyz", response_model=Readiness, tags=["ops"])
def readyz(principal: CurrentPrincipal, response: Response) -> Readiness:
    """Is the demo ready? 200 if every dependency is, 503 with the same body if not."""
    checks = [
        _timed("lakebase", lambda: _check_lakebase(principal)),
        _timed("agent_endpoint", lambda: _check_agent(principal)),
        _timed("search_index", lambda: _check_index(principal)),
        _timed("snapshots", _check_snapshots),
    ]
    ready = all(c.status == OK for c in checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(ready=ready, checks=checks)
