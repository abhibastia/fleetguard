#!/usr/bin/env bash
# Build the React console and place it where FastAPI serves it.
#
# One service serves both API and console, so there is no CORS and no second service to
# deploy. Nothing runs this for you: the Databricks Apps runtime has no Node, so the output
# is committed. Re-run it whenever anything under app/frontend/ changes, or the App serves
# stale UI while every test still passes.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> building console"
( cd app/frontend && npm ci --silent && npm run build )

echo "==> installing into the API package"
rm -rf app/backend/fleetguard_api/console
cp -r app/frontend/dist app/backend/fleetguard_api/console

echo "==> done: $(find app/backend/fleetguard_api/console -type f | wc -l | tr -d ' ') files"
