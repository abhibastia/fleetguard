#!/usr/bin/env bash
# Run the FastAPI backend locally in static-dev auth mode, against live Lakebase.
#
# This is the exact command reconstructed from memory over and over during the
# 2026-09-04/05 session before it occurred to anyone to check it into the repo — the
# app/backend/README.md "Run locally" snippet is missing DATABRICKS_HOST (required; every
# Lakebase call raises without it), FLEETGUARD_DEV_USER (required for FLEETGUARD_APPROVERS
# to ever match anyone — principal.user_name stays None without it, so may_approve() is
# always False and every write-path test 403s), and FLEETGUARD_APPROVERS itself (needed to
# test approve/work-order-edit at all). Fixed here instead of leaving the gap for the next
# session to rediscover the hard way.
#
# **The token is captured once, here, and Databricks access tokens live one hour.** After
# that this server starts returning 500s from every Lakebase-backed endpoint, with
# `PermissionDenied: ... Invalid Token` in its log — which looks exactly like a code
# regression if you have been editing all afternoon (it was mistaken for one on 2026-09-07).
# `static-dev` holds a *static* token by design, so the fix is to restart this script, not to
# add refresh logic to StaticTokenProvider. There is no refreshing alternative for local use:
# the one mode that refreshed in place went with Render (see `deploy/render`).
#
# Usage: scripts/run_local_static_dev.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="abhi"
DEV_USER="abhisek.bastia17@gmail.com"
PORT="${1:-8811}"

echo "==> minting a live token for --profile $PROFILE"
TOKEN=$(databricks auth token --profile "$PROFILE" -o json | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "==> starting uvicorn on :$PORT (static-dev, live Lakebase, $DEV_USER is an approver)"
FLEETGUARD_AUTH_MODE=static-dev \
FLEETGUARD_DEV_TOKEN="$TOKEN" \
FLEETGUARD_DEV_USER="$DEV_USER" \
FLEETGUARD_APPROVERS="$DEV_USER" \
DATABRICKS_HOST="https://dbc-7b106152-caf3.cloud.databricks.com" \
FLEETGUARD_PG_PROJECT="projects/summer-bootcamp-2026-v2" \
.venv/bin/uvicorn fleetguard_api.main:app --app-dir app/backend --host 127.0.0.1 --port "$PORT" \
    --reload --reload-dir app/backend/fleetguard_api
