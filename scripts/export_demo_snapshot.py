"""Export the operator console's data into a committed snapshot.

**Why this exists.** The Render deployment cannot hold a Databricks credential. Measured
2026-09-02 on this account: service-principal creation is admin-only, personal access tokens
are disabled for this user ("User does not have permission to use tokens"), Lakebase roles
are all `LAKEBASE_OAUTH_V1` with no password auth, and the account-level OAuth app needed for
U2M requires account admin (E-14). Every route to a durable machine credential is closed
without an administrator granting one.

So the public console runs on a snapshot, exactly as the evidence page already does. The data
is real — pulled from live Lakebase by a developer who *does* have credentials — it is simply
point-in-time rather than live.

**This is a fidelity trade, and the console says so.** Snapshot mode is announced in the UI
rather than hidden, because a demo that silently shows stale data as live is the same class
of dishonesty as an agent reporting a failed query as "no results" (I-050).

When a service principal becomes available, set `FLEETGUARD_DATA_MODE=lakebase` and the same
code paths query live. Nothing else changes.

Usage:
    .venv/bin/python scripts/export_demo_snapshot.py --profile abhi
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("PSYCOPG_IMPL", "")  # let db.py decide; see I-045

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_DB = "databricks_postgres"
PG_SCHEMA = "bootcamp_students"

OUT = Path(__file__).resolve().parents[1] / "app/backend/fleetguard_api/snapshot.json"

# How many campaigns get full detail. The queue lists all of them; detail is only needed for
# the ones a demo will actually open, and every extra campaign is ~25 sample vehicles of
# committed JSON.
DETAIL_LIMIT = 12

QUEUE_SQL = f"""
    SELECT c.campaign_id, c.component, c.park_it, c.do_not_drive,
           COUNT(DISTINCT e.vin) AS vehicles_exposed,
           COUNT(DISTINCT v.depot_id) AS depots_affected,
           c.consequence
    FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
    JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
    JOIN {PG_SCHEMA}.fleetguard_recall_campaign c ON c.campaign_id = e.campaign_id
    GROUP BY c.campaign_id, c.component, c.park_it, c.do_not_drive, c.consequence
    ORDER BY c.park_it DESC, c.do_not_drive DESC, vehicles_exposed DESC
    LIMIT 60
"""

SIGNALS_SQL = f"""
    SELECT signal_id, series_key, make, model, component,
           run_start, run_end, run_len, max_z, complaint_count,
           harm_share, fleet_vehicles, is_live, status
    FROM {PG_SCHEMA}.fleetguard_defect_signal
    ORDER BY fleet_vehicles DESC NULLS LAST, run_end DESC NULLS LAST, max_z DESC
"""

SIGNAL_COUNTS_SQL = f"""
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE is_live) AS live,
           COUNT(*) FILTER (WHERE fleet_vehicles > 0) AS fleet_relevant,
           MAX(as_of_month) AS as_of_month
    FROM {PG_SCHEMA}.fleetguard_defect_signal
"""


def rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def campaign_detail(cur, campaign_id: str) -> dict | None:
    cur.execute(
        f"""SELECT campaign_id, component, park_it, consequence, remedy
            FROM {PG_SCHEMA}.fleetguard_recall_campaign WHERE campaign_id = %s""",
        (campaign_id,),
    )
    head = rows(cur)
    if not head:
        return None

    cur.execute(
        f"""SELECT v.depot_id, COUNT(DISTINCT e.vin) AS n
            FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
            JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
            WHERE e.campaign_id = %s GROUP BY v.depot_id ORDER BY n DESC""",
        (campaign_id,),
    )
    by_depot = {r["depot_id"]: r["n"] for r in rows(cur)}

    cur.execute(
        f"""SELECT e.vin, v.depot_id, v.make, v.model, v.model_year
            FROM {PG_SCHEMA}.fleetguard_vehicle_exposure e
            JOIN {PG_SCHEMA}.fleetguard_vehicle v ON v.vin = e.vin
            WHERE e.campaign_id = %s LIMIT 25""",
        (campaign_id,),
    )
    sample = rows(cur)

    return {
        **head[0],
        "vehicles_exposed": sum(by_depot.values()),
        "by_depot": by_depot,
        "sample_vehicles": sample,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
    user = w.current_user.me().user_name

    with (
        psycopg.connect(
            host=host, user=user, password=token, dbname=PG_DB, sslmode="require"
        ) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(QUEUE_SQL)
        queue = rows(cur)

        details = {}
        for item in queue[:DETAIL_LIMIT]:
            d = campaign_detail(cur, item["campaign_id"])
            if d:
                details[item["campaign_id"]] = d

        cur.execute(SIGNALS_SQL)
        signals = rows(cur)
        cur.execute(SIGNAL_COUNTS_SQL)
        counts = rows(cur)[0]

    snapshot = {
        "queue": queue,
        "campaigns": details,
        "signals": {**counts, "signals": signals},
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": f"{PG_DB}.{PG_SCHEMA} (Lakebase)",
    }

    # Reconcile rather than trust. An empty export would produce a console that looks
    # working and shows nothing — the failure mode this project keeps re-learning.
    assert queue, "no queue rows exported"
    assert details, "no campaign detail exported"
    assert signals, "no signals exported"
    assert counts["fleet_relevant"] > 0, "no fleet-relevant signals; the Emerging tab demos empty"

    OUT.write_text(json.dumps(snapshot, indent=2, default=str) + "\n")
    print(
        f"queue {len(queue)} · campaign detail {len(details)} · signals {len(signals)} "
        f"({counts['fleet_relevant']} fleet-relevant)\nwritten: {OUT}"
    )


if __name__ == "__main__":
    main()
