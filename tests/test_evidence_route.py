"""The evidence route is unauthenticated *on purpose* — lock that in, both directions.

Two failure modes this guards against, and they point opposite ways:
  - someone puts `/evidence` behind auth, and the public page (the whole reason the Render
    deployment exists) silently stops showing the measured result;
  - someone widens the exemption, and `/queue` — which reads fleet exposure — becomes public.

Both are one-line mistakes. Neither is obvious in review.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # render-u2m with no session is the public deployment's exact configuration.
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "render-u2m")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    return TestClient(main.app)


def test_evidence_is_public(client: TestClient) -> None:
    resp = client.get("/api/evidence")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Pin the published claim. If the snapshot is regenerated and these move, that is a
    # result change and should be a deliberate edit here, not a silent one.
    assert body["real"]["rate_pct"] == 16.0
    assert body["placebo"]["rate_pct"] == 11.1
    assert body["lift"] == 1.44
    assert body["p_value"] < 0.01
    # Provenance is part of the contract, not decoration.
    assert body["source_table"].endswith("gold_lead_time_summary")
    assert body["generated_at"]
    # Model B ships on the same page (proposal §6: "published on the application's own
    # page"). Precision must be < 1.0 here specifically — a perfect score on this route is
    # the I-060 leakage signature, not a result to be proud of.
    mb = body["model_b"]
    assert 0 < mb["precision"] < 1.0
    assert 0 < mb["recall"] <= 1.0
    assert mb["golden_set_size"] >= 150


def test_queue_stays_gated(client: TestClient) -> None:
    assert client.get("/api/queue").status_code == 401


def test_chat_stays_gated(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401


def test_signals_stay_gated(client: TestClient) -> None:
    # Signals read fleet exposure. Evidence is the *only* public route; if this ever
    # returns 200 the exemption has been widened past what §8a permits.
    assert client.get("/api/signals").status_code == 401
