"""`actual_cost` must be a finite number — and nothing downstream was enforcing that.

**The bug (I-117), found by a fourth external review.** `update_work_order` guarded only
`actual_cost < 0`. That guard is blind to the two values that matter most:

    float("nan") < 0   ->  False
    float("inf") < 0   ->  False

Pydantic v2 allows non-finite floats by default, and Python's `json.loads` accepts the bare
literals `NaN` and `Infinity`, so both were reachable **over the wire** — which is why the
tests here post raw JSON rather than constructing the model. Only `-Infinity` was ever caught,
and only incidentally.

**The database did not stop it either.** Postgres `numeric` accepts `'NaN'` and orders it
above every number, so `fg_wo_actual_cost_nonnegative` (`actual_cost >= 0`) passes it too. Two
independent layers, both waving through the same value — the shape of failure this project
keeps finding, where every individual check passes and the composition is wrong.

**Why it is worth a file of its own.** A single poisoned row makes `SUM(actual_cost)` NaN for
its whole depot, and that sum is the cost figure the console's breakdown renders and
`docs/DEMO.md` quotes. Nothing errors; a headline number just quietly becomes meaningless.
"""

from __future__ import annotations

import math

import fleetguard_api.deps as deps_module
import pytest
from fakes import FakeCursor, install
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fleetguard_api.auth.tokens import Principal
from fleetguard_api.main import app
from fleetguard_api.routers import work_orders
from fleetguard_api.routers.work_orders import WorkOrderUpdate, update_work_order
from pydantic import ValidationError

APPROVER = Principal(token="tok", user_name="ops@example.com", source="databricks-apps")

client = TestClient(app)


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLEETGUARD_AUTH_MODE", "static-dev")
    monkeypatch.setenv("FLEETGUARD_DEV_TOKEN", "dapi-test-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    deps_module.get_token_provider.cache_clear()
    return client


def _patch_raw(c: TestClient, body: str):
    """PATCH with a hand-written JSON body.

    Deliberately not `json=` — `json.dumps(float("nan"))` emits `NaN`, but going through the
    model in Python would skip the parsing step that made this reachable. The literal on the
    wire is the thing under test.
    """
    return c.patch(
        "/api/work-orders/WO-1", content=body, headers={"content-type": "application/json"}
    )


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_cost_is_rejected_on_the_wire(wire: TestClient, literal: str) -> None:
    """All three, including `-Infinity` — which the old `< 0` guard happened to catch, so it
    would have passed either way. Parametrising all three keeps the reason for rejection the
    same across them instead of resting on a coincidence for one."""
    resp = _patch_raw(wire, '{"actual_cost": ' + literal + "}")
    assert resp.status_code == 422, resp.text
    assert "actual_cost" in resp.text


def test_the_422_body_is_itself_serialisable(wire: TestClient) -> None:
    """The second-order bug, which the review that found the first one did not reach.

    `allow_inf_nan=False` rejects the value — and then FastAPI's default handler echoes it
    back under `input`, Starlette's JSONResponse sets `allow_nan=False`, and serialisation
    raises *after* the handler returned. The caller gets a **500 with a stack trace** instead
    of a 422. So the schema fix alone traded a silent corruption for a loud crash.

    `main.validation_error_handler` sanitises non-finite floats out of the error body. This
    asserts the whole round trip: correct status, parseable body, field named.
    """
    resp = _patch_raw(wire, '{"actual_cost": NaN}')
    assert resp.status_code == 422, resp.text
    body = resp.json()  # raises if the response is not valid JSON
    assert "actual_cost" in str(body["detail"])
    # The offending value survives as text rather than vanishing — a 422 that cannot say what
    # it rejected is only half an error message.
    assert "nan" in str(body["detail"]).lower()


def test_a_finite_cost_still_passes_validation(wire: TestClient) -> None:
    """The guard must not have closed the door on the normal case. This gets past validation
    and fails later for want of a database, which is exactly far enough to prove the point —
    a 422 here would mean the fix over-rejected."""
    resp = _patch_raw(wire, '{"actual_cost": 1234.56}')
    assert resp.status_code != 422, resp.text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_the_model_itself_refuses_non_finite_values(value: float) -> None:
    """Enforced at the schema, not in the handler, so the next endpoint to accept a float
    inherits it rather than having to remember."""
    with pytest.raises(ValidationError):
        WorkOrderUpdate(actual_cost=value)


def test_the_handler_guard_holds_even_if_the_schema_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt and braces. `model_construct` skips validation the way constructing the model in
    code does, and a NaN cost is worth refusing on that path too — because everything
    downstream of here accepts it: the `< 0` check, and Postgres `numeric`."""
    monkeypatch.setenv("FLEETGUARD_APPROVERS", "ops@example.com")
    monkeypatch.setattr(work_orders.snapshot, "is_snapshot", lambda: False)
    install(monkeypatch, work_orders, FakeCursor({}))

    body = WorkOrderUpdate.model_construct(actual_cost=float("nan"))
    body.__pydantic_fields_set__ = {"actual_cost"}

    with pytest.raises(HTTPException) as exc:
        update_work_order(APPROVER, "WO-1", body)

    assert exc.value.status_code == 400
    assert "finite" in exc.value.detail


def test_the_old_guard_could_not_have_caught_this() -> None:
    """Pins *why* the fix is `math.isfinite` and not a tightened comparison. If this ever
    starts failing, Python's float semantics changed and the reasoning above needs re-reading
    — it is the whole basis for the bug being invisible."""
    assert not (float("nan") < 0), "NaN compares False against everything"
    assert not (float("inf") < 0)
    assert not math.isfinite(float("nan"))
    assert not math.isfinite(float("inf"))
