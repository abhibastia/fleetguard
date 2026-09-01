"""The single place any depot or VIN restriction is expressed.

Same discipline as the auth seam (E-13): the *enforcement mechanism* will change, so the
decision lives in one module rather than spread across handlers.

**Where enforcement stands today.** Measured 2026-09-01: Databricks identities exist as
Postgres login roles, connections resolve `current_user` to the caller's own email, and
`row_security` is `on`. So Postgres RLS can enforce depot scoping *below* the application —
which is what keeps §5.1's "the frontend cannot bypass it" literally true once the policies
are written (Phase 10).

Until those policies exist, scoping is **application-enforced** by the predicates below.
That is stated plainly rather than implied: an unwritten RLS policy provides no protection,
and a document claiming otherwise would be the kind of unfalsifiable assertion this project
has spent weeks removing.

`ScopeMode.FLEET_WIDE` is not an absence of governance — it is the reliability-analyst view
from §2, which is deliberately fleet-wide *with VINs masked*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .auth.tokens import Principal


class ScopeMode(StrEnum):
    DEPOT = "depot"  # depot manager — own depot only, VINs in the clear
    FLEET_WIDE = "fleet_wide"  # analyst — whole fleet, VINs masked
    FULL = "full"  # safety manager — whole fleet, VINs in the clear


@dataclass(frozen=True)
class Scope:
    """A scoping decision, rendered as a SQL fragment plus bound parameters.

    Returning a fragment and params — never an interpolated string — keeps callers from
    building SQL by concatenation, which is how a scoping predicate becomes an injection
    point.
    """

    mode: ScopeMode
    predicate: str
    params: dict
    mask_vin: bool

    def where(self, prefix: str = "WHERE") -> str:
        return f"{prefix} {self.predicate}" if self.predicate else ""


def resolve_scope(principal: Principal, depot_id: str | None = None) -> Scope:
    """Decide what this caller may see.

    MVP: every authenticated caller gets `FULL`, because no role mapping exists yet and
    inventing one would be theatre — a fake authorisation layer is worse than an honest
    absence of one. `depot_id` narrows voluntarily (the depot-manager view) without
    implying it is enforced.

    When Phase 10 lands, this function reads the caller's group membership and the
    predicates below become defence-in-depth behind RLS rather than the only control.
    """
    if depot_id:
        return Scope(
            mode=ScopeMode.DEPOT,
            predicate="v.depot_id = %(depot_id)s",
            params={"depot_id": depot_id},
            mask_vin=False,
        )
    return Scope(mode=ScopeMode.FULL, predicate="", params={}, mask_vin=False)


def mask_vin(vin: str | None, *, mask: bool) -> str | None:
    """Show the last 6 of a 17-char VIN — enough to identify a unit on a depot sheet,
    not enough to look up an owner. Applied at the edge; when RLS lands, the column mask
    does this in Postgres and this becomes belt-and-braces.
    """
    if not vin or not mask:
        return vin
    return f"{'*' * 11}{vin[-6:]}" if len(vin) >= 6 else "*" * len(vin)
