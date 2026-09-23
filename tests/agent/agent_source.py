"""Loads the real agent module out of `src/agent/14_fleetguard_agent.py`.

**Why this exists**, and why the tests do not import a hand-copied reimplementation: the
agent is written as a `%%writefile` cell inside a Databricks notebook, so the code that
actually ships is the `# MAGIC `-prefixed body of that one cell. Same discipline as
`tests/pipelines/sql_extract.py` — exercise the committed text, not a copy of it that can
drift.

The module's imports are all Databricks/MLflow, none of which belong in CI, so they are
stubbed before exec. The stubs are deliberately *thin*: they exist to let the module body
execute, and every one of them raises if a test actually calls through to it. A stub that
silently returned a plausible value would let a test pass against behaviour the real SDK
does not have, which is this project's most-repeated failure mode.

`WorkspaceClient` is the one exception — it is instantiated at module level (`w = ...`), so
it returns an object whose every attribute access raises on *use* rather than on creation.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

AGENT_NOTEBOOK = Path(__file__).resolve().parents[2] / "src" / "agent" / "14_fleetguard_agent.py"

MAGIC = "# MAGIC "


def agent_source() -> str:
    """The body of the `%%writefile fleetguard_agent.py` cell, de-MAGIC'd."""
    lines = AGENT_NOTEBOOK.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# MAGIC %%writefile"))
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.startswith(MAGIC):
            body.append(ln[len(MAGIC) :])
        elif ln.strip() == "# MAGIC":
            body.append("")
        else:
            # The cell ends at the first line that is not part of it. `# COMMAND ----------`
            # is the usual terminator; anything else would mean the notebook's structure
            # changed and this extractor should be fixed rather than worked around.
            break
    if not body:
        raise AssertionError(f"no %%writefile cell body found in {AGENT_NOTEBOOK}")
    return "\n".join(body)


class _Exploding:
    """Any attribute access is fine; any *call* raises. Lets `w.foo.bar` resolve during
    import while making an un-stubbed SDK call in a test loud instead of plausible."""

    def __init__(self, path: str = "stub") -> None:
        self._path = path

    def __getattr__(self, name: str) -> Any:
        return _Exploding(f"{self._path}.{name}")

    def __call__(self, *a: Any, **k: Any) -> Any:
        raise AssertionError(f"test called un-stubbed Databricks/MLflow API: {self._path}")


def _stub_modules() -> dict[str, types.ModuleType]:
    def mod(name: str, **attrs: Any) -> types.ModuleType:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    def passthrough_decorator(*a: Any, **k: Any):
        """`@mlflow.trace(span_type=...)` — returns the function unchanged."""
        def wrap(fn):
            return fn
        return wrap

    class _ModelConfig:
        """Feeds the module the same four keys `agent_config.yaml` carries in the workspace."""

        def __init__(self, *a: Any, **k: Any) -> None:
            self._d = {
                "catalog": "bootcamp_students",
                "schema": "fleetguard",
                "llm_endpoint": "test-llm",
                "warehouse_id": "test-warehouse",
            }

        def get(self, key: str) -> Any:
            return self._d.get(key)

    class _ResponsesAgent:
        """Only the two helpers the agent actually calls off its base class."""

        def create_text_output_item(self, text: str, id: str) -> dict:
            return {"type": "message", "id": id, "content": [{"type": "output_text", "text": text}]}

    class _Response:
        def __init__(self, output: list) -> None:
            self.output = output

    mlflow_mod = mod(
        "mlflow",
        trace=passthrough_decorator,
        openai=mod("mlflow.openai", autolog=lambda *a, **k: None),
    )
    return {
        "mlflow": mlflow_mod,
        "mlflow.openai": mlflow_mod.openai,
        "mlflow.entities": mod("mlflow.entities", SpanType=_Exploding("SpanType")),
        "mlflow.models": mod(
            "mlflow.models", ModelConfig=_ModelConfig, set_model=lambda *a, **k: None
        ),
        "mlflow.pyfunc": mod("mlflow.pyfunc", ResponsesAgent=_ResponsesAgent),
        "mlflow.types": mod("mlflow.types"),
        "mlflow.types.responses": mod(
            "mlflow.types.responses",
            ResponsesAgentRequest=_Exploding("ResponsesAgentRequest"),
            ResponsesAgentResponse=_Response,
            ResponsesAgentStreamEvent=_Exploding("ResponsesAgentStreamEvent"),
        ),
        "databricks": mod("databricks"),
        "databricks.sdk": mod("databricks.sdk", WorkspaceClient=lambda *a, **k: _Exploding("w")),
        "databricks.sdk.service": mod("databricks.sdk.service"),
        "databricks.sdk.service.sql": mod(
            "databricks.sdk.service.sql",
            StatementParameterListItem=lambda **k: k,
            StatementState=_Exploding("StatementState"),
        ),
    }


def load_agent_module() -> types.ModuleType:
    """Exec the extracted source as a module, with Databricks/MLflow stubbed out.

    Returns a fresh module each call, so a test that monkey-patches a tool function or
    primes `_evidence_cache` cannot leak into the next one.
    """
    stubs = _stub_modules()
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        m = types.ModuleType("fleetguard_agent_under_test")
        m.__file__ = str(AGENT_NOTEBOOK)
        exec(compile(agent_source(), "fleetguard_agent.py", "exec"), m.__dict__)
        return m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
