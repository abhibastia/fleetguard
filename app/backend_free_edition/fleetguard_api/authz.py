"""Who may approve — the write-path gate, applied on every surface.

Read access and write access are deliberately different questions. Anyone whose identity the
auth seam resolves can *read* the queue, the emerging signals and the evidence. Only the
identities named in `FLEETGUARD_APPROVERS` may approve a service campaign or edit a work
order. Being authenticated proves you are someone; it does not prove you should be able to
dispatch work orders against a fleet.

**The gate is unconditional on `principal.source`.** It used to be reachable only for one
login flow, which meant a principal carrying a real Databricks token skipped it entirely
(fixed 2026-09-04). `tests/test_approval_gate.py` and `tests/test_work_order_gate.py`
parametrize over every auth mode precisely so that cannot come back.

This module imports no FastAPI, so — like `auth/tokens.py` — it can be unit-tested
off-platform.
"""

from __future__ import annotations

import os


def approvers() -> set[str]:
    """Identities allowed to approve. Empty means nobody — read-only for everyone.

    These are Databricks usernames (emails) under both supported auth modes. The comparison
    is a case-insensitive string match against the authenticated principal, whatever produced
    it — which is why an address that matches no real identity is a silently dead entry, and
    why workspace admin does not bypass this gate.
    """
    raw = os.getenv("FLEETGUARD_APPROVERS", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def may_approve(user_name: str | None) -> bool:
    return bool(user_name) and user_name.lower() in approvers()
