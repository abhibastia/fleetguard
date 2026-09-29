"""Executing the agent's write actions — the one place the assistant changes fleet state.

**Why the agent does not do this itself.** The deployed `ResponsesAgent` runs on a Model
Serving endpoint under an auto-provisioned service principal, with only vector-search and
SQL-warehouse resources declared. It has no path to Lakebase, and cannot be given one on this
account: `DatabricksLakebase` addresses a *database instance* while FleetGuard's Lakebase is
the autoscaling project/endpoint flavour; manual auth needs a service principal
(`service-principals create` is admin-only here); and Databricks' on-behalf-of-user support
for Model Serving does not cover Lakebase. All three routes verified closed 2026-09-07.

So the agent's write tools return a *requested action* and this module executes it — client-side
tool execution, the same shape the sibling f1-intelligence-copilot capstone uses, where a Flask
app runs the INSERT the model asked for. FleetGuard does it with **better attribution**: that
project writes as a static Postgres role with `user_id='default'`, while this runs under
`connect(principal)`, i.e. the requesting human's own OBO token. `opened_by`/`watched_by` are
real identities.

**The console is authoritative about what happened, never the model's prose.** The agent is
prompted to say it *requested* an action; the committed row returned from here is what the UI
reports. An INSERT that never committed looks identical to the caller otherwise — which is the
failure the sibling project hit and guarded against, and the same shape as I-050.

Both writes are an **observation, not a dispatch**. Opening a defect signal records "this looks
worth tracking"; watching a campaign records "keep an eye on this". Neither creates a work
order, and the agent still has no route to `fleetguard_work_order` — launching a campaign stays
behind the `FLEETGUARD_APPROVERS` gate.

**Gated by the same allowlist regardless.** Being "an observation, not a dispatch" bounds the
blast radius of a bad write, it doesn't make the write harmless: an unauthorised signal still
lands on the operator's Emerging tab ranked by a real (if attacker-chosen) `make`/`model`, and
an unauthorised watch still shows up on the fleet team's list. Both execute paths call
`authz.may_approve` before touching the database, same as `approve_campaign` and the work-order
PATCH — so the safety property does not depend on `FLEETGUARD_APPROVERS` and the app's own
`CAN_MANAGE` grantees happening to be the same people.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import authz, snapshot
from .auth.tokens import Principal
from .db import PG_SCHEMA, UniqueViolation, connect, rows_to_dicts

# Must match ACTION_SENTINEL in src/agent/14_fleetguard_agent.py, which prefixes the extra
# output item carrying an envelope.
ACTION_SENTINEL = "__FLEETGUARD_ACTION__"

log = logging.getLogger(__name__)


OPEN_DEFECT_SIGNAL = "open_defect_signal"
WATCH_CAMPAIGN = "watch_campaign"


class OpenDefectSignalParams(BaseModel):
    """Validated shape of what the model asked for.

    Every field here originated in LLM output, so it is validated rather than trusted:
    lengths are bounded (these land in a database and then on a page), and `component` —
    the one NOT NULL column — must be non-empty. Values are passed as bound parameters
    downstream, never interpolated.
    """

    component: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=2000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    # Accepted so an already-deployed agent version can still call the tool, and recorded on
    # `fleetguard_agent_action.tool_input` as what the model claimed — but deliberately NOT
    # written to `fleetguard_defect_signal`. See the INSERT below.
    complaint_count: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("component", "make", "model", mode="before")
    @classmethod
    def _upper_and_strip(cls, v: str | None) -> str | None:
        """The corpus stores make/model/component upper-cased (silver_recall normalises with
        UPPER(TRIM(...))). Matching that here is what lets an agent-opened signal line up with
        detector rows and with `fleetguard_vehicle` for the fleet count.

        **`mode="before"` is load-bearing, not stylistic** (I-115). Pydantic runs
        `Field(min_length=1)` as part of the core schema, which happens BEFORE a default
        `mode="after"` validator. So `component=" "` used to pass the length check, then get
        stripped to `""` — and an empty component is not a cosmetic problem:

          component=" " -> passes min_length=1 -> stripped to "" -> series_key "" -> NULL

        `series_key` is built from `(make, model, component)` and written as `series_key or
        None`, so a blank component produced a NULL key. `ux_fg_defect_signal_agent_active` is
        a partial unique index over `(opened_by, series_key, component)`, and Postgres allows
        unlimited NULLs in a unique index — so the idempotency guard was defeated and the
        Emerging tab rendered a blank component in a NOT NULL column.

        Stripping first means the length constraint sees what will actually be stored. A
        whitespace-only component is now rejected at the edge with a 422, which is the honest
        answer: the model did not name a component.
        """
        return v.strip().upper() if isinstance(v, str) else v


class ActionResult(BaseModel):
    """What the console did for `open_defect_signal` — returned to the UI so it can report
    the real outcome."""

    action: str
    signal_id: str
    component: str
    make: str | None
    model: str | None
    fleet_vehicles: int
    # How `fleet_vehicles` was arrived at: 'EXACT' (make+model matched as given),
    # 'MODEL_VARIANT' (matched across a naming difference, e.g. NHTSA's `F-250 SD` vs vPIC's
    # `F-250`), 'MAKE_ONLY' (no model supplied), or 'NONE' (nothing matched). Reported rather
    # than folded into the count because the guarantee attached to the number differs by tier.
    match_basis: str
    opened_by: str


class WatchCampaignParams(BaseModel):
    """Validated shape of what the model asked for.

    Unlike `OpenDefectSignalParams`, `campaign_id` is not upper-cased/normalised against a
    corpus — it's an NHTSA campaign number (`17V629000`), passed straight through and checked
    for existence against `fleetguard_recall_campaign` instead.
    """

    campaign_id: str = Field(min_length=1, max_length=40)
    rationale: str = Field(min_length=1, max_length=2000)


class WatchCampaignResult(BaseModel):
    """What the console did for `watch_campaign`. No `fleet_vehicles`/`match_basis` here —
    this action computes no fact, it's a bookmark with a reason, not an observation."""

    action: str
    watchlist_id: str
    campaign_id: str
    watched_by: str
    watched_at: datetime


def parse_envelope(text: str) -> dict | None:
    """Pull an action envelope out of a sentinel-prefixed output item, or return None.

    Deliberately strict: the sentinel must *start* the item. The agent constructs these in
    Python from its own tool's return value, so a well-formed envelope only exists if the
    tool actually ran — a model that merely writes the sentinel into its prose produces an
    item that does not start with it, and is ignored.
    """
    if not text.startswith(ACTION_SENTINEL):
        return None
    try:
        return json.loads(text[len(ACTION_SENTINEL) :].strip())
    except ValueError:
        return None


def _record_attempt(
    principal: Principal,
    tool: str,
    params: dict,
    outcome: str,
    reason: str,
    trace_id: str | None,
) -> None:
    """Write one `fleetguard_agent_action` row for an attempt that did NOT commit.

    **Why this is a second transaction.** The successful path writes the business row, the
    audit row and the agent-action row together, so an action that is recorded is an action
    that happened. The corollary used to be that a REJECTED or FAILED attempt rolled back
    with them and left no trace in Lakebase at all — the table recorded only successes while
    reading, from its name, like a record of what the agent did. MLflow still had the trace,
    but the audit trail and the observability trail are only joinable through `trace_id`, and
    a row that never existed cannot carry one.

    So: after the rollback, open a fresh connection and record the attempt. It must not share
    the failed transaction — that one is already aborted — and it must not resurrect any part
    of the business write, which is why only this one row is written.

    **Best-effort on purpose.** If the attempt log itself fails, swallow it and let the
    original error surface. Losing the record of a refusal is bad; replacing the caller's
    real 403 with a confusing database error from the logging path is worse.
    """
    if snapshot.is_snapshot():
        # There is no database in snapshot mode — the refusal this is recording IS that
        # fact. Calling `connect` here would turn a clean 501 into a connection error, and
        # `tests/test_agent_actions.py` asserts the snapshot path never opens one.
        return
    try:
        with connect(principal, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {PG_SCHEMA}.fleetguard_agent_action
                    (tool, tool_input, tool_output, actor_principal, on_behalf_of,
                     requires_approval, trace_id)
                    VALUES (%(tool)s, %(inp)s, %(outp)s, %(actor)s, %(actor)s, false, %(trace)s)""",
                {
                    "tool": tool,
                    "inp": json.dumps(params, default=str),
                    "outp": json.dumps({"outcome": outcome, "reason": reason}),
                    "actor": principal.user_name,
                    "trace": trace_id,
                },
            )
    except Exception:
        # VISIBLE, not silent (I-117). This was a bare `pass` with a lint suppression.
        # Swallowing the exception is still right — the caller's real 403 must not be replaced by a database
        # error from the *logging* path — but swallowing it **quietly** meant a refusal could
        # vanish from the audit trail with nothing anywhere recording that it had. For a system
        # whose whole claim is that every agent action is attributable, "the audit write failed
        # and nobody knows" is the wrong half to keep.
        #
        # `log.exception` keeps the original behaviour for the caller and gives the operator a
        # stack trace. The DB audit row remains best-effort *by design*, and is now honest
        # about it: the trail is Lakebase **plus** the MLflow trace, and this is the line that
        # says which one was lost.
        log.exception("failed to record a refused agent action for %s", principal.user_name)


def execute(
    principal: Principal, envelope: dict, trace_id: str | None = None
) -> ActionResult | WatchCampaignResult:
    """Perform the requested action under the caller's own identity.

    Raises HTTPException on refusal so the caller surfaces a real status rather than a
    half-written state.
    """
    action = envelope.get("__fleetguard_action__")
    handler = {
        OPEN_DEFECT_SIGNAL: _execute_open_defect_signal,
        WATCH_CAMPAIGN: _execute_watch_campaign,
    }.get(action)
    if handler is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported agent action {action!r}")

    # `fleetguard_agent_action` records ATTEMPTS, not only commits — the outcome is in
    # `tool_output.outcome`. See `_record_attempt` and ARCHITECTURE §7.1.
    #
    # Only attempts by an IDENTIFIED caller are recorded: an unattributable row is the
    # thing this project refuses to write anywhere, and `_record_attempt` would have no
    # `actor_principal` to put in it. That refusal is itself the 403 raised below.
    try:
        return handler(principal, envelope, trace_id)
    except HTTPException as exc:
        if principal.user_name:
            _record_attempt(
                principal,
                action,
                envelope.get("params") or {},
                "REJECTED",
                str(exc.detail),
                trace_id,
            )
        raise
    except ValidationError as exc:
        # MODEL-AUTHORED PARAMS THAT FAIL THE SCHEMA ARE A 422, NOT A 500 (found 2026-09-29).
        #
        # Every handler starts by constructing its params model, and that raises a bare
        # `pydantic.ValidationError` — which is not an `HTTPException`, so it fell through to
        # the generic branch below, got re-raised, and left `/api/chat` returning **500**.
        # Reachable from ordinary model output: a blank `component`, a negative
        # `complaint_count`, an over-long `rationale`. The write was always correctly refused;
        # what was wrong is that a refusal of bad input presented as the console breaking.
        #
        # Same bug family as I-117's 422-that-became-a-500 in `main.py`, reached from the other
        # side — there the *error body* could not serialise, here the error never became one.
        #
        # Recorded as REJECTED rather than FAILED for the same reason: the system worked. The
        # detail is `exc.errors()` rather than `str(exc)` so the audit row keeps the field names
        # without the multi-line pydantic banner and its docs URL.
        if principal.user_name:
            _record_attempt(
                principal,
                action,
                envelope.get("params") or {},
                "REJECTED",
                f"invalid params: {exc.errors(include_url=False)}",
                trace_id,
            )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"the agent requested {action} with invalid parameters: "
            + "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()),
        ) from exc
    except Exception as exc:
        if principal.user_name:
            _record_attempt(
                principal,
                action,
                envelope.get("params") or {},
                "FAILED",
                f"{type(exc).__name__}: {exc}",
                trace_id,
            )
        raise


def _execute_open_defect_signal(
    principal: Principal, envelope: dict, trace_id: str | None
) -> ActionResult:
    actor = principal.user_name
    if not actor:
        # Same rule as approve_campaign: an unattributable write cannot be audited, so it
        # must not happen. There is no service-principal fallback.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "opening a defect signal requires an identified user; this token carries no identity",
        )

    if not authz.may_approve(actor):
        # Same gate as approve_campaign / the work-order PATCH. This write is an observation,
        # not a dispatch, but it still lands on the operator's Emerging tab with an
        # attacker-chosen make/model/rationale if left ungated — the blast radius is smaller
        # than a dispatch, not zero.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{actor} is signed in but not an approver on this deployment.",
        )

    if snapshot.is_snapshot():
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "This deployment runs on a data snapshot and cannot open defect signals.",
        )

    params = OpenDefectSignalParams(**(envelope.get("params") or {}))
    signal_id = f"AGENT-{uuid.uuid4().hex[:12]}"
    series_key = "|".join(p for p in (params.make, params.model, params.component) if p)
    # The id is fresh per call, so it makes this write idempotent about nothing. What
    # serialises a retry is `ux_fg_defect_signal_agent_active` (src/lakebase/
    # 26_add_defect_signal_idempotency.py) — a partial unique index on
    # (opened_by, series_key, component) over OPEN agent signals. See the INSERT below.

    with connect(principal, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                # Fleet relevance is computed here from real rows, not taken from the model.
                # The Emerging tab ranks on this, so a hallucinated count would reorder the
                # operator's page. NULL make/model means "unknown", which is 0 matched.
                #
                # Exact model matching alone is WRONG here, and silently so. The agent names
                # models the way NHTSA spells them (its evidence is complaint text); the fleet
                # registry spells them the way vPIC does. NHTSA writes `F-250 SD`, vPIC writes
                # `F-250` — same trucks, no exact match. Measured live 2026-09-05: an agent
                # signal for FORD / `F-250 SD` reported **0** fleet vehicles against a fleet
                # holding **2,116** of them. This is I-030 reaching the agent path, and 0 is
                # the most damaging possible answer: the Emerging tab ranks on this column, so
                # a real brake defect sorted to the bottom looking like it touched nobody.
                #
                # So match in the same tiers the gold layer already uses (EXACT first, then
                # MODEL_VARIANT), and report which tier answered rather than blending them
                # into one unattributed number — §7's determinism claim covers EXACT only.
                # The variant test is anchored to a word boundary (`model || ' %'`) in both
                # directions, so `F-250` matches `F-250 SD` but `F-2` does not.
                fleet_vehicles = 0
                match_basis = "NONE"
                if params.make:
                    # `%(model)s::text` — the cast is required, not stylistic. psycopg binds
                    # server-side, and a parameter whose first appearance is a bare
                    # `$n IS NULL` gives Postgres nothing to infer a type from:
                    # `AmbiguousParameter: could not determine data type of parameter $2`.
                    # Found by live execution; a fake cursor never type-checks SQL, so no
                    # unit test could have caught it.
                    cur.execute(
                        f"""SELECT
                              COUNT(*) FILTER (
                                WHERE %(model)s::text IS NOT NULL AND model = %(model)s
                              ) AS exact_n,
                              COUNT(*) FILTER (
                                WHERE %(model)s::text IS NOT NULL
                                  AND (model = %(model)s
                                       OR %(model)s LIKE model || ' %%'
                                       OR model LIKE %(model)s || ' %%')
                              ) AS variant_n,
                              COUNT(*) AS make_n
                            FROM {PG_SCHEMA}.fleetguard_vehicle
                            WHERE make = %(make)s""",
                        {"make": params.make, "model": params.model},
                    )
                    counts = rows_to_dicts(cur)[0]

                    # THE LOOKUP IS AUTHORITATIVE, NOT ADVISORY.
                    #
                    # The prompt tells the agent to call `lookup_fleet_models` before naming
                    # a make, and it generally does. But a prompt is not an enforcement
                    # mechanism, and a make this fleet does not operate is not a weak signal —
                    # it is a signal about nothing, recorded under a name an operator will
                    # read as real. `make_n == 0` means NO vehicle in `fleetguard_vehicle`
                    # carries this make, which no honest observation about THIS fleet can
                    # produce.
                    #
                    # Note what is NOT rejected: a make that matches with a model that does
                    # not. That is `match_basis='NONE'`, a legitimate and informative state —
                    # the fleet runs FORD, and this particular FORD model is not one of them.
                    # And a NULL make stays allowed entirely: "brake complaints, make not yet
                    # established" is a real thing to record. What is refused is a make
                    # asserted and wrong.
                    if int(counts["make_n"]) == 0:
                        raise HTTPException(
                            status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"this fleet operates no {params.make} vehicles — call "
                            "lookup_fleet_models for the makes it does operate",
                        )

                    if params.model is None:
                        fleet_vehicles, match_basis = int(counts["make_n"]), "MAKE_ONLY"
                    elif int(counts["exact_n"]) > 0:
                        fleet_vehicles, match_basis = int(counts["exact_n"]), "EXACT"
                    elif int(counts["variant_n"]) > 0:
                        fleet_vehicles, match_basis = int(counts["variant_n"]), "MODEL_VARIANT"

                # Detector-only columns (max_z, run_len, run_start/end, as_of_month, is_live)
                # are left NULL on purpose: the agent did not run the z-score detector, and
                # filling them would fabricate a measurement. `source` carries the provenance
                # explicitly rather than leaving it to be inferred from those NULLs (I-069).
                #
                # `complaint_count` IS NOW ONE OF THEM, and it did not used to be.
                #
                # It was persisted straight from `params.complaint_count` — a value the *model*
                # supplies, bounded by Pydantic but not derived from anything. The Emerging tab
                # renders it in the same column as the detector's measured counts, so a number
                # the model produced was displayed exactly like one the pipeline computed. That
                # is the mistake the neighbouring `max_z` handling already guards against: the
                # console shows an em-dash there precisely because "the agent did not measure
                # this" and "the measurement was zero" are different claims.
                #
                # So it is written NULL. The value still exists on the envelope and in
                # `fleetguard_agent_action.tool_input`, where it is legible as *what the model
                # asserted* rather than as fleet data. If a real count is wanted here it has to
                # be computed, not accepted (I-051's rule: a NULL that says "not measured" beats
                # a value implying a calibration nobody performed).
                # IDEMPOTENT ON RETRY, like the approval path and the watchlist.
                #
                # `chat.py` runs on a 120 s timeout and the agent can legitimately take tens
                # of seconds. A browser that gives up and retries would previously write a
                # SECOND signal for the same observation — different `signal_id`, identical
                # meaning — and both would land on the operator's Emerging tab as separate
                # findings. The agent's own turn budget (90 s) now makes that window much
                # narrower, but narrower is not closed, and only the database can actually
                # serialise two concurrent requests: a SELECT-then-INSERT check would let
                # both reads miss before either write.
                #
                # Scoped to OPEN agent signals by the same actor, so re-opening a signal
                # after it has been closed is still allowed — the index does not see closed
                # rows. Same shape as `ux_fg_service_campaign_active`.
                try:
                    cur.execute(
                        f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
                            (signal_id, component, make, model, series_key,
                             fleet_vehicles, status, source, opened_by, rationale)
                            VALUES (%(sid)s, %(component)s, %(make)s, %(model)s, %(series_key)s,
                                    %(fleet_vehicles)s, 'OPEN', 'AGENT',
                                    %(actor)s, %(rationale)s)""",
                        {
                            "sid": signal_id,
                            "component": params.component,
                            "make": params.make,
                            "model": params.model,
                            "series_key": series_key or None,
                            "fleet_vehicles": fleet_vehicles,
                            "actor": actor,
                            "rationale": params.rationale,
                        },
                    )
                except UniqueViolation as exc:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"{actor} already has an open {params.component} signal for "
                        f"{series_key or 'this series'}",
                    ) from exc

                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                        (entity_type, entity_id, action, actor_principal, after_state)
                        VALUES ('defect_signal', %(sid)s, 'SIGNAL_OPENED', %(actor)s, %(after)s)""",
                    {
                        "sid": signal_id,
                        "actor": actor,
                        "after": json.dumps(
                            {
                                "component": params.component,
                                "make": params.make,
                                "model": params.model,
                                "fleet_vehicles": fleet_vehicles,
                                "rationale": params.rationale,
                                "source": "AGENT",
                            }
                        ),
                    },
                )

                # First ever write to this table. It has existed since Phase 5 with the exact
                # columns this needs - `actor_principal` AND `on_behalf_of` only make sense
                # for an agent acting for a human - and had zero rows and no writer until now.
                # §8.3's CDF trigger path reads its history table.
                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_agent_action
                        (tool, tool_input, tool_output, actor_principal, on_behalf_of,
                         requires_approval, trace_id)
                        VALUES (%(tool)s, %(inp)s, %(outp)s, %(actor)s, %(actor)s, false, %(trace)s)""",
                    {
                        "tool": OPEN_DEFECT_SIGNAL,
                        "inp": json.dumps(params.model_dump()),
                        "outp": json.dumps(
                            {
                                "signal_id": signal_id,
                                "fleet_vehicles": fleet_vehicles,
                                # Persisted because `fleet_vehicles` alone cannot be audited
                                # later: the same number means "these exact rows" under EXACT
                                # and "these rows, matched across a spelling difference" under
                                # MODEL_VARIANT.
                                "match_basis": match_basis,
                            }
                        ),
                        "actor": actor,
                        "trace": trace_id,
                    },
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return ActionResult(
        action=OPEN_DEFECT_SIGNAL,
        signal_id=signal_id,
        component=params.component,
        make=params.make,
        model=params.model,
        fleet_vehicles=fleet_vehicles,
        match_basis=match_basis,
        opened_by=actor,
    )


def _execute_watch_campaign(
    principal: Principal, envelope: dict, trace_id: str | None
) -> WatchCampaignResult:
    actor = principal.user_name
    if not actor:
        # Same rule as _execute_open_defect_signal / approve_campaign: an unattributable
        # write cannot be audited, so it must not happen. No service-principal fallback.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "watching a campaign requires an identified user; this token carries no identity",
        )

    if not authz.may_approve(actor):
        # Same gate as _execute_open_defect_signal / approve_campaign.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{actor} is signed in but not an approver on this deployment.",
        )

    if snapshot.is_snapshot():
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "This deployment runs on a data snapshot and cannot watch campaigns.",
        )

    params = WatchCampaignParams(**(envelope.get("params") or {}))
    watchlist_id = f"WATCH-{uuid.uuid4().hex[:12]}"

    with connect(principal, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                # Same shape as approve_campaign's existence check (routers/approval.py):
                # name what's missing rather than let a bare FK violation surface.
                #
                # Since 2026-09-20 there IS an FK here (`fk_fg_watchlist_campaign`,
                # ARCHITECTURE §4.6a), which reverses what this comment used to say about the
                # schema being app-checked rather than DB-enforced. The check stays and still
                # runs first, because the two do different jobs: the constraint guarantees the
                # row cannot be written, and this gives the caller a 404 naming the campaign
                # instead of a ForeignKeyViolation the UI would have to parse. Defence in
                # depth, in that order.
                cur.execute(
                    f"""SELECT campaign_id FROM {PG_SCHEMA}.fleetguard_recall_campaign
                        WHERE campaign_id = %(cid)s""",
                    {"cid": params.campaign_id},
                )
                if not rows_to_dicts(cur):
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND, f"unknown campaign {params.campaign_id}"
                    )

                try:
                    cur.execute(
                        f"""INSERT INTO {PG_SCHEMA}.fleetguard_watchlist
                            (watchlist_id, campaign_id, rationale, watched_by)
                            VALUES (%(wid)s, %(cid)s, %(rationale)s, %(actor)s)
                            RETURNING watched_at""",
                        {
                            "wid": watchlist_id,
                            "cid": params.campaign_id,
                            "rationale": params.rationale,
                            "actor": actor,
                        },
                    )
                    watched_at = rows_to_dicts(cur)[0]["watched_at"]
                except UniqueViolation as exc:
                    # `ux_fg_watchlist_active` (src/lakebase/22_create_watchlist_table.py) is
                    # what actually serialises a double-click; this exists for the message.
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"{params.campaign_id} is already on {actor}'s watchlist",
                    ) from exc

                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_audit_log
                        (entity_type, entity_id, action, actor_principal, after_state)
                        VALUES ('watchlist', %(wid)s, 'CAMPAIGN_WATCHED', %(actor)s, %(after)s)""",
                    {
                        "wid": watchlist_id,
                        "actor": actor,
                        "after": json.dumps(
                            {"campaign_id": params.campaign_id, "rationale": params.rationale}
                        ),
                    },
                )

                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_agent_action
                        (tool, tool_input, tool_output, actor_principal, on_behalf_of,
                         requires_approval, trace_id)
                        VALUES (%(tool)s, %(inp)s, %(outp)s, %(actor)s, %(actor)s, false, %(trace)s)""",
                    {
                        "tool": WATCH_CAMPAIGN,
                        "inp": json.dumps(params.model_dump()),
                        "outp": json.dumps({"watchlist_id": watchlist_id}),
                        "actor": actor,
                        "trace": trace_id,
                    },
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return WatchCampaignResult(
        action=WATCH_CAMPAIGN,
        watchlist_id=watchlist_id,
        campaign_id=params.campaign_id,
        watched_by=actor,
        watched_at=watched_at,
    )
