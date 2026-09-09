"""Seed the console with a lived-in operational history (Phase 11's B2).

**Why this exists.** Every write feature in Phase 13 works and is tested, but live Lakebase
holds almost nothing for them to show. Measured 2026-09-09, before this script ran:

    work orders   230 rows — 229 OPEN, 1 COMPLETED, 1 assigned, 1 costed
    audit log      11 rows — 4 of them LATENCY_PROBE noise
    cost breakdown  1 costed work order

So the Work orders, Cost breakdown and Audit log tabs demo as empty, and a judge reads three
built features as three unbuilt ones. The repair→cost→audit loop is the whole commercial
argument of Phase 13; it needs to have visibly *happened*.

**Everything here goes through the real API, not direct INSERTs.** Assignments pass the
depot-consistency check in `work_orders.py`, status moves stamp `completed_at` through the
COALESCE branch, costs hit the non-negative constraint, and every one of them writes its own
`fleetguard_audit_log` row through the same handler an operator's click would. The audit
trail is therefore genuine: it records writes that actually occurred, by an identity that
actually made them. A bulk INSERT would have produced the same-looking tables and a
fabricated audit log, which is the failure mode this project keeps naming (I-050, I-051).

**What is NOT genuine, stated plainly.** Timestamps are today's. A real fleet would have
assigned, started and completed these over weeks; this seeds them over minutes. Backdating
`created_at` would mean writing audit rows directly and pretending they are older than they
are — exactly the fabrication the paragraph above avoids — so it is not done. Due dates *are*
staggered (step 5, direct SQL) because a due date is a plan, not a record of an event, and
331 work orders all due on the same day is an artefact of how they were created rather than
anything an operator chose.

**CDF is append-only.** Every write here lands permanently in
`bootcamp_students.bootcamp_cdc.lb_fleetguard_*_history` and cannot be scrubbed later. That
is fine for state we mean to keep — which is the point of seeding rather than testing — but
it is not undoable. `fleetguard-cdf-to-gold` is NOT triggered by any of this: it watches
`fleetguard_agent_action` and `fleetguard_defect_signal`, neither of which this script
touches.

**Idempotent.** Every choice (technician, target status, cost) is derived deterministically
from the work order id, so a second run converges on the same state instead of drifting, and
re-launching an already-launched campaign is refused by I-063's 409 rather than duplicated.

Usage:
    scripts/run_local_static_dev.sh 8811                      # in another shell
    .venv/bin/python scripts/seed_demo_state.py               # dry run — writes nothing
    .venv/bin/python scripts/seed_demo_state.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

import httpx

os.environ.setdefault("PSYCOPG_IMPL", "")  # let db.py decide; see I-045

# --- what to seed -----------------------------------------------------------------------
#
# A third campaign, so `cost-breakdown`'s by_component table has more than two rows. Chosen
# from the live queue: 101 vehicles across 48 depots, a component that is neither of the two
# already launched, and deliberately NOT a Park It recall — those stay unapproved so the live
# approve-and-launch moment in the demo still has something consequential to act on.
THIRD_CAMPAIGN = "17V621000"
THIRD_TITLE = "Frontal airbag inflator replacement"
THIRD_RATIONALE = (
    "Frontal airbag inflator campaign covering 101 fleet vehicles across 48 depots. "
    "Not a Park It recall; scheduled repair rather than immediate grounding."
)
THIRD_DUE_DAYS = 30

# Status mix per campaign, as (COMPLETED, IN_PROGRESS, CANCELLED) shares. The remainder stays
# OPEN. Older campaigns are further through the work — 17V629000 launched 2026-09-01, the
# other two later — which is what makes the Launched tab read as an operation in progress
# rather than three identical rows.
STATUS_MIX = {
    "17V629000": (0.80, 0.12, 0.00),  # oldest, a 25-vehicle Park It — nearly closed out
    "21V037000": (0.46, 0.27, 0.02),  # mid-flight
    "17V621000": (0.20, 0.30, 0.03),  # newest, just getting going
}
DEFAULT_MIX = (0.40, 0.25, 0.02)

# Cost bands per component keyword, in dollars: (low, high). A steering-rack inspection and a
# brake master-cylinder replacement are not the same job — the flat per-vehicle figure was
# tried and rejected for exactly this reason (I-069), so the seed must not reintroduce it by
# giving every component the same spread.
COST_BANDS = [
    ("STEERING", (140, 1_450)),  # inspection, escalating to replacement on some
    ("SERVICE BRAKES", (380, 920)),
    ("AIR BAGS", (620, 1_180)),
]
DEFAULT_BAND = (250, 900)

# Due-date stagger, in days from today, cycled deterministically. Negative values are
# genuinely overdue — a real fleet has some, and it exercises the OVERDUE badge I-087 fixed.
DUE_OFFSETS = (-9, -4, -1, 2, 5, 8, 12, 15, 19, 23, 28, 34, 41, 52)


def die(msg: str) -> None:
    print(f"REFUSING: {msg}", file=sys.stderr)
    raise SystemExit(2)


def bucket(wo_id: str, salt: str) -> float:
    """A stable [0, 1) value per (work order, purpose).

    Deterministic so re-running converges instead of reshuffling every work order into a new
    status each time, which would make the audit log a record of the script's randomness
    rather than of anything meaningful.
    """
    h = hashlib.sha256(f"{salt}:{wo_id}".encode()).hexdigest()
    return int(h[:12], 16) / 0x1000000000000


def target_status(wo_id: str, campaign_id: str) -> str:
    completed, in_progress, cancelled = STATUS_MIX.get(campaign_id, DEFAULT_MIX)
    r = bucket(wo_id, "status")
    if r < completed:
        return "COMPLETED"
    if r < completed + in_progress:
        return "IN_PROGRESS"
    if r < completed + in_progress + cancelled:
        return "CANCELLED"
    return "OPEN"


def target_cost(wo_id: str, component: str) -> float:
    low, high = DEFAULT_BAND
    for keyword, band in COST_BANDS:
        if component.upper().startswith(keyword):
            low, high = band
            break
    # Squared so the distribution leans toward the cheap end with a real tail, rather than
    # being flat — a flat spread of repair costs is its own kind of obviously-generated.
    r = bucket(wo_id, "cost") ** 2
    return round(low + r * (high - low), 2)


class Api:
    def __init__(self, base_url: str, apply: bool) -> None:
        self.c = httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)
        self.apply = apply
        self.writes = 0

    def get(self, path: str, **params):
        r = self.c.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, body: dict):
        if not self.apply:
            self.writes += 1
            return None
        r = self.c.patch(path, json=body)
        if r.status_code >= 400:
            die(f"PATCH {path} -> {r.status_code} {r.text[:300]}")
        self.writes += 1
        return r.json()

    def post(self, path: str, body: dict):
        if not self.apply:
            self.writes += 1
            return None
        r = self.c.post(path, json=body)
        if r.status_code == 409:  # I-063: already launched, which is the converged state
            return {"already": True, "detail": r.json()}
        if r.status_code >= 400:
            die(f"POST {path} -> {r.status_code} {r.text[:300]}")
        self.writes += 1
        return r.json()


def stagger_due_dates_and_fix_titles(profile: str, apply: bool) -> str:
    """Direct SQL, deliberately — neither of these is an operational event.

    A due date is a plan and a campaign title is a label; writing them through PATCH would
    put "STATUS_CHANGE"-shaped rows in the audit log for things nobody did. The title fix is
    data hygiene: `SC-17V629000-b3b9dfbd` is still called "(MVP verification)" from the day
    the write path was first proved, and that text is visible in the Launched tab.
    """
    import psycopg
    from databricks.sdk import WorkspaceClient

    endpoint = "projects/summer-bootcamp-2026-v2/branches/production/endpoints/primary"
    schema = "bootcamp_students"

    w = WorkspaceClient(profile=profile)
    host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=endpoint).token
    user = w.current_user.me().user_name

    offsets = ", ".join(str(d) for d in DUE_OFFSETS)
    # ORDER BY wo_id so the offset a work order gets is stable across runs, same reason as
    # bucket() above.
    # Single `%`, not `%%`. These statements are executed with NO parameters, so psycopg does
    # no interpolation and never unescapes a doubled `%` — `%%` would reach Postgres literally
    # and break both the modulo and the LIKE pattern.
    due_sql = f"""
        UPDATE {schema}.fleetguard_work_order w
        SET due_date = CURRENT_DATE + (ARRAY[{offsets}])[(r.rn % {len(DUE_OFFSETS)}) + 1]
        FROM (SELECT wo_id, ROW_NUMBER() OVER (ORDER BY wo_id) - 1 AS rn
              FROM {schema}.fleetguard_work_order) r
        WHERE r.wo_id = w.wo_id
    """
    title_sql = f"""
        UPDATE {schema}.fleetguard_service_campaign
        SET title = replace(title, ' (MVP verification)', '')
        WHERE title LIKE '% (MVP verification)'
    """

    with (
        psycopg.connect(
            host=host, user=user, password=token, dbname="databricks_postgres", sslmode="require"
        ) as conn,
        conn.cursor() as cur,
    ):
        if not apply:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.fleetguard_work_order")
            n = cur.fetchone()[0]
            cur.execute(
                f"SELECT COUNT(*) FROM {schema}.fleetguard_service_campaign "
                "WHERE title LIKE '% (MVP verification)'"
            )
            t = cur.fetchone()[0]
            return f"would stagger {n} due dates over {len(DUE_OFFSETS)} offsets; would re-title {t} campaign(s)"
        cur.execute(due_sql)
        staggered = cur.rowcount
        cur.execute(title_sql)
        retitled = cur.rowcount
        conn.commit()
        return f"staggered {staggered} due dates; re-titled {retitled} campaign(s)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8811")
    ap.add_argument("--profile", default="abhi", help="only used for the direct-SQL step")
    ap.add_argument("--apply", action="store_true", help="actually write; default is a dry run")
    args = ap.parse_args()

    api = Api(args.base_url, args.apply)
    mode = "APPLY" if args.apply else "DRY RUN — nothing will be written"
    print(f"=== seed_demo_state · {mode} ===\n")

    # --- preflight ------------------------------------------------------------------------
    try:
        me = api.get("/api/me")
    except httpx.HTTPError as e:
        die(f"cannot reach {args.base_url} — is scripts/run_local_static_dev.sh running? ({e})")
    # `/api/me` reports identity, not authorisation — there is no read-only way to ask "may I
    # approve" that is true under static-dev. `/api/auth/status` looks like the right call and
    # is not: it reads the GitHub session table directly, which static-dev never populates, so
    # it reports signed_in=False for a perfectly working dev server. Authorisation is proved by
    # the first write instead; `die()` surfaces the handler's own 403 text verbatim, which names
    # the principal and says it is not an approver on this deployment.
    if not me.get("user_name"):
        die("this token carries no identity; every write here would 403")
    print(f"identified as {me['user_name']} (source {me.get('token_source')})")

    # --- 1. the third campaign ------------------------------------------------------------
    launched = {c["campaign_id"] for c in api.get("/api/service-campaigns")}
    if THIRD_CAMPAIGN in launched:
        print(f"\n1. {THIRD_CAMPAIGN} already launched — nothing to do")
    else:
        print(f"\n1. launching {THIRD_CAMPAIGN} ({THIRD_TITLE})")
        res = api.post(
            f"/api/campaigns/{THIRD_CAMPAIGN}/service-campaign",
            {
                "title": THIRD_TITLE,
                "rationale": THIRD_RATIONALE,
                "due_in_days": THIRD_DUE_DAYS,
            },
        )
        if res and not res.get("already"):
            print(f"   -> {res['service_campaign_id']}, {res['work_orders_created']} work orders")

    # --- gather the work orders and their components --------------------------------------
    campaigns = {c["service_campaign_id"]: c for c in api.get("/api/service-campaigns")}
    if not args.apply and THIRD_CAMPAIGN not in launched:
        print(
            "   (dry run: the third campaign's work orders do not exist yet and are not counted below)"
        )

    work_orders: list[dict] = []
    for sc_id in campaigns:
        page = api.get("/api/work-orders", service_campaign_id=sc_id, limit=500)
        work_orders.extend(page)
    print(f"\n   {len(work_orders)} work orders across {len(campaigns)} launched campaigns")

    # component comes from the recall behind the service campaign — the same join
    # cost-breakdown uses, so seeded costs land in the buckets the tab actually renders
    component_of: dict[str, str] = {}
    recall_of: dict[str, str] = {}
    for sc_id, c in campaigns.items():
        recall_of[sc_id] = c["campaign_id"]
        component_of[sc_id] = api.get(f"/api/campaigns/{c['campaign_id']}").get("component", "")

    # --- 2. assignment --------------------------------------------------------------------
    # No `limit` param on this route — it returns the whole roster, and `active_only` defaults
    # to true, so everything here is already assignable.
    techs_by_depot: dict[str, list[str]] = {}
    for t in api.get("/api/technicians"):
        techs_by_depot.setdefault(t["depot_id"], []).append(t["technician_id"])
    for ids in techs_by_depot.values():
        ids.sort()

    to_assign = [w for w in work_orders if not w.get("assigned_to")]
    print(f"\n2. assigning technicians — {len(to_assign)} unassigned of {len(work_orders)}")
    unassignable = 0
    for w in to_assign:
        pool = techs_by_depot.get(w["depot_id"], [])
        if not pool:
            unassignable += 1
            continue
        tech = pool[int(bucket(w["wo_id"], "tech") * len(pool))]
        api.patch(f"/api/work-orders/{w['wo_id']}", {"assigned_to": tech})
        w["assigned_to"] = tech
    if unassignable:
        print(
            f"   {unassignable} work orders are at a depot with no active technician — left unassigned"
        )

    # --- 3. status ------------------------------------------------------------------------
    moves: dict[str, int] = {}
    held = 0
    for w in work_orders:
        want = target_status(w["wo_id"], recall_of[w["service_campaign_id"]])
        if want == w["status"]:
            continue
        # A COMPLETED work order that already carries a cost stays COMPLETED. Found on the
        # first live run: one pre-existing row (WO-7b0d5c14a8fc, $75 from the 2026-09-08
        # verification) hashed to IN_PROGRESS, so the status pass moved it out of COMPLETED and
        # left a costed work order that was not finished — which step 4's own comment calls a
        # data error. Because the hash is deterministic, re-running would have recreated it
        # every time, and repairing it by hand would have been undone by the next run.
        if w["status"] == "COMPLETED" and w.get("actual_cost") is not None:
            held += 1
            continue
        api.patch(f"/api/work-orders/{w['wo_id']}", {"status": want})
        w["status"] = want
        moves[want] = moves.get(want, 0) + 1
    if held:
        print(f"   {held} completed-and-costed work orders held at COMPLETED")
    detail = ", ".join(f"{k} {v}" for k, v in sorted(moves.items())) or "none"
    print(f"\n3. status moves — {sum(moves.values())}: {detail}")

    # --- 4. cost --------------------------------------------------------------------------
    costed = 0
    total = 0.0
    for w in work_orders:
        # Cost is logged when the repair is done. An OPEN or CANCELLED work order carrying a
        # cost would be a data error, and `cost-breakdown` reports costed_count against
        # total_work_orders precisely so partial coverage is visible rather than averaged away.
        if w["status"] != "COMPLETED" or w.get("actual_cost") is not None:
            continue
        cost = target_cost(w["wo_id"], component_of[w["service_campaign_id"]])
        api.patch(f"/api/work-orders/{w['wo_id']}", {"actual_cost": cost})
        costed += 1
        total += cost
    print(f"\n4. costs logged — {costed} work orders, ${total:,.2f}")

    # --- 5. due dates + title hygiene -----------------------------------------------------
    print(f"\n5. {stagger_due_dates_and_fix_titles(args.profile, args.apply)}")

    # --- 6. reconcile ---------------------------------------------------------------------
    print(f"\n{'=' * 70}")
    if not args.apply:
        print(f"DRY RUN complete — {api.writes} writes would have been made. Re-run with --apply.")
        return

    after = []
    for sc_id in api.get("/api/service-campaigns"):
        after.extend(
            api.get("/api/work-orders", service_campaign_id=sc_id["service_campaign_id"], limit=500)
        )
    by_status: dict[str, int] = {}
    for w in after:
        by_status[w["status"]] = by_status.get(w["status"], 0) + 1
    breakdown = api.get("/api/cost-breakdown")
    audit = api.get("/api/audit-log", limit=500)

    print(f"APPLIED — {api.writes} writes\n")
    print(
        f"work orders   {len(after)}: "
        + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
    )
    print(f"assigned      {sum(1 for w in after if w.get('assigned_to'))}")
    print("cost by component:")
    for row in breakdown["by_component"]:
        print(
            f"    {row['key'][:52]:52s} ${row['total_actual_cost']:>12,.2f}  ({row['costed_count']}/{row['total_work_orders']})"
        )
    print(f"cost by depot  {len(breakdown['by_depot'])} depots with rows")
    print(f"audit log      {len(audit)} rows returned (limit 500)")

    # The point of the whole exercise: these three tabs must not demo empty.
    assert len(after) > 250, "too few work orders — the Work orders tab is still thin"
    assert sum(1 for w in after if w.get("actual_cost") is not None) > 50, (
        "cost breakdown still thin"
    )
    assert len(breakdown["by_component"]) >= 3, "by_component has fewer than 3 rows"
    assert len(audit) > 200, "audit log still thin"

    # A cost belongs to a finished repair. This is the invariant the status pass now holds
    # COMPLETED rows to; asserting it here is what makes that a check rather than a hope,
    # since the bug it encodes was found in the data and not by reading the code.
    miscosted = [
        w["wo_id"] for w in after if w.get("actual_cost") is not None and w["status"] != "COMPLETED"
    ]
    assert not miscosted, f"cost logged on non-completed work orders: {miscosted[:5]}"
    print("\nall reconciliation checks passed")


if __name__ == "__main__":
    main()
