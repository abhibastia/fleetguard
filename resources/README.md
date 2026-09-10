# `resources/` — the bundle's resource definitions

Every file here is a Declarative Automation Bundle resource, included by `databricks.yml` via
`include: resources/*.yml`. Together with the bundle root they are **the** deployment
mechanism for this project as of 2026-09-10; see `docs/ARCHITECTURE.md` §9.1 for what they own
and the four things they deliberately do not.

## The rules that are easy to get wrong

**These resources are *bound* to objects that already exist.** `bundle deploy` updates them in
place. It does not create new ones — and if a resource loses its binding, the next deploy makes
a **duplicate** instead of erroring. Check `databricks bundle summary` before deploying:
nothing should read *to be created*.

**Editing a bound object by hand is silently undone.** `jobs reset`, `jobs update --json`,
`apps update`, the UI — all of it is re-asserted from this directory on the next deploy. Change
the YAML.

**Never run `databricks bundle generate` with its default `--source-dir`.** `generate job` and
`generate app` download the *workspace* copy of every notebook alongside the YAML, and the
default target is `src` — which would overwrite the repo with the stale content this bundle
exists to eliminate (I-096). Use the gitignored scratch dir, which must be inside the repo
because the CLI needs a path relative to the bundle root:

```bash
databricks bundle generate job --existing-job-id <id> --key <key> \
  --config-dir resources --source-dir .dab-scratch --profile abhi
# then: repoint notebook_path at ../src/..., check `git status`, rm -rf .dab-scratch
```

**Adding a resource for an object that already exists** means binding it, and binding takes a
raw ID out of a namespace shared with ~296 other students. Run `jobs get <id>` and confirm
`creator_user_name` first — several of their jobs have names that read like ours.

```bash
databricks bundle deployment bind <key> <id> --profile abhi
```

## What is here

| File | Notes |
|---|---|
| `fleetguard_console.app.yml` | The App. Owns identity, OBO scopes and the ACL; `app/backend/app.yaml` owns runtime config. Deliberately no `config:` block. |
| `bronze_silver.pipeline.yml` | The Lakeflow pipeline, libraries globbed from `../src/pipelines/**`. |
| `fleetguard_overview.dashboard.yml` | The AI/BI dashboard. `parent_path` is load-bearing — without it a deploy *recreates* the dashboard under a new URL. |
| `*.job.yml` (17) | One per job. Generated from the live jobs, then repointed at `../src/`. |

Two job files carry configuration that existed nowhere else before this bundle and must not be
"tidied": `cdf_to_gold.job.yml`'s `trigger.table_update` (both intervals sit on a hard 60 s
platform floor — smaller values are rejected outright, I-081) and `load_signals.job.yml`'s
`environments` block (for serverless jobs the dependency list lives in the job, not the
notebook, I-045).

`tests/test_bundle_resources.py` asserts the parts of all this that can be checked without
credentials. It runs in the normal `pytest` suite.
