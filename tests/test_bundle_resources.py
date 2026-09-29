"""Bundle resource assertions — the credential-free half of `bundle validate`.

**Why this file exists.** `databricks bundle validate` cannot run without workspace
credentials (measured 2026-09-10: with an empty config file it fails on
`default auth: cannot configure default credentials` before it checks anything). CI has no
credentials by design and should not get any, so the bundle's YAML would otherwise be the
one part of the repo with no automated check at all — while being the part that decides what
runs in the workspace.

These tests cover what the CLI cannot tell us offline and what actually goes wrong:

1. **Every `notebook_path` resolves to a file that exists.** This is the failure the bundle
   was built to eliminate, arriving from the other direction. Before the bundle, jobs pointed
   at hand-synced workspace copies and *those* drifted; now they point at repo paths, so a
   renamed or moved notebook breaks a job silently at deploy time instead. `bundle deploy`
   uploads the file tree without checking that anything references it.
2. **Every job name keeps the `fleetguard-` prefix.** The jobs namespace on this workspace is
   flat and shared — `jobs list` returns ~300 jobs belonging to ~296 other students, several
   with names that read like ours. Every destructive operation in the runbooks is scoped by
   that prefix, so a job that loses it becomes invisible to the safety rule.
3. **The App resource still declares its OBO scopes.** Dropping `user_api_scopes` does not
   fail a deploy; it produces an app that returns `403 Invalid scope, required scopes:
   postgres` on every Lakebase route, with nothing to distinguish it from a consent problem
   (I-083, I-086).

Nothing here contacts Databricks. `yaml` arrives via `uvicorn[standard]` in
`app/backend/requirements.txt` and is pinned explicitly in `requirements-dev.txt`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RESOURCES = REPO / "resources"
BUNDLE_ROOT = REPO / "databricks.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _resource_files() -> list[Path]:
    return sorted(RESOURCES.glob("*.yml"))


def _resources_of(kind: str) -> list[tuple[Path, str, dict]]:
    """Every declared resource of one kind, as (file, key, body)."""
    out = []
    for f in _resource_files():
        for key, body in (_load(f).get("resources", {}).get(kind) or {}).items():
            out.append((f, key, body))
    return out


def test_bundle_root_parses_and_has_expected_targets():
    root = _load(BUNDLE_ROOT)
    assert root["bundle"]["name"] == "fleetguard"

    targets = root["targets"]
    # `prod` (abhi, the shared bootcamp metastore — the submission deliverable) and
    # `free_edition` (a wholly separate account, isolated agent/app testing only) are the
    # only two targets expected. A `mode: development` variant of either would name-prefix
    # every resource — fine on free_edition's own account, but `prod` shares its namespace
    # with ~296 other students and would also pause the `table_update` trigger the
    # sub-5-minute Postgres->gold path depends on. If someone adds a third target, or a
    # `mode: development` one, this test is where they should have to argue for it.
    assert set(targets) == {"prod", "free_edition"}, (
        "only prod and free_edition targets are expected; see databricks.yml"
    )
    for name in ("prod", "free_edition"):
        assert targets[name]["mode"] == "production"
        assert targets[name]["run_as"]["user_name"]
        assert targets[name]["workspace"]["host"]
    # The two targets must never point at the same workspace or the same deployed path —
    # that would make a `free_edition` deploy silently collide with `prod`'s live objects.
    assert targets["prod"]["workspace"]["host"] != targets["free_edition"]["workspace"]["host"]
    assert targets["prod"]["run_as"]["user_name"] != targets["free_edition"]["run_as"]["user_name"]


def test_every_resource_file_parses():
    files = _resource_files()
    assert files, "no resource files found — did resources/ move?"
    for f in files:
        doc = _load(f)
        assert "resources" in doc, f"{f.name} declares no resources"


@pytest.mark.parametrize("kind", ["jobs", "pipelines", "apps", "dashboards"])
def test_some_resource_of_each_kind_is_declared(kind):
    assert _resources_of(kind), f"no {kind} declared in resources/"


def test_every_notebook_path_exists():
    missing = []
    checked = 0
    for f, key, job in _resources_of("jobs"):
        for task in job.get("tasks", []):
            path = (task.get("notebook_task") or {}).get("notebook_path")
            if path is None:
                continue
            checked += 1
            # Paths are relative to the resource file, per DABs path resolution.
            resolved = (f.parent / path).resolve()
            if not resolved.is_file():
                missing.append(f"{key}/{task['task_key']} -> {path}")
    assert not missing, "job notebooks that do not exist in the repo:\n  " + "\n  ".join(missing)
    # Without this the test passes when it checked nothing — a glob that stops matching, or a
    # resource file saved as `.yaml`, would read as success.
    assert checked >= 17, f"only {checked} notebook paths checked; expected every bundled job"


def test_every_job_name_carries_the_fleetguard_prefix():
    bad = [
        f"{key}: {job.get('name')!r}"
        for _f, key, job in _resources_of("jobs")
        if not str(job.get("name", "")).startswith("fleetguard-")
    ]
    assert not bad, (
        "jobs whose name would escape the `fleetguard-` safety prefix:\n  " + "\n  ".join(bad)
    )


def test_pipeline_libraries_resolve_to_repo_sql():
    for f, key, pipeline in _resources_of("pipelines"):
        globs = [lib["glob"]["include"] for lib in pipeline.get("libraries", []) if "glob" in lib]
        assert globs, f"pipeline {key} declares no library glob"
        for g in globs:
            # `../src/pipelines/**` -> check the directory exists and holds SQL.
            base = (f.parent / g.split("**")[0]).resolve()
            assert base.is_dir(), f"pipeline {key} glob base does not exist: {g}"
            assert list(base.rglob("*.sql")), f"pipeline {key} glob matches no SQL: {g}"


def test_app_source_and_scopes():
    apps = _resources_of("apps")
    for f, key, app in apps:
        src = (f.parent / app["source_code_path"]).resolve()
        assert src.is_dir(), f"app {key} source_code_path does not exist: {app['source_code_path']}"
        assert (src / "app.yaml").is_file(), f"app {key} source has no app.yaml"

        # The console bundle is committed rather than built on deploy: the Apps runtime has
        # no Node. An empty console/ means `scripts/build_console.sh` was never run, and the
        # App would deploy cleanly and serve nothing.
        console = src / "fleetguard_api" / "console"
        assert (console / "index.html").is_file(), f"app {key} has no built console at {console}"

        # The EXACT set, not a membership check. The first version of this test asserted only
        # `"postgres" in scopes`, which a review mutation walked straight through: deleting
        # `sql` and `model-serving` left 12/12 passing. Losing `sql` breaks the UC-backed
        # routes and losing `model-serving` breaks the chat panel — both with the same
        # undifferentiated 403 that also means "stale consent grant" (I-086), which is the
        # single most expensive error message in this project.
        # `vector-search` added 2026-09-30 (I-123) — the deliberate change this assertion asks
        # for. The first live run of `/api/readyz` returned
        # `PermissionDenied: ... required scopes: vector-search` on its index check, and because
        # that check fails closed, the endpoint could never return 200 through the App on either
        # target. Proved to be the App's cap and not the caller's: the same bearer token got
        # HTTP 200 calling the vector-search API directly.
        assert set(app.get("user_api_scopes") or []) == {
            "postgres",
            "sql",
            "model-serving",
            "vector-search",
        }, (
            f"app {key} OBO scopes changed: {app.get('user_api_scopes')!r}. "
            "Change this assertion deliberately, with a reason — do not widen it to a subset."
        )


def test_app_holds_no_privileges_of_its_own():
    """The load-bearing security invariant, and the one a mutation test proved was unguarded.

    `db.py` mints its Lakebase credential from the *caller's* forwarded token and `chat.py`
    calls the serving endpoint with the caller's token, so the app's service principal needs
    no `database` or `serving-endpoint` resource. Every read and write runs as the signed-in
    human, and Postgres RLS applies to them exactly as it does locally — that is what
    `docs/ARCHITECTURE.md` §5.1 claims.

    An `app.resources:` block attaches a credential to the *app*. The moment one exists, the
    app can reach Lakebase as itself, every caller collapses into one service principal, and
    the RLS and ABAC guarantees become false — silently, because the routes keep returning
    200. A review mutation added exactly this (a `database` resource with
    `CAN_CONNECT_AND_CREATE`) and the whole suite stayed green.
    """
    for _f, key, app in _resources_of("apps"):
        assert "resources" not in app, (
            f"app {key} declares an `app.resources:` block, giving the App a credential of its "
            "own. That converts per-user OBO into a service-account model and falsifies "
            "ARCHITECTURE §5.1. If this is intentional, §5.1 has to change first."
        )


def test_stateful_resources_are_protected_from_destruction():
    """`bundle destroy` — and the quieter path of deleting a resource file and deploying —
    removes bound objects. Deleting the UC pipeline drops the streaming tables it owns
    (2.24M complaints, 5.8M TSBs); the dashboard comes back with a new id and a dead URL;
    the App is the primary demoable workflow.
    """
    for kind in ("apps", "pipelines", "dashboards"):
        found = _resources_of(kind)
        assert found, f"no {kind} declared — this test would otherwise pass vacuously"
        for _f, key, body in found:
            assert (body.get("lifecycle") or {}).get("prevent_destroy") is True, (
                f"{kind}/{key} has no `lifecycle.prevent_destroy: true`"
            )


def test_no_job_pins_a_model_version_in_base_parameters():
    """Regression guard for I-098.

    `bundle generate` copied `model_version: "3"` into the evaluation job and `"1"` into the
    deploy job, straight out of the live jobs. A `base_parameters` value *sets* the notebook
    widget, so it defeats I-094's blank-means-latest fix from one layer up — the evaluation
    scored a three-version-stale model while v6 served, and running the deploy job would have
    rolled the live agent back five versions. Both come up green.

    Pinning is legitimate for deliberately re-running an old artefact; it is not something to
    leave in version-controlled config that is re-asserted on every deploy.
    """
    for _f, key, job in _resources_of("jobs"):
        for task in job.get("tasks", []):
            params = (task.get("notebook_task") or {}).get("base_parameters") or {}
            assert "model_version" not in params, (
                f"job {key} pins model_version={params['model_version']!r} in base_parameters; "
                "let the notebook resolve the latest registered version instead"
            )


def test_app_does_not_duplicate_app_yaml_runtime_config():
    """`app.yaml` owns command/env; the bundle owns identity, scopes and permissions.

    Setting `config:` here overrides `app/backend/app.yaml` silently, creating two sources of
    truth for the auth mode, the data mode and the approver list, with no warning when they
    disagree.
    """
    for _f, key, app in _resources_of("apps"):
        assert "config" not in app, (
            f"app {key} declares a `config:` block, which overrides app/backend/app.yaml"
        )


def test_dashboard_pins_its_parent_path():
    """Moving a dashboard is a recreate, not an update — new id, new permanent URL.

    The first `bundle deploy` refused to proceed for exactly this reason. The published URL is
    part of the evidence surface and is quoted in docs/STATUS.md.
    """
    for f, key, dash in _resources_of("dashboards"):
        assert dash.get("parent_path"), (
            f"dashboard {key} has no parent_path — deploy would recreate it"
        )
        file_path = (f.parent / dash["file_path"]).resolve()
        assert file_path.is_file(), f"dashboard {key} file_path does not exist: {dash['file_path']}"


# --- the free_edition App mirror -------------------------------------------------------------
#
# `app/backend_free_edition/` is an rsync mirror of `app/backend/`, existing for one reason:
# Databricks Apps reads env/command from a literal `app.yaml` in the App resource's
# `source_code_path`, and a `config:` block on the DABs `apps` resource silently fails to write
# one (databricks/cli#4901, confirmed on CLI v1.12.1). So each target needs its own directory,
# and `scripts/sync_free_edition_app.sh` is what keeps the copy honest.
#
# WHY THIS IS TESTED RATHER THAN TRUSTED. "Re-run the script when app/backend/ changes" is the
# same contract that produced I-096 and I-098 — a hand-synced copy that drifted silently and was
# found only by comparing it to git. It drifted again the moment the two branches were folded
# together (2026-09-30): the mirror was carrying a pre-I-120 `agent_actions.py` and `readyz.py`,
# i.e. a free-edition app missing the NaN/422 fix and the readyz eval gate, with nothing
# reporting it. A comment asking for discipline is not a mechanism; this is.

APP_SRC = REPO / "app" / "backend"
APP_MIRROR = REPO / "app" / "backend_free_edition"
#: Exactly what `sync_free_edition_app.sh` copies — the tree plus two files. Scoped to the
#: script's own contract rather than "everything under the directory", because the wider
#: comparison flags `.databricks/` (gitignored CLI sync-snapshot state, which is local, per
#: -directory, and correctly not mirrored) and a test that cries wolf gets muted.
MIRROR_TREE = "fleetguard_api"
MIRROR_FILES = ("requirements.txt", "README.md")
#: `app.yaml` is excluded by the script and must stay excluded — it is the whole reason two
#: directories exist. Pinned from the other side by the test below.
MIRROR_SKIP = {"app.yaml"}


def _tree(root: Path) -> dict[str, bytes]:
    files = [
        p
        for p in sorted((root / MIRROR_TREE).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.name not in MIRROR_SKIP
    ]
    files += [root / name for name in MIRROR_FILES if (root / name).is_file()]
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


@pytest.mark.skipif(not APP_MIRROR.exists(), reason="free_edition app mirror not present")
def test_the_free_edition_app_mirror_has_not_drifted():
    src, mirror = _tree(APP_SRC), _tree(APP_MIRROR)

    missing = sorted(set(src) - set(mirror))
    extra = sorted(set(mirror) - set(src))
    changed = sorted(k for k in set(src) & set(mirror) if src[k] != mirror[k])

    assert not (missing or extra or changed), (
        "app/backend_free_edition/ has drifted from app/backend/ — run "
        "./scripts/sync_free_edition_app.sh and commit the result.\n"
        f"  missing from mirror: {missing or 'none'}\n"
        f"  only in mirror:      {extra or 'none'}\n"
        f"  contents differ:     {changed or 'none'}"
    )


@pytest.mark.skipif(not APP_MIRROR.exists(), reason="free_edition app mirror not present")
def test_each_app_target_keeps_its_own_app_yaml():
    """The one file that must NOT match. If these ever become identical, the mirror has
    overwritten free-edition's config with prod's — which points the free-edition app at
    abhi's Lakebase project and approvers, the exact breakage the two directories prevent."""
    src_yaml = APP_SRC / "app.yaml"
    mirror_yaml = APP_MIRROR / "app.yaml"
    assert src_yaml.exists() and mirror_yaml.exists(), "both targets need their own app.yaml"
    assert src_yaml.read_bytes() != mirror_yaml.read_bytes(), (
        "app/backend_free_edition/app.yaml is byte-identical to prod's — the sync script "
        "must never copy app.yaml; see its header and databricks/cli#4901"
    )
