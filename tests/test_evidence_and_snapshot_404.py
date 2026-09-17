"""Two remaining error branches: `evidence.py`'s missing-snapshot-file 503 (I-050's lesson —
loud failure, not zeros), and `queue.py`'s snapshot-mode 404 for a campaign the exporter never
captured (the live-mode 404 is covered by `test_queue_routes.py::test_unknown_campaign_is_404`).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.routers import evidence, queue
from fleetguard_api.routers.evidence import evidence as evidence_handler
from fleetguard_api.routers.queue import get_campaign

USER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")


def test_evidence_503s_when_the_snapshot_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "SNAPSHOT", tmp_path / "does-not-exist.json")
    with pytest.raises(HTTPException) as exc:
        evidence_handler()
    assert exc.value.status_code == 503
    assert "export_evidence.py" in exc.value.detail


def test_campaign_not_in_the_demo_snapshot_is_a_404(monkeypatch):
    monkeypatch.setattr(queue.snapshot, "is_snapshot", lambda: True)
    monkeypatch.setattr(queue.snapshot, "campaign", lambda campaign_id: None)

    with pytest.raises(HTTPException) as exc:
        get_campaign(USER, "NOT-CAPTURED", depot_id=None, sample=25)

    assert exc.value.status_code == 404
    assert "demo snapshot" in exc.value.detail
