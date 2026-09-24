"""Unit tests for scoping.py — "the single place any depot or VIN restriction is
expressed", by its own docstring, and untested until this review.

`resolve_scope` and `mask_vin` are pure functions with no I/O, so every branch is cheap to
pin here rather than trusted to manual testing against live Lakebase.
"""

import pytest
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.scoping import (
    Scope,
    ScopeMode,
    authorized_depots,
    mask_vin,
    resolve_scope,
)

PRINCIPAL = Principal(token="t", user_name="a@b.com", source="test")


class TestResolveScope:
    def test_no_depot_id_is_full_scope(self):
        scope = resolve_scope(PRINCIPAL, None)
        assert scope.mode == ScopeMode.FULL
        assert scope.predicate == ""
        assert scope.params == {}
        assert scope.mask_vin is False

    def test_empty_string_depot_id_is_also_full_scope(self):
        # Falsy-but-present input (an empty query param) must behave like "not given", not
        # like a request to scope to the depot named "".
        scope = resolve_scope(PRINCIPAL, "")
        assert scope.mode == ScopeMode.FULL

    def test_depot_id_narrows_to_depot_scope(self):
        scope = resolve_scope(PRINCIPAL, "DEP-041")
        assert scope.mode == ScopeMode.DEPOT
        assert scope.predicate == "v.depot_id = %(depot_id)s"
        assert scope.params == {"depot_id": "DEP-041"}
        assert scope.mask_vin is False

    def test_depot_id_is_bound_as_a_parameter_never_interpolated(self):
        # The whole reason Scope carries params separately from predicate: a depot_id
        # containing SQL syntax must end up as a bound value, never inside the fragment
        # string itself. If a future edit f-strings depot_id into the predicate, this fails.
        hostile = "x'; DROP TABLE fleetguard_vehicle; --"
        scope = resolve_scope(PRINCIPAL, hostile)
        assert hostile not in scope.predicate
        assert scope.params["depot_id"] == hostile


class TestScopeWhere:
    def test_empty_predicate_produces_no_where_clause(self):
        # A FULL scope's predicate is "" — where() must not emit a bare "WHERE" with
        # nothing after it, which every SQL engine rejects as a syntax error.
        scope = Scope(mode=ScopeMode.FULL, predicate="", params={}, mask_vin=False)
        assert scope.where() == ""
        assert scope.where("AND") == ""

    def test_nonempty_predicate_gets_the_default_where_prefix(self):
        scope = Scope(
            mode=ScopeMode.DEPOT, predicate="v.depot_id = %(d)s", params={}, mask_vin=False
        )
        assert scope.where() == "WHERE v.depot_id = %(d)s"

    def test_nonempty_predicate_with_custom_prefix(self):
        # The shape routers/queue.py actually uses: appending onto an existing WHERE clause.
        scope = Scope(
            mode=ScopeMode.DEPOT, predicate="v.depot_id = %(d)s", params={}, mask_vin=False
        )
        assert scope.where("AND") == "AND v.depot_id = %(d)s"


class TestMaskVin:
    def test_unmasked_passthrough(self):
        assert mask_vin("1FTEW1EG2GK123456", mask=False) == "1FTEW1EG2GK123456"

    def test_masks_all_but_the_last_six(self):
        masked = mask_vin("1FTEW1EG2GK123456", mask=True)
        assert masked == "***********123456"
        assert masked.endswith("123456")
        assert len(masked) == len("1FTEW1EG2GK123456")

    def test_none_passes_through_regardless_of_mask_flag(self):
        # A masked query result can legitimately have no VIN (e.g. a join miss); masking
        # None must not raise or produce a string of asterisks standing in for "no VIN".
        assert mask_vin(None, mask=True) is None
        assert mask_vin(None, mask=False) is None

    @pytest.mark.parametrize("short", ["ABCDE", "ABC", "A", ""])
    def test_short_or_empty_vin_masks_to_all_asterisks(self, short):
        # len < 6: there is no "last 6" to preserve without exposing the whole thing, so the
        # function falls back to masking entirely rather than accidentally showing everything.
        result = mask_vin(short, mask=True)
        if short:
            assert result == "*" * len(short)
        else:
            # Empty string is falsy — same passthrough branch as None, by design (matches
            # `if not vin or not mask: return vin`).
            assert result == ""

    def test_exactly_six_chars_still_gets_the_full_eleven_asterisk_prefix(self):
        # Boundary: len(vin) == 6 is the shortest input that takes the "11 asterisks + last
        # 6" branch (>= 6, not > 6), so it gets the SAME fixed 11-asterisk prefix as a real
        # 17-char VIN — the function does not scale the prefix to the input's length. Worth
        # pinning explicitly: it's easy to assume >= 6 means "show whatever's left unmasked".
        assert mask_vin("ABCDEF", mask=True) == "***********ABCDEF"

    def test_five_chars_takes_the_all_asterisk_branch_not_the_prefix_branch(self):
        # One below the boundary: len < 6 means there is no "last six" to speak of, so the
        # ENTIRE string is masked instead — not the 11-asterisk prefix applied to a
        # nonsensical negative slice.
        assert mask_vin("ABCDE", mask=True) == "*****"


class TestDepotContainment:
    """I-117. `depot_id` arrives from the CLIENT — `GET /work-orders?depot_id=DEP-042` — and
    `resolve_scope` turned it straight into `WHERE depot_id = :depot_id`. That is *filtering*,
    not authorisation: the predicate narrows to what was asked for, never checks whether the
    caller may ask.

    It is not exploitable today, and these tests say so rather than pretending otherwise:
    `authorized_depots` returns `None`, everyone is unrestricted, and the Postgres RLS policy
    is deliberately fail-open with nobody enrolled. The check exists because the day someone
    IS enrolled is exactly the day nobody re-reads this function.
    """

    def test_nobody_is_enrolled_today_so_nothing_is_denied(self):
        """The honest current state, pinned. If this ever starts failing, enrollment happened
        — and every assumption in the surrounding docs needs re-reading, not this test
        loosening."""
        assert authorized_depots(PRINCIPAL) is None
        scope = resolve_scope(PRINCIPAL, "DEP-042")
        assert scope.mode is ScopeMode.DEPOT
        assert scope.params == {"depot_id": "DEP-042"}

    def test_an_unauthorized_depot_is_a_403_once_a_set_exists(self):
        with pytest.raises(HTTPException) as exc:
            resolve_scope(PRINCIPAL, "DEP-042", authorized=frozenset({"DEP-001"}))
        assert exc.value.status_code == 403
        assert "DEP-042" in exc.value.detail

    def test_an_authorized_depot_still_resolves(self):
        scope = resolve_scope(PRINCIPAL, "DEP-001", authorized=frozenset({"DEP-001", "DEP-002"}))
        assert scope.mode is ScopeMode.DEPOT
        assert scope.params == {"depot_id": "DEP-001"}

    def test_an_empty_authorized_set_denies_rather_than_waving_through(self):
        """The failure mode worth naming: `frozenset()` is falsy, so a truthiness test here
        would read "no restrictions" and allow everything — turning the most restrictive input
        into the least. The check is `is not None`, deliberately."""
        with pytest.raises(HTTPException) as exc:
            resolve_scope(PRINCIPAL, "DEP-001", authorized=frozenset())
        assert exc.value.status_code == 403

    def test_explicit_none_means_unrestricted_and_is_not_the_same_as_omitting_it(self):
        """`None` (unrestricted) and "not passed" must stay distinguishable — hence the
        `_UNSET` sentinel. If the default were `None`, a caller that resolved the set and got
        `None` would be indistinguishable from one that never looked."""
        assert resolve_scope(PRINCIPAL, "DEP-042", authorized=None).mode is ScopeMode.DEPOT

    def test_no_requested_depot_is_never_denied(self):
        """Fleet-wide reads are not depot requests and must not be caught by this check."""
        assert resolve_scope(PRINCIPAL, None, authorized=frozenset()).mode is ScopeMode.FULL
