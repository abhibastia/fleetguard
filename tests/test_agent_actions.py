"""The agent's write action — envelope parsing, validation, and the executed INSERT.

`open_defect_signal` is the assistant's only write. The agent *requests* it (its serving
endpoint has no Lakebase path); `agent_actions.execute` performs it under the caller's own OBO
token. These tests cover the console half — the part that decides whether a write happens and
under whose name.

The threat model here is unusual and worth stating: **every field in an envelope originated in
LLM output.** So the tests below are less about "does the happy path work" and more about what
happens when the model emits something malformed, hostile, or simply absent.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fleetguard_api import agent_actions
from fleetguard_api.agent_actions import ACTION_SENTINEL, execute, parse_envelope
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.db import UniqueViolation

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")
ANON = Principal(token="tok", user_name=None, source="static-dev")

VALID = {
    "__fleetguard_action__": "open_defect_signal",
    "status": "REQUESTED",
    "params": {
        "component": "STEERING",
        "rationale": "31 complaints in 3 months, 12% alleging loss of control.",
        "make": "RAM",
        "model": "2500",
        "complaint_count": 31,
    },
}

VEHICLE_COUNT_Q = "FROM bootcamp_students.fleetguard_vehicle"
INSERT_SIGNAL = "INSERT INTO bootcamp_students.fleetguard_defect_signal"
INSERT_AUDIT = "INSERT INTO bootcamp_students.fleetguard_audit_log"
INSERT_AGENT_ACTION = "INSERT INTO bootcamp_students.fleetguard_agent_action"

CAMPAIGN_ID = "17V629000"

VALID_WATCH = {
    "__fleetguard_action__": "watch_campaign",
    "status": "REQUESTED",
    "params": {"campaign_id": CAMPAIGN_ID, "rationale": "worth tracking"},
}

CAMPAIGN_LOOKUP_Q = "FROM bootcamp_students.fleetguard_recall_campaign"
INSERT_WATCHLIST = "INSERT INTO bootcamp_students.fleetguard_watchlist"


def _watch_cursor(
    campaign_exists: bool = True,
    watched_at: datetime | None = None,
    raise_on: tuple[str, Exception] | None = None,
) -> FakeCursor:
    script = {
        CAMPAIGN_LOOKUP_Q: [{"campaign_id": CAMPAIGN_ID}] if campaign_exists else [],
        INSERT_WATCHLIST: [{"watched_at": watched_at or datetime(2026, 9, 13, 12, 0, 0)}],
    }
    return FakeCursor(script, raise_on=raise_on)


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    monkeypatch.setattr(agent_actions.snapshot, "is_snapshot", lambda: False)


def _counts(exact: int = 1256, variant: int | None = None, make: int | None = None) -> dict:
    """The tiered fleet lookup returns all three counts in one row; the tier is chosen in
    Python. `variant` defaults to `exact` because a variant match is a superset of an exact
    one - any row matching `model = X` also matches the variant predicate."""
    variant = exact if variant is None else variant
    return {"exact_n": exact, "variant_n": variant, "make_n": make if make is not None else variant}


def _cursor(fleet_n: int | None = None, **counts) -> FakeCursor:
    """`fleet_n` is a spelling of `exact=` kept for the tests that predate the match tiers."""
    if fleet_n is not None:
        counts.setdefault("exact", fleet_n)
    return FakeCursor({VEHICLE_COUNT_Q: [_counts(**counts)]})


class TestParseEnvelope:
    def test_parses_a_sentinel_prefixed_item(self):
        assert parse_envelope(f"{ACTION_SENTINEL} {json.dumps(VALID)}") == VALID

    def test_plain_prose_is_not_an_envelope(self):
        assert parse_envelope("I've requested a defect signal for the RAM 2500.") is None

    def test_sentinel_must_start_the_item_not_merely_appear_in_it(self):
        """The agent builds envelopes in Python from its own tool's return value. A model
        that merely *writes* the sentinel into its prose must not be able to forge one —
        which is why the check is `startswith`, not `in`."""
        forged = f"Sure! {ACTION_SENTINEL} {json.dumps(VALID)}"
        assert parse_envelope(forged) is None

    def test_malformed_json_after_the_sentinel_is_ignored_not_raised(self):
        """A truncated envelope (the agent caps tool output at 6000 chars) must degrade to
        'no action', not 500 the chat endpoint."""
        assert parse_envelope(f"{ACTION_SENTINEL} {{not valid json") is None


class TestRefusals:
    def test_unknown_action_is_refused(self, monkeypatch):
        """Only actions this module implements may execute. A future agent tool, or a model
        inventing a plausible name, must not reach the database."""
        install(monkeypatch, agent_actions, _cursor())
        with pytest.raises(HTTPException) as exc:
            execute(USER, {"__fleetguard_action__": "delete_all_work_orders", "params": {}})
        assert exc.value.status_code == 400

    def test_unidentified_principal_is_refused_before_any_database_work(self, monkeypatch):
        """An unattributable write cannot be audited, so it must not happen — the same rule
        approve_campaign enforces. Asserted by making any DB use blow up."""

        def explode(*a, **k):
            raise AssertionError("connect() must not be reached without an identity")

        monkeypatch.setattr(agent_actions, "connect", explode)
        with pytest.raises(HTTPException) as exc:
            execute(ANON, VALID)
        assert exc.value.status_code == 403

    def test_snapshot_mode_refuses_rather_than_faking(self, monkeypatch):
        """A snapshot deployment has no credential. Returning a plausible signal_id for a row
        that was never created is I-050's failure applied to a write."""
        monkeypatch.setattr(agent_actions.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            agent_actions, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
        )
        with pytest.raises(HTTPException) as exc:
            execute(USER, VALID)
        assert exc.value.status_code == 501

    @pytest.mark.parametrize(
        "params",
        [
            {},  # component is NOT NULL in the schema
            {"component": "", "rationale": "x"},  # empty component
            {"component": "STEERING"},  # rationale missing
            {"component": "STEERING", "rationale": "x", "complaint_count": -5},  # negative
            {"component": "S" * 500, "rationale": "x"},  # unbounded length
        ],
    )
    def test_malformed_params_are_rejected(self, monkeypatch, params):
        """These all arrive as LLM output. Validation happens before the connection is opened,
        so a bad envelope cannot leave a half-written transaction."""
        install(monkeypatch, agent_actions, _cursor())
        with pytest.raises(Exception) as exc:
            execute(USER, {"__fleetguard_action__": "open_defect_signal", "params": params})
        assert not isinstance(exc.value, AssertionError)


class TestExecute:
    def test_writes_the_signal_and_returns_the_committed_row(self, monkeypatch):
        cur = _cursor(fleet_n=1256)
        conn = install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID)

        assert result.action == "open_defect_signal"
        assert result.signal_id.startswith("AGENT-")
        assert result.component == "STEERING"
        assert result.fleet_vehicles == 1256
        assert result.opened_by == "ops@example.com"
        assert conn.committed and not conn.rolled_back

    def test_row_is_marked_source_agent_and_attributed_to_the_real_human(self, monkeypatch):
        """Provenance is explicit rather than inferred from NULL detector columns (I-069's
        lesson). `opened_by` is the requesting user — not a service principal — because the
        write runs under their OBO token."""
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID)

        params = cur.params_for(INSERT_SIGNAL)
        assert params["actor"] == "ops@example.com"
        assert "'AGENT'" in cur.sql_for(INSERT_SIGNAL)

    def test_detector_columns_are_left_null(self, monkeypatch):
        """The agent did not run the z-score detector. Writing a max_z or run_len would
        fabricate a measurement, and the Emerging tab ranks on those."""
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID)

        sql = cur.sql_for(INSERT_SIGNAL)
        for detector_only in ("max_z", "run_len", "run_start", "run_end", "as_of_month", "is_live"):
            assert detector_only not in sql

    def test_fleet_count_is_computed_from_real_rows_not_taken_from_the_model(self, monkeypatch):
        """`fleet_vehicles` orders the Emerging tab, so a hallucinated value would reorder the
        operator's page. It is never read from the envelope — note the params carry no such
        field, and the value returned matches the database."""
        cur = _cursor(fleet_n=99)
        install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID)

        assert result.fleet_vehicles == 99
        assert cur.params_for(VEHICLE_COUNT_Q)["make"] == "RAM"

    def test_make_and_model_are_upper_cased_to_match_the_corpus(self, monkeypatch):
        """silver_recall normalises with UPPER(TRIM(...)); matching that is what lets an
        agent signal line up with detector rows and with fleetguard_vehicle."""
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        envelope = {**VALID, "params": {**VALID["params"], "make": " ram ", "model": "2500"}}
        result = execute(USER, envelope)

        assert result.make == "RAM"
        assert cur.params_for(VEHICLE_COUNT_Q)["make"] == "RAM"

    def test_no_make_means_no_fleet_lookup_and_zero_matches(self, monkeypatch):
        """Unknown make is honest as 0 matched, and must not run an unfiltered COUNT over
        every vehicle in the fleet."""
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        envelope = {
            "__fleetguard_action__": "open_defect_signal",
            "params": {"component": "BRAKES", "rationale": "pattern across several makes"},
        }
        result = execute(USER, envelope)

        assert result.fleet_vehicles == 0
        assert not any(VEHICLE_COUNT_Q in sql for sql, _ in cur.executed)

    def test_exact_match_is_preferred_and_labelled(self, monkeypatch):
        cur = _cursor(exact=1256, variant=1300)
        install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID)

        assert (result.fleet_vehicles, result.match_basis) == (1256, "EXACT")

    def test_falls_back_to_the_variant_count_when_the_model_name_is_spelled_differently(
        self, monkeypatch
    ):
        """The bug this exists for, measured live 2026-09-05. The agent names models the way
        NHTSA spells them (`F-250 SD`); the fleet registry uses vPIC's spelling (`F-250`). An
        exact-only match reported **0** fleet vehicles for a brake defect on 2,116 real trucks,
        and because the Emerging tab ranks on that column the signal sorted to the bottom
        looking like it touched nobody. Zero is the worst possible wrong answer here."""
        cur = _cursor(exact=0, variant=2116)
        install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID)

        assert result.fleet_vehicles == 2116
        assert result.match_basis == "MODEL_VARIANT"

    def test_variant_tier_is_recorded_not_just_returned(self, monkeypatch):
        """The count alone cannot be audited later: 2,116 means "these exact rows" under EXACT
        and "these rows, across a spelling difference" under MODEL_VARIANT."""
        cur = _cursor(exact=0, variant=2116)
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID)

        assert '"match_basis": "MODEL_VARIANT"' in cur.params_for(INSERT_AGENT_ACTION)["outp"]

    def test_a_make_with_no_matching_model_at_any_tier_stays_zero(self, monkeypatch):
        """The fallback must not degrade to "count the whole make". A signal for a model the
        fleet does not operate is genuinely 0 — reporting the make-wide count instead would
        manufacture exposure that does not exist."""
        cur = _cursor(exact=0, variant=0, make=9999)
        install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID)

        assert (result.fleet_vehicles, result.match_basis) == (0, "NONE")

    def test_make_only_signal_counts_the_whole_make(self, monkeypatch):
        """Distinct from the case above: no model was *supplied*, so the make-wide count is
        the honest answer rather than a widened fallback."""
        cur = _cursor(exact=0, variant=0, make=4200)
        install(monkeypatch, agent_actions, cur)

        envelope = {**VALID, "params": {**VALID["params"], "model": None}}
        result = execute(USER, envelope)

        assert (result.fleet_vehicles, result.match_basis) == (4200, "MAKE_ONLY")

    def test_writes_an_audit_row_attributed_to_the_caller(self, monkeypatch):
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID)

        assert "SIGNAL_OPENED" in cur.sql_for(INSERT_AUDIT)
        assert cur.params_for(INSERT_AUDIT)["actor"] == "ops@example.com"

    def test_writes_the_agent_action_row_with_on_behalf_of(self, monkeypatch):
        """fleetguard_agent_action has existed since Phase 5 with zero rows and no writer.
        `actor_principal` + `on_behalf_of` only make sense for an agent acting for a human,
        which is exactly this path."""
        cur = _cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID, trace_id="tr-123")

        params = cur.params_for(INSERT_AGENT_ACTION)
        assert params["tool"] == "open_defect_signal"
        assert params["actor"] == "ops@example.com"
        assert params["trace"] == "tr-123"

    def test_extra_envelopes_beyond_the_first_are_not_executed(self, monkeypatch):
        """chat.py executes at most one action per turn. Asserted here as a property of the
        result rather than trusting the caller: a chat turn that silently performs a batch of
        writes is not something an operator can review."""
        from fleetguard_api.routers.chat import _extract

        payload = {
            "output": [
                {"content": [{"type": "output_text", "text": "Requested two signals."}]},
                {
                    "content": [
                        {"type": "output_text", "text": f"{ACTION_SENTINEL} {json.dumps(VALID)}"}
                    ]
                },
                {
                    "content": [
                        {"type": "output_text", "text": f"{ACTION_SENTINEL} {json.dumps(VALID)}"}
                    ]
                },
            ]
        }
        text, actions = _extract(payload)
        assert len(actions) == 2  # both surfaced...
        assert text == "Requested two signals."  # ...and neither leaked into the prose

    def test_a_failed_write_rolls_back_rather_than_leaving_a_partial_record(self, monkeypatch):
        """Signal, audit row and agent-action row commit together or not at all — a signal
        with no audit trail is worse than no signal."""
        cur = FakeCursor(
            {VEHICLE_COUNT_Q: [_counts(5)]},
            raise_on=(INSERT_AUDIT, RuntimeError("audit insert failed")),
        )
        conn = install(monkeypatch, agent_actions, cur)

        with pytest.raises(RuntimeError):
            execute(USER, VALID)

        assert conn.rolled_back and not conn.committed


class TestWatchCampaign:
    """`watch_campaign` — the agent's second write. Same threat model as `open_defect_signal`
    (every field originated in LLM output) plus one thing that one doesn't have: a foreign
    reference to check, since `fleetguard_watchlist.campaign_id` carries no DB-level FK."""

    def test_unidentified_principal_is_refused_before_any_database_work(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("connect() must not be reached without an identity")

        monkeypatch.setattr(agent_actions, "connect", explode)
        with pytest.raises(HTTPException) as exc:
            execute(ANON, VALID_WATCH)
        assert exc.value.status_code == 403

    def test_snapshot_mode_refuses_rather_than_faking(self, monkeypatch):
        monkeypatch.setattr(agent_actions.snapshot, "is_snapshot", lambda: True)
        monkeypatch.setattr(
            agent_actions, "connect", lambda *a, **k: pytest.fail("connect() in snapshot mode")
        )
        with pytest.raises(HTTPException) as exc:
            execute(USER, VALID_WATCH)
        assert exc.value.status_code == 501

    @pytest.mark.parametrize(
        "params",
        [
            {},  # campaign_id and rationale both required
            {"campaign_id": "", "rationale": "x"},  # empty campaign_id
            {"campaign_id": CAMPAIGN_ID},  # rationale missing
            {"campaign_id": CAMPAIGN_ID, "rationale": ""},  # empty rationale
            {"campaign_id": "C" * 100, "rationale": "x"},  # unbounded length
        ],
    )
    def test_malformed_params_are_rejected_before_any_connection(self, monkeypatch, params):
        def explode(*a, **k):
            raise AssertionError("connect() must not be reached with malformed params")

        monkeypatch.setattr(agent_actions, "connect", explode)
        with pytest.raises(Exception) as exc:
            execute(USER, {"__fleetguard_action__": "watch_campaign", "params": params})
        assert not isinstance(exc.value, AssertionError)

    def test_unknown_campaign_is_refused(self, monkeypatch):
        """`fleetguard_watchlist.campaign_id` has no FK — this check is what stops a
        hallucinated campaign number from being recorded as if it were real."""
        cur = _watch_cursor(campaign_exists=False)
        install(monkeypatch, agent_actions, cur)

        with pytest.raises(HTTPException) as exc:
            execute(USER, VALID_WATCH)
        assert exc.value.status_code == 404

    def test_writes_the_watchlist_row_and_returns_the_committed_row(self, monkeypatch):
        cur = _watch_cursor()
        conn = install(monkeypatch, agent_actions, cur)

        result = execute(USER, VALID_WATCH, trace_id="tr-456")

        assert result.action == "watch_campaign"
        assert result.watchlist_id.startswith("WATCH-")
        assert result.campaign_id == CAMPAIGN_ID
        assert result.watched_by == "ops@example.com"
        assert conn.committed and not conn.rolled_back

    def test_writes_an_audit_row_attributed_to_the_caller(self, monkeypatch):
        cur = _watch_cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID_WATCH)

        assert "CAMPAIGN_WATCHED" in cur.sql_for(INSERT_AUDIT)
        assert cur.params_for(INSERT_AUDIT)["actor"] == "ops@example.com"

    def test_writes_the_agent_action_row_with_on_behalf_of(self, monkeypatch):
        cur = _watch_cursor()
        install(monkeypatch, agent_actions, cur)

        execute(USER, VALID_WATCH, trace_id="tr-456")

        params = cur.params_for(INSERT_AGENT_ACTION)
        assert params["tool"] == "watch_campaign"
        assert params["actor"] == "ops@example.com"
        assert params["trace"] == "tr-456"

    def test_duplicate_active_watch_is_refused_with_409(self, monkeypatch):
        """`ux_fg_watchlist_active` (src/lakebase/22_create_watchlist_table.py) is what
        actually serialises a double-click; this is the message layered on top of it."""
        cur = _watch_cursor(raise_on=(INSERT_WATCHLIST, UniqueViolation("duplicate key")))
        install(monkeypatch, agent_actions, cur)

        with pytest.raises(HTTPException) as exc:
            execute(USER, VALID_WATCH)
        assert exc.value.status_code == 409

    def test_a_failed_write_rolls_back_rather_than_leaving_a_partial_record(self, monkeypatch):
        cur = _watch_cursor(raise_on=(INSERT_AUDIT, RuntimeError("audit insert failed")))
        conn = install(monkeypatch, agent_actions, cur)

        with pytest.raises(RuntimeError):
            execute(USER, VALID_WATCH)

        assert conn.rolled_back and not conn.committed


class TestChatExtraction:
    """`chat.py::_extract` — the boundary that keeps envelopes out of the conversation.

    This matters beyond tidiness: `Assistant.tsx` replays assistant turns as history on the
    next request. An envelope left in the visible reply would re-enter the model's context and
    could be acted on a second time.
    """

    def _payload(self, *texts: str) -> dict:
        return {"output": [{"content": [{"type": "output_text", "text": t}]} for t in texts]}

    def test_envelope_is_stripped_from_the_visible_reply(self):
        from fleetguard_api.routers.chat import _extract

        text, actions = _extract(
            self._payload("I've requested a signal.", f"{ACTION_SENTINEL} {json.dumps(VALID)}")
        )
        assert text == "I've requested a signal."
        assert ACTION_SENTINEL not in text
        assert actions == [VALID]

    def test_ordinary_reply_is_unchanged_and_yields_no_action(self):
        """The overwhelmingly common case — four of the five tools are reads. Behaviour here
        must be identical to before this feature existed."""
        from fleetguard_api.routers.chat import _extract

        text, actions = _extract(self._payload("25 vehicles across 22 depots, EXACT match."))
        assert text == "25 vehicles across 22 depots, EXACT match."
        assert actions == []

    def test_non_output_text_chunks_are_still_ignored(self):
        from fleetguard_api.routers.chat import _extract

        payload = {
            "output": [
                {"content": [{"type": "reasoning", "text": "internal"}]},
                {"content": [{"type": "output_text", "text": "visible"}]},
            ]
        }
        text, actions = _extract(payload)
        assert text == "visible"
        assert actions == []
