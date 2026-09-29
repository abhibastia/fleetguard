"""`/readyz` exists because `/healthz` answered `ok` while nothing worked.

Between the two online windows this project's AI Search index is deleted, its agent endpoint is
stopped and its App is stopped — and `/healthz` reported healthy throughout, because it only
ever checked the process. I-115 filed that; this file pins the replacement.

What is asserted here, in order of what would actually hurt:

1. **A failing dependency produces 503, not 200.** The entire point. A readiness endpoint that
   stays green while the demo is broken is worse than no readiness endpoint, because someone
   trusts it.
2. **One check failing does not hide the others.** The value of this route is naming *which*
   thing is down 20 minutes before a demo, so a single raising check must not abort the sweep.
3. **It costs nothing to call.** `serving_endpoints.query` would wake a scale-to-zero container
   and bill for it; the test below fails if anyone ever reaches for it.
4. **It is authenticated**, because the Lakebase check runs under the caller's token.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import fleetguard_api.deps as deps_module
import pytest
from fastapi.testclient import TestClient
from fleetguard_api.main import app
from fleetguard_api.routers import readyz as readyz_module

client = TestClient(app)

#: "source count equals the index count" — the healthy case, distinct from an explicit
#: number and from None (warehouse unavailable).
_SAME = object()


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setenv("FLEETGUARD_DEV_TOKEN", "dapi-test-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    deps_module.get_token_provider.cache_clear()


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return (1,)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cursor()


class _FakeWorkspace:
    """Stands in for the SDK client.

    `query` raises rather than returning a value: waking the agent endpoint is the one thing
    this route must never do, and a mock that quietly answered it would let that regress
    silently. Same reasoning as the `scale_to_zero_enabled` note in the router — the cheap
    wrong call and the correct call look identical at the call site.
    """

    def __init__(
        self,
        *,
        endpoint_ready="READY",
        index_ready=True,
        rows=115_499,
        source_rows=_SAME,
        eval_tags=_SAME,
    ):
        self.serving_endpoints = SimpleNamespace(
            get=lambda name: SimpleNamespace(
                state=SimpleNamespace(
                    ready=SimpleNamespace(value=endpoint_ready), config_update=None
                ),
                config=SimpleNamespace(
                    served_entities=[
                        SimpleNamespace(
                            entity_version="7",
                            entity_name="bootcamp_students.fleetguard.fleetguard_agent",
                        )
                    ]
                ),
            ),
            query=self._forbidden,
        )
        # The release check reads the served version's `eval_hard_gates` tag through the
        # MLflow-on-UC REST endpoint, because `ModelVersionInfo` has no `tags` field. Shape
        # measured live 2026-09-29: `tags` is a list of {key, value} and is OMITTED entirely
        # when the version has none — which the `eval_tags=None` case below reproduces.
        self._eval_tags = (
            {"eval_hard_gates": "passed", "eval_run_id": "abc123"}
            if eval_tags is _SAME
            else eval_tags
        )
        self.api_client = SimpleNamespace(do=self._do)
        self.vector_search_indexes = SimpleNamespace(
            get_index=lambda index_name: SimpleNamespace(
                status=SimpleNamespace(ready=index_ready, indexed_row_count=rows)
            )
        )
        # The index check compares `indexed_row_count` against the source table, because an
        # index can be `ready` and SHORT (I-105 — a sync restarting from zero answers every
        # query without erroring). `source_rows=None` simulates a sleeping warehouse.
        self.statement_execution = SimpleNamespace(
            execute_statement=lambda **k: (
                SimpleNamespace(
                    status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
                    result=SimpleNamespace(
                        data_array=[[str(rows if source_rows is _SAME else source_rows)]]
                    ),
                )
                if source_rows is not None
                else SimpleNamespace(status=None, result=None)
            )
        )

    @staticmethod
    def _forbidden(*a, **k):
        raise AssertionError(
            "readyz queried the serving endpoint — that wakes a scale-to-zero container and "
            "bills for it. Check state with serving_endpoints.get instead."
        )

    def _do(self, method, path, query=None, **k):
        assert method == "GET", "the eval-tag read must not mutate the registry"
        if self._eval_tags is None:
            # An untagged version: the key is absent, not an empty list.
            return {"model_version": {"version": (query or {}).get("version")}}
        return {
            "model_version": {
                "version": (query or {}).get("version"),
                "tags": [{"key": k_, "value": v} for k_, v in self._eval_tags.items()],
            }
        }


def _patch_all(monkeypatch: pytest.MonkeyPatch, *, workspace=None, connect=None) -> None:
    monkeypatch.setattr(readyz_module.db, "connect", connect or (lambda principal: _Conn()))
    monkeypatch.setattr(
        readyz_module.db, "_workspace_client", lambda principal: workspace or _FakeWorkspace()
    )


def _get(monkeypatch: pytest.MonkeyPatch, **kw):
    _patch_all(monkeypatch, **kw)
    return client.get("/api/readyz")


def test_everything_up_is_200_and_says_so(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _get(monkeypatch)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert {c["name"] for c in body["checks"]} == {
        "lakebase",
        "agent_endpoint",
        "search_index",
        "snapshots",
        "release",
    }
    assert all(c["status"] == "ok" for c in body["checks"])
    # Detail is populated on SUCCESS too — "ok" alone is not useful before a demo.
    assert all(c["detail"] for c in body["checks"])
    index = next(c for c in body["checks"] if c["name"] == "search_index")
    assert "115499" in index["detail"].replace(",", "")


def test_a_deleted_index_is_a_503_not_a_green_tick(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact state this system sits in between online windows, and the defect I-115 filed:
    `/healthz` returned `ok` for weeks with no index at all."""

    class _NoIndex(_FakeWorkspace):
        def __init__(self):
            super().__init__()
            self.vector_search_indexes = SimpleNamespace(
                get_index=lambda index_name: (_ for _ in ()).throw(
                    RuntimeError("RESOURCE_DOES_NOT_EXIST: index not found")
                )
            )

    resp = _get(monkeypatch, workspace=_NoIndex())
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["search_index"]["status"] == "down"
    assert "RESOURCE_DOES_NOT_EXIST" in by_name["search_index"]["detail"]
    # ...and the other three still ran and still passed. This is the property that makes the
    # route worth having: it names the broken thing rather than failing as a unit.
    assert by_name["lakebase"]["status"] == "ok"
    assert by_name["agent_endpoint"]["status"] == "ok"
    assert by_name["snapshots"]["status"] == "ok"


def test_a_stopped_agent_endpoint_is_down(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _get(monkeypatch, workspace=_FakeWorkspace(endpoint_ready="NOT_READY"))
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["agent_endpoint"]["status"] == "down"
    assert by_name["search_index"]["status"] == "ok"


def test_lakebase_unreachable_is_down_and_named(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(principal):
        raise OSError("connection refused")

    resp = _get(monkeypatch, connect=_refuse)
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["lakebase"]["status"] == "down"
    assert "connection refused" in by_name["lakebase"]["detail"]
    assert by_name["agent_endpoint"]["status"] == "ok"


def test_a_missing_snapshot_is_down(configured, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A missing snapshot breaks only the two PUBLIC routes, so it is invisible to anyone
    signed in — which is precisely why a readiness check should see it."""
    monkeypatch.setitem(readyz_module.SNAPSHOTS, "corpus", tmp_path / "absent.json")
    resp = _get(monkeypatch)
    assert resp.status_code == 503
    by_name = {c["name"]: c for c in resp.json()["checks"]}
    assert by_name["snapshots"]["status"] == "down"
    assert "corpus" in by_name["snapshots"]["detail"]


def test_snapshot_detail_carries_generated_at(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """Freshness, not just presence — a snapshot from before the last re-export is a stale
    number on a public page, which is the failure I-115 spent a session sweeping up."""
    resp = _get(monkeypatch)
    detail = next(c for c in resp.json()["checks"] if c["name"] == "snapshots")["detail"]
    for name, path in readyz_module.SNAPSHOTS.items():
        assert name in detail
        assert json.loads(path.read_text())["generated_at"] in detail


def test_readyz_requires_a_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike `/healthz`. The Lakebase check runs under the caller's own token because this app
    holds no privileges of its own (§8a) — checking the *app's* access would test something the
    product does not do."""
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "databricks-apps")
    deps_module.get_token_provider.cache_clear()
    assert client.get("/api/readyz").status_code == 401


def test_healthz_is_untouched_and_still_always_ok(configured) -> None:
    """`/readyz` must not turn the container probe into something that can fail."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_a_short_index_is_down_even_when_it_reports_ready(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-117. `ready: true` was treated as sufficient, and it is not.

    I-105 records a sync silently restarting from zero. A partially-synced index answers every
    query without erroring — it simply cannot see the rows it never embedded, which is the
    worst shape this project keeps finding: a confident answer over a truncated corpus. So the
    row count is compared against the source table, and a mismatch is `down`.
    """
    resp = _get(monkeypatch, workspace=_FakeWorkspace(rows=40_000, source_rows=115_499))
    assert resp.status_code == 503
    check = next(c for c in resp.json()["checks"] if c["name"] == "search_index")
    assert check["status"] == "down"
    assert "SHORT" in check["detail"]


def test_a_sleeping_warehouse_does_not_fail_readiness(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source count is the one check here that needs a SQL warehouse. A warehouse scaled
    to zero is not a broken index, so it degrades to a note rather than turning a healthy
    system red — otherwise the cheapest state to be in would look like an outage."""
    resp = _get(monkeypatch, workspace=_FakeWorkspace(source_rows=None))
    assert resp.status_code == 200
    check = next(c for c in resp.json()["checks"] if c["name"] == "search_index")
    assert check["status"] == "ok"
    assert "unavailable" in check["detail"]


def test_release_provenance_is_reported(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Is the live system the thing in the zip?" — named as the biggest practical risk, and
    today it means looking in five separate places. Two of the five are cheap here."""
    monkeypatch.setenv("FLEETGUARD_GIT_SHA", "abc1234")
    check = next(c for c in _get(monkeypatch).json()["checks"] if c["name"] == "release")
    assert check["status"] == "ok"
    assert "agent v7" in check["detail"]
    assert "abc1234" in check["detail"]


def test_release_provenance_never_fails_readiness_on_its_own(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing git SHA means the deploy did not stamp one. Worth reporting; not a reason to
    call a working system unready."""
    monkeypatch.delenv("FLEETGUARD_GIT_SHA", raising=False)
    monkeypatch.delenv("DATABRICKS_BUNDLE_GIT_COMMIT", raising=False)
    resp = _get(monkeypatch)
    assert resp.status_code == 200
    check = next(c for c in resp.json()["checks"] if c["name"] == "release")
    assert check["status"] == "ok"
    assert "unset" in check["detail"]


def test_the_served_version_must_carry_a_passing_eval_tag(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release loop's last inch, closed 2026-09-29.

    `16_evaluate_agent.py` stamps `eval_hard_gates` on the UC model version after the gates
    pass, and until now nothing read it — so "the deployed artefact is the evaluated one" was a
    claim with no check behind it. Reported on success, because the useful thing before a demo
    is the run id, not the word `ok`.
    """
    check = next(c for c in _get(monkeypatch).json()["checks"] if c["name"] == "release")
    assert check["status"] == "ok"
    assert "eval_hard_gates=passed" in check["detail"]
    assert "abc123" in check["detail"], "the run id makes the scores one lookup away"


def test_an_unevaluated_served_version_is_a_503(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Not hypothetical.** All seven registered versions were untagged on 2026-09-29 because
    the evaluation had never been run against any of them (I-118) — the agent was rebuilt,
    redeployed and demoed with its hard gates inert throughout. A check that called that "ok,
    unverified" would reproduce exactly the decorative-gate failure I-118 was filed for.

    Note which absence this is: the registry answered, and its answer was "no such tag". That
    is a release-gate failure, distinct from the registry being unreachable below.
    """
    resp = _get(monkeypatch, workspace=_FakeWorkspace(eval_tags=None))
    assert resp.status_code == 503
    check = next(c for c in resp.json()["checks"] if c["name"] == "release")
    assert check["status"] == "down"
    assert "NEVER EVALUATED" in check["detail"]
    assert "3.3a" in check["detail"], "say which runbook step fixes it, not just that it is wrong"


def test_a_failed_eval_tag_is_a_503(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable via `16_evaluate_agent.py`, which raises before it tags — so a version can
    never carry `failed`. Pinned anyway: the alternative is a branch that silently treats any
    non-`passed` value as acceptable, and "unreachable today" is not the same as "safe to get
    wrong"."""
    resp = _get(monkeypatch, workspace=_FakeWorkspace(eval_tags={"eval_hard_gates": "failed"}))
    assert resp.status_code == 503
    check = next(c for c in resp.json()["checks"] if c["name"] == "release")
    assert check["status"] == "down"
    assert "do not demo" in check["detail"]


def test_an_unreachable_registry_does_not_fail_readiness(
    configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same discipline as the sleeping warehouse above: *unavailable* is not *unhealthy*. The
    distinction from the 503 case is that here the call never produced an answer, so nothing
    was learned about the version either way."""

    class _NoRegistry(_FakeWorkspace):
        def _do(self, *a, **k):
            raise RuntimeError("PERMISSION_DENIED")

    resp = _get(monkeypatch, workspace=_NoRegistry())
    assert resp.status_code == 200
    check = next(c for c in resp.json()["checks"] if c["name"] == "release")
    assert check["status"] == "ok"
    assert "unverified" in check["detail"]
    assert "RuntimeError" in check["detail"], "name the cause, do not just say unverified"


def test_the_eval_tag_read_is_free(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every check in this router is a control-plane read, and that is a constraint rather than
    a coincidence — `/readyz` is documented as safe to poll on the live deployment. This pins
    that the eval-tag read is a GET against the metadata endpoint and touches neither a
    warehouse nor a serving container."""
    seen: list[tuple] = []

    class _Recording(_FakeWorkspace):
        def _do(self, method, path, query=None, **k):
            seen.append((method, path, (query or {}).get("version")))
            return super()._do(method, path, query=query, **k)

    _get(monkeypatch, workspace=_Recording())
    assert seen == [
        ("GET", "/api/2.0/mlflow/unity-catalog/model-versions/get", "7")
    ], "must read the SERVED version (7), by GET, from the metadata endpoint"
