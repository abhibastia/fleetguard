"""What the console needs to know about the caller before it draws anything.

Both supported surfaces authenticate *outside* this application — Databricks Apps at its
ingress, the local dev server from a token in the environment — so there is no login flow
here, and no session to create or destroy. What remains is the one question the console asks
on mount: who am I, and may I approve?

**Two tiers, deliberately.** Anyone the seam resolves can *read*. Only identities in
`FLEETGUARD_APPROVERS` can approve (`authz.may_approve`). Being authenticated proves you are
someone; it does not prove you should be able to dispatch work orders against a fleet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..auth.tokens import AuthError
from ..authz import may_approve
from ..deps import get_token_provider

router = APIRouter(tags=["auth"])


class AuthStatus(BaseModel):
    signed_in: bool
    user_name: str | None
    may_approve: bool


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    """Unauthenticated by design: 200 with `signed_in: false`, never a 401.

    The Evidence page carries the measured early-warning result and needs no identity, so
    the console must be able to ask this question before it has one. That is the whole
    reason this endpoint exists rather than the console reading `/api/me`, which *does* 401.

    Derived from the resolved principal rather than from a session. It used to read a cookie
    store directly, which is how it drifted out of step with the token providers when
    server-side session expiry was added (I-071): the console drew a signed-in header, and
    even a "you may approve" state, while every data request 401'd underneath it. With no
    session store left, one resolution path answers both this and every real request, so the
    two cannot disagree.
    """
    try:
        principal = get_token_provider().resolve(dict(request.headers))
    except AuthError:
        return AuthStatus(signed_in=False, user_name=None, may_approve=False)

    return AuthStatus(
        signed_in=True,
        user_name=principal.user_name,
        may_approve=may_approve(principal.user_name),
    )
