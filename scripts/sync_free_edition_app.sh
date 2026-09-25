#!/usr/bin/env bash
# Mirror app/backend/ into app/backend_free_edition/, everything except app.yaml.
#
# WHY THIS EXISTS. Databricks Apps reads env/command from exactly one file — a literal
# `app.yaml` in the App resource's `source_code_path` — and there is no other lever: a
# `config:` block on the DABs `apps` resource looks like one (accepts `command`/`env`,
# supports `${var.x}` substitution, validates and deploys clean) but silently fails to
# generate app.yaml in the workspace (github.com/databricks/cli#4901, confirmed against this
# project's CLI, v1.12.1, 2026-09-25: `bundle deploy` succeeded, the uploaded app.yaml was
# byte-for-byte prod's, none of the free_edition-target values took effect). `app/backend/
# app.yaml` is prod's live config (abhi's Lakebase project, abhi's host, abhi's approvers) —
# editing it in place to point at free-edition would mean the next `bundle deploy abhi prod`
# ships broken config to the live app.
#
# So two directories, one `source_code_path` per target: `app/backend` (prod, untouched) and
# `app/backend_free_edition` (this script's output). `fleetguard_api/` and `requirements.txt`
# are the single source of truth in `app/backend/` — this script is what keeps the mirror from
# drifting, not a second copy to hand-edit. Re-run it whenever `app/backend/` changes and a
# free-edition app deploy is coming.
#
# `app/backend_free_edition/app.yaml` is NOT touched by this script — it is its own
# committed, free-edition-specific file, hand-maintained the same way app/backend/app.yaml is.

set -euo pipefail
cd "$(dirname "$0")/.."

SRC="app/backend"
DST="app/backend_free_edition"

mkdir -p "$DST"

# fleetguard_api/ mirrored exactly (deletes files removed upstream); app.yaml and anything
# else already in $DST is left alone, since only this one subdirectory is the sync target.
rsync -a --delete "$SRC/fleetguard_api/" "$DST/fleetguard_api/"
cp "$SRC/requirements.txt" "$DST/requirements.txt"
cp "$SRC/README.md" "$DST/README.md"

echo "synced $SRC -> $DST (app.yaml untouched)"
