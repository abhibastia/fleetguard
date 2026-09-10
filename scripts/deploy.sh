#!/usr/bin/env bash
# Deploy the bundle, refusing to do it from a tree that cannot be identified afterwards.
#
# WHY THIS EXISTS. `bundle deploy` records the *commit* in the deployment state
# (`state/metadata.json`) but uploads the *working tree*. Deploy with uncommitted changes and
# the two disagree: the workspace runs code that exists nowhere in git history, while the state
# file names a commit that does not contain it. There is no error, and `bundle summary` looks
# perfect.
#
# This happened twice on 2026-09-10 — the second time within hours of the first being written
# up (I-098), by the person who wrote it up. Documenting the hazard demonstrably did not prevent
# it. That is the whole argument for a script instead of a paragraph.
#
#   deploy 1: state said f2c2c43 (main's tip, containing no bundle at all)
#   deploy 2: state said bd8bd24 while the deployed files were 4c49d08
#
# WHAT THIS DOES NOT DO — deliberately.
#
# It does not deploy the App. `bundle deploy` uploads the App's source and updates the app
# resource but creates **no app deployment**, and it does not touch `default_source_code_path`,
# so a plain `apps start` will re-deploy whatever the app last used (I-097). Shipping App code
# is `databricks bundle run fleetguard_console`, and that restarts the App under whoever is
# using it — which is exactly why it stays a separate, deliberate, human step. This script
# prints the reminder rather than taking the decision.
#
# It also does not check `bundle summary` for unbound resources. Parsing that output for "to be
# created" is brittle, and the real guard for it lives in `tests/test_bundle_resources.py`.

set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${1:-abhi}"
TARGET="${2:-prod}"

echo "==> target=$TARGET profile=$PROFILE"

# ---- guard 1: the tree must be committed, or the deployed code is unidentifiable ----------
if [ -n "$(git status --porcelain)" ]; then
  echo
  echo "REFUSING TO DEPLOY — the working tree is dirty." >&2
  echo >&2
  git status --short >&2
  echo >&2
  echo "bundle deploy uploads the working tree but records HEAD. Deploying now would put code" >&2
  echo "in the workspace that exists in no commit, and label it with a commit that lacks it." >&2
  echo "Commit (or stash) first. This is I-098, which recurred once already." >&2
  exit 1
fi

# ---- guard 2: warn if HEAD is not on the remote ------------------------------------------
# Not fatal: deploying a local commit is legitimate while iterating. But the commit named in
# the deployment state would be unreachable for anyone else, so say so out loud.
BRANCH="$(git branch --show-current)"
if ! git rev-parse --quiet --verify "origin/$BRANCH" >/dev/null 2>&1; then
  echo "!!  branch '$BRANCH' has no upstream — the deployed commit will be local-only"
elif [ -n "$(git rev-list "origin/$BRANCH..HEAD" 2>/dev/null)" ]; then
  echo "!!  $(git rev-list --count "origin/$BRANCH..HEAD") commit(s) not pushed — the deployed commit is local-only"
fi

echo "==> validating"
databricks bundle validate --strict --target "$TARGET" --profile "$PROFILE" >/dev/null

echo "==> deploying $(git rev-parse --short=9 HEAD) ($BRANCH)"
databricks bundle deploy --target "$TARGET" --profile "$PROFILE"

# ---- verify the state file actually agrees with HEAD --------------------------------------
# The point of the guard is provenance, so check it rather than assume the guard was enough.
ROOT="/Workspace/Users/abhisek.bastia17@gmail.com/.bundle/fleetguard/$TARGET/state/metadata.json"
DEPLOYED="$(databricks workspace export "$ROOT" --format AUTO --profile "$PROFILE" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("config",{}).get("bundle",{}).get("git",{}).get("commit",""))' || true)"
HEAD_SHA="$(git rev-parse HEAD)"

if [ "$DEPLOYED" = "$HEAD_SHA" ]; then
  echo "==> provenance OK — deployed state names ${HEAD_SHA:0:9}"
else
  echo "!!  provenance MISMATCH: state says ${DEPLOYED:0:9}, HEAD is ${HEAD_SHA:0:9}" >&2
  exit 1
fi

echo
echo "The App was NOT deployed. bundle deploy ships no app deployment (I-097)."
echo "To ship App code:  databricks bundle run fleetguard_console -t $TARGET --profile $PROFILE"
echo "                   (starts/restarts it under whoever is using it — deliberate, manual)"
