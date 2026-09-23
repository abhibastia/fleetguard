"""`/api/corpus` is unauthenticated on purpose — and it is the SECOND such route, not a
widening of the first.

`tests/test_evidence_route.py` guards the public exemption in both directions. This file does
the same job for corpus, and the pair together are what stop the exemption drifting: if a
third route ever answers without a session, one of these two files should be the reason
someone had to think about it.

The specific risk being guarded: corpus counts rows in `gold_fleet_vehicle`. A fleet roster is
exactly the kind of thing that must not be public — so what is asserted below is that the
*cardinality* is served and the *contents* are not. `/queue` reads the same registry joined to
campaigns and stays gated.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SNAPSHOT = Path(__file__).resolve().parents[1] / "app/backend/fleetguard_api/corpus.json"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # A configured mode whose provider resolves nobody — what an unauthenticated caller looks
    # like on a real deployment. Same fixture shape as test_evidence_route.py.
    monkeypatch.setitem(os.environ, "FLEETGUARD_AUTH_MODE", "databricks-apps")
    from fleetguard_api import deps, main

    deps.get_token_provider.cache_clear()
    return TestClient(main.app)


def test_corpus_is_public(client: TestClient) -> None:
    resp = client.get("/api/corpus")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Pin the measured corpus. These are the figures CLAUDE.md records as ground truth and
    # `export_corpus.py` reproduced exactly; if they move, that is either a re-ingest or a bug,
    # and either way it should be a deliberate edit here rather than a silent drift.
    assert body["complaints"] == 2_240_289
    assert body["tsbs"] == 5_801_279
    assert body["recalls"] == 244_925
    assert body["investigations"] == 154_367
    assert body["fleet_vehicles"] == 20_000
    assert body["fleet_depots"] == 60

    # Derived in the exporter, not the frontend, so the page cannot disagree about what
    # "bronze" includes.
    assert body["bronze_total"] == (
        body["complaints"] + body["tsbs"] + body["recalls"] + body["investigations"]
    )

    # Provenance is part of the contract, not decoration — same rule as /evidence.
    assert body["source_schema"].endswith("fleetguard")
    assert "COUNT(*)" in body["statement"]
    assert body["generated_at"]


def test_rag_chunks_is_served_but_not_pinned(client: TestClient) -> None:
    """`rag_chunks` is deliberately NOT asserted to a value, unlike every other count.

    I-115 widened the AI Search source from exact make/model spelling to the
    EXACT + MODEL_VARIANT tiers, which is a strict superset of the 115,499 measured for the
    old scope. The job that rebuilds the table runs in Run 2, so the committed snapshot still
    carries the old figure and will change when it is re-exported. Pinning it would turn a
    planned, expected update into a test failure.

    What IS pinned is the floor: the widened predicate can only add rows.
    """
    body = client.get("/api/corpus").json()
    assert body["rag_chunks"] >= 115_499


def test_the_roster_itself_stays_gated(client: TestClient) -> None:
    """Counting the fleet is public; reading it is not."""
    assert client.get("/api/queue").status_code == 401
    assert client.get("/api/depot-risk").status_code == 401


def test_missing_snapshot_is_a_loud_503_not_zeros(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """I-050's rule applied to a page: "0 complaints" reads as "this system has no data",
    which is a far worse answer than "the numbers failed to load"."""
    from fleetguard_api.routers import corpus as corpus_router

    monkeypatch.setattr(corpus_router, "SNAPSHOT", tmp_path / "absent.json")
    resp = client.get("/api/corpus")
    assert resp.status_code == 503
    assert "export_corpus" in resp.json()["detail"]


def test_committed_snapshot_matches_the_model() -> None:
    """The file in git is what ships — parse it directly, not through the route, so a
    malformed commit fails here rather than at request time on the deployed App."""
    from fleetguard_api.routers.corpus import CorpusOut

    CorpusOut(**json.loads(SNAPSHOT.read_text()))
