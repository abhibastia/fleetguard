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
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator

from . import authz, snapshot
from .auth.tokens import Principal
from .db import PG_SCHEMA, UniqueViolation, connect, rows_to_dicts

# Must match ACTION_SENTINEL in src/agent/14_fleetguard_agent.py, which prefixes the extra
# output item carrying an envelope.
ACTION_SENTINEL = "__FLEETGUARD_ACTION__"

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
    complaint_count: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("component", "make", "model")
    @classmethod
    def _upper_and_strip(cls, v: str | None) -> str | None:
        """The corpus stores make/model/component upper-cased (silver_recall normalises with
        UPPER(TRIM(...))). Matching that here is what lets an agent-opened signal line up with
        detector rows and with `fleetguard_vehicle` for the fleet count."""
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


def execute(
    principal: Principal, envelope: dict, trace_id: str | None = None
) -> ActionResult | WatchCampaignResult:
    """Perform the requested action under the caller's own identity.

    Raises HTTPException on refusal so the caller surfaces a real status rather than a
    half-written state.
    """
    action = envelope.get("__fleetguard_action__")
    if action == OPEN_DEFECT_SIGNAL:
        return _execute_open_defect_signal(principal, envelope, trace_id)
    if action == WATCH_CAMPAIGN:
        return _execute_watch_campaign(principal, envelope, trace_id)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported agent action {action!r}")


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
                cur.execute(
                    f"""INSERT INTO {PG_SCHEMA}.fleetguard_defect_signal
                        (signal_id, component, make, model, series_key, complaint_count,
                         fleet_vehicles, status, source, opened_by, rationale)
                        VALUES (%(sid)s, %(component)s, %(make)s, %(model)s, %(series_key)s,
                                %(complaint_count)s, %(fleet_vehicles)s, 'OPEN', 'AGENT',
                                %(actor)s, %(rationale)s)""",
                    {
                        "sid": signal_id,
                        "component": params.component,
                        "make": params.make,
                        "model": params.model,
                        "series_key": series_key or None,
                        "complaint_count": params.complaint_count,
                        "fleet_vehicles": fleet_vehicles,
                        "actor": actor,
                        "rationale": params.rationale,
                    },
                )

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
                # name what's missing rather than let a bare FK violation surface, since this
                # table deliberately carries no FK on campaign_id (consistent with every other
                # cross-table reference in this schema — app-checked, not DB-enforced).
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
