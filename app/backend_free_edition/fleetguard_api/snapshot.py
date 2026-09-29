"""Snapshot data source — the credential-free read path.

Lets the console run with no Databricks credential at all, serving committed data captured
from live Lakebase by a developer who *does* have one. Built for a host that could hold no
credential (see `scripts/export_demo_snapshot.py` for the four routes that were checked and
are all closed on this account); kept because "show the console without a token" is useful
on its own — offline work, and a demo that cannot reach the workspace.

**Two rules govern this module.**

1. **Never silently.** `FLEETGUARD_DATA_MODE` must say `snapshot` explicitly; the default is
   `lakebase`. A deployment that quietly served stale data as live would be the same class of
   failure as a tool reporting an error as "no results" (I-050), and it would be harder to
   notice because the numbers look plausible.
2. **Say so in the UI.** `captured_at` is exposed through the API so the console can label
   what the viewer is looking at. Honesty about freshness is part of the product, not a
   caveat hidden in a README.

Writes are handled separately — see `routers/approval.py`. A snapshot is read-only by
construction, and pretending otherwise would be worse than refusing.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshot.json"


def data_mode() -> str:
    """`lakebase` (default) or `snapshot`. Read per call so tests can flip it."""
    return os.getenv("FLEETGUARD_DATA_MODE", "lakebase").strip().lower()


def is_snapshot() -> bool:
    return data_mode() == "snapshot"


@lru_cache(maxsize=1)
def load() -> dict:
    """Parsed once per process. The file is committed and immutable at runtime."""
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(
            f"{SNAPSHOT_PATH} missing — run scripts/export_demo_snapshot.py, "
            "or unset FLEETGUARD_DATA_MODE=snapshot"
        )
    return json.loads(SNAPSHOT_PATH.read_text())


def captured_at() -> str | None:
    return load().get("captured_at")


def queue(limit: int = 50) -> list[dict]:
    return load()["queue"][:limit]


def campaign(campaign_id: str) -> dict | None:
    """Detail exists only for the campaigns the exporter captured.

    Returning `None` for the rest is correct: the alternative — synthesising a detail page
    from the queue row — would show an operator a campaign page with no depot breakdown and
    no sample vehicles, which reads as "this campaign affects nothing".
    """
    return load()["campaigns"].get(campaign_id)


def signals(fleet_only: bool = False, limit: int = 50) -> dict:
    s = load()["signals"]
    rows = s["signals"]
    if fleet_only:
        rows = [r for r in rows if (r.get("fleet_vehicles") or 0) > 0]
    return {
        "total": s["total"],
        "live": s["live"],
        "fleet_relevant": s["fleet_relevant"],
        "as_of_month": s.get("as_of_month"),
        "signals": rows[:limit],
    }
