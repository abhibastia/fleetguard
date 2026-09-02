"""Export the measured backtest into a committed snapshot the public console can serve.

Why a snapshot rather than a request-time query: the evidence page is the **public** surface
(§8a). Reading `gold_lead_time_summary` at request time would require a Databricks credential
on Render, which the architecture explicitly forbids — the URL is public. So the numbers are
pulled here, by a developer with their own credentials, and committed with provenance.

That is still a strict improvement over hand-typed constants: the figures are *derived* from
the source table, the derivation is in this file, and the snapshot records where and when it
came from. If the backtest is re-run, this is re-run.

Usage:
    .venv/bin/python scripts/export_evidence.py --profile abhi
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

SOURCE_TABLE = "bootcamp_students.fleetguard.gold_lead_time_summary"
WAREHOUSE_ID = "b15d3d6f837ba428"
OUT = Path(__file__).resolve().parents[1] / "app/backend/fleetguard_api/evidence.json"

STATEMENT = f"SELECT arm, n, detected, detect_rate_pct, median_lead_days FROM {SOURCE_TABLE}"


def two_proportion_z(d1: int, n1: int, d2: int, n2: int) -> tuple[float, float]:
    """Two-proportion z-test — the same statistic quoted as z ≈ 2.62, p ≈ 0.009.

    Recomputed from the arm counts rather than copied, so the published claim and the
    displayed claim cannot drift apart.
    """
    p1, p2 = d1 / n1, d2 / n2
    pooled = (d1 + d2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se
    p_two_sided = math.erfc(abs(z) / math.sqrt(2))
    return z, p_two_sided


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    stmt = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=STATEMENT, wait_timeout="50s"
    )
    # Same discipline as I-050: a non-SUCCEEDED statement returns no rows without raising,
    # and silently writing an empty snapshot would blank the evidence page.
    if not stmt.status or stmt.status.state != StatementState.SUCCEEDED:
        state = stmt.status.state if stmt.status else "UNKNOWN"
        err = stmt.status.error.message if (stmt.status and stmt.status.error) else ""
        raise SystemExit(f"query {state}: {err}")

    rows = (stmt.result.data_array or []) if stmt.result else []
    arms = {
        r[0]: {
            "n": int(r[1]),
            "detected": int(r[2]),
            "rate_pct": float(r[3]),
            "median_lead_days": float(r[4]),
        }
        for r in rows
    }

    real = next(v for k, v in arms.items() if k.startswith("REAL"))
    placebo = next(v for k, v in arms.items() if k.startswith("PLACEBO"))

    z, p = two_proportion_z(real["detected"], real["n"], placebo["detected"], placebo["n"])

    snapshot = {
        "real": real,
        "placebo": placebo,
        "lift": round(real["rate_pct"] / placebo["rate_pct"], 2),
        "z": round(z, 2),
        "p_value": round(p, 4),
        "source_table": SOURCE_TABLE,
        "statement": STATEMENT,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    OUT.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(json.dumps(snapshot, indent=2))
    print(f"\nwritten: {OUT}")


if __name__ == "__main__":
    main()
