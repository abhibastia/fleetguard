"""Export corpus row counts into a committed snapshot the public console can serve.

Same reasoning as `scripts/export_evidence.py`, which this mirrors: the Home page answers
without an identity (§8a), so it cannot query anything at request time — there is no caller
credential to run `COUNT(*)` under. The counts are pulled here, by a developer with their own
credentials, and committed with provenance.

**Why not just type the numbers into the JSX.** Because that is precisely what I-115 spent a
session undoing. I-111 rescoped the AI Search source and `1.75M` was left asserted on three
user-facing surfaces — `README.md`, `Assistant.tsx` and a rendered diagram — hours after it
became wrong, with nothing anywhere to notice. A derived-and-committed figure has a script that
regenerates it and a `generated_at` that says how old it is.

**One count here is expected to move.** `silver_complaint_chunk_indexed` was 115,499 under the
old exact-spelling scope; I-115 widened the join to EXACT + MODEL_VARIANT, a strict superset
that has not been measured yet because the warehouse is torn down between submission windows.
Re-run this in Run 2 and Home picks up the real figure on its own.

Usage:
    .venv/bin/python scripts/export_corpus.py --profile abhi
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

SCHEMA = "bootcamp_students.fleetguard"
WAREHOUSE_ID = "b15d3d6f837ba428"
OUT = Path(__file__).resolve().parents[1] / "app/backend/fleetguard_api/corpus.json"

#: (json key, table). Bronze counts are the lakehouse-scale claim; the gold pair is the
#: synthetic fleet registry; `rag_chunks` is the AI Search source table.
COUNTS: list[tuple[str, str]] = [
    ("complaints", "bronze_complaints"),
    ("tsbs", "bronze_tsbs"),
    ("recalls", "bronze_recalls"),
    ("investigations", "bronze_investigations"),
    ("fleet_vehicles", "gold_fleet_vehicle"),
    ("fleet_depots", "gold_fleet_depot"),
    ("rag_chunks", "silver_complaint_chunk_indexed"),
]

# One statement, not seven round trips — and written out into the snapshot so the derivation
# is legible from the committed artefact alone, exactly like `evidence.json`'s.
STATEMENT = " UNION ALL ".join(
    f"SELECT '{key}' AS k, COUNT(*) AS n FROM {SCHEMA}.{table}" for key, table in COUNTS
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    stmt = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=STATEMENT, wait_timeout="50s"
    )
    # Same discipline as I-050 and export_evidence.py: a non-SUCCEEDED statement returns no
    # rows *without raising*, and silently writing an empty snapshot would put zeros on the
    # landing page — which reads as "this system has no data", the worst available answer.
    if not stmt.status or stmt.status.state != StatementState.SUCCEEDED:
        state = stmt.status.state if stmt.status else "UNKNOWN"
        err = stmt.status.error.message if (stmt.status and stmt.status.error) else ""
        raise SystemExit(f"query {state}: {err}")

    rows = (stmt.result.data_array or []) if stmt.result else []
    counts = {r[0]: int(r[1]) for r in rows}

    missing = [key for key, _ in COUNTS if key not in counts]
    if missing:
        raise SystemExit(f"query succeeded but returned no row for: {', '.join(missing)}")
    if any(v == 0 for v in counts.values()):
        zeros = [k for k, v in counts.items() if v == 0]
        raise SystemExit(
            f"refusing to write a snapshot with zero rows for: {', '.join(zeros)}. "
            "A real zero here means the table is empty, which is a data problem, not a count."
        )

    snapshot = {
        **counts,
        # Precomputed rather than summed in the frontend, so the page and this script cannot
        # disagree about what "bronze" includes.
        "bronze_total": sum(counts[k] for k in ("complaints", "tsbs", "recalls", "investigations")),
        "source_schema": SCHEMA,
        "statement": STATEMENT,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    OUT.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(json.dumps(snapshot, indent=2))
    print(f"\nwritten: {OUT}")


if __name__ == "__main__":
    main()
