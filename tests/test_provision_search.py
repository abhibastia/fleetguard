"""`scripts/provision_search.sh` and `docs/RUNBOOK.md` must not drift apart.

The script exists because a documented command sequence is not runnable under deadline
pressure. But shipping both a script and a runbook creates a failure this project has already
had in other forms: two descriptions of one procedure, one of them quietly wrong. The index
configuration is the part where that matters — a missing entry in `columns_to_sync` does not
error, it silently produces an index whose results carry only the key and the embedded text,
and `search_complaints` then cannot filter by harm or name a component. I-040 spent a session
proving those columns had taken.

So these tests read both files and assert they agree. No workspace, no credentials, no
execution — running the script bills.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/provision_search.sh"
RUNBOOK = ROOT / "docs/RUNBOOK.md"


def _script_index_body() -> dict:
    """Extract the `index_body()` heredoc and resolve the shell variables it interpolates.

    Parsed rather than duplicated here: a copy of the expected JSON in this file would be a
    third description of the same thing, which is the problem, not the fix.
    """
    text = SCRIPT.read_text()
    body = re.search(r"index_body\(\) \{\n  cat <<JSON\n(.*?)\nJSON\n\}", text, re.S)
    assert body, "index_body() heredoc not found — did the script's shape change?"
    raw = body.group(1)
    for name in ("INDEX", "ENDPOINT", "SOURCE_TABLE", "EMBEDDING_MODEL"):
        value = re.search(rf'^{name}="([^"]+)"', text, re.M)
        assert value, f"{name} assignment not found in the script"
        resolved = value.group(1)
        # One level of nesting: INDEX and SOURCE_TABLE are built from CATALOG_SCHEMA.
        schema = re.search(r'^CATALOG_SCHEMA="([^"]+)"', text, re.M)
        assert schema
        resolved = resolved.replace("${CATALOG_SCHEMA}", schema.group(1))
        raw = raw.replace("${" + name + "}", resolved)
    return json.loads(raw)


def test_the_script_is_executable() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "provision_search.sh must be executable"


def test_it_parses_as_bash() -> None:
    """`bash -n` only. Running it creates a ~$6.72/day endpoint."""
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_check_is_the_default_mode() -> None:
    """The free, read-only mode must be what a bare invocation does. A script whose default
    provisions billable infrastructure is one typo away from an unnoticed recurring charge."""
    assert re.search(r'^MODE="check"', SCRIPT.read_text(), re.M)


def test_profile_is_never_defaulted() -> None:
    """`abhi` is a shared metastore with ~296 other students' work in it. Every script in this
    repo demands the profile explicitly; a default here would be the one that guessed."""
    text = SCRIPT.read_text()
    assert 'PROFILE=""' in text
    assert "--profile is required" in text


def test_index_config_matches_the_runbook() -> None:
    """The load-bearing assertion. Each of these was verified live in Run 1, and each fails
    silently rather than loudly if it is wrong."""
    body = _script_index_body()
    runbook = RUNBOOK.read_text()

    assert body["primary_key"] == "chunk_id"
    assert body["index_type"] == "DELTA_SYNC"
    spec = body["delta_sync_index_spec"]
    assert spec["pipeline_type"] == "TRIGGERED"
    assert spec["embedding_source_columns"][0]["name"] == "chunk_text"

    # Every value the script will send must appear in the runbook's own command block, so the
    # two cannot disagree about what gets created.
    for needle in (
        body["name"],
        body["endpoint_name"],
        spec["source_table"],
        spec["embedding_source_columns"][0]["embedding_model_endpoint_name"],
    ):
        assert needle in runbook, f"{needle!r} is in the script but not in docs/RUNBOOK.md"


def test_columns_to_sync_is_complete() -> None:
    """I-040's finding: `columns_to_sync` did not appear in the returned spec, and if it had
    been ignored, harm-filtered retrieval (§4.3) would have been impossible. A column dropped
    from this list produces an index that builds cleanly and retrieves uselessly."""
    columns = _script_index_body()["delta_sync_index_spec"]["columns_to_sync"]
    assert set(columns) == {
        "chunk_id",
        "complaint_id",
        "make",
        "model",
        "component",
        "any_harm",
        "chunk_text",
    }
    assert columns == sorted(columns, key=lambda c: columns.index(c))  # order is stable
    for column in columns:
        assert column in RUNBOOK.read_text()


def test_the_row_count_is_read_not_hard_coded() -> None:
    """115,499 was true for about twelve hours. I-111 rescoped the source, I-115 widened the
    fleet match again, and Run 2 re-measures it. A literal here would be wrong by construction,
    which is the specific mistake this round of review was about."""
    # Comments are excluded deliberately: the script's header *explains* the history of that
    # number, and forbidding it there would push the reasoning out of the file that needs it.
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "115499" not in code.replace(",", "")
    assert "COUNT(*)" in code


def test_it_watches_for_a_drop_not_a_plateau() -> None:
    """I-105's only visible symptom was `indexed_row_count` going backwards. A poll that waits
    for `ready` alone sits happily through a restart-from-zero."""
    text = SCRIPT.read_text()
    assert "rows < last" in text
    assert "list-pipeline-events" in text


def test_it_does_not_tear_down() -> None:
    """Teardown stays a deliberate human command. A script that can both create and delete the
    demo's retrieval corpus is one flag away from deleting it the morning of a submission."""
    text = SCRIPT.read_text()
    assert "delete-index" not in text
    assert "delete-endpoint" not in text
