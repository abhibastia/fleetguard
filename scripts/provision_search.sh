#!/usr/bin/env bash
# Bring the AI Search endpoint and index up, idempotently, with drop detection.
#
# WHY THIS EXISTS. The commands live in `docs/RUNBOOK.md` and they are correct — Run 1
# executed them start to finish on 2026-09-23. But a documented sequence is not the same as a
# runnable one. It is not idempotent (a re-run after a dropped SSH session errors on
# `create-endpoint`), it does not poll, and the poll it describes has a failure signal a human
# has to remember to watch for. Run 2 executes this under deadline pressure. That is the wrong
# moment to be reading prose and retyping a 20-line JSON body.
#
# WHY IT IS A SCRIPT AND NOT BUNDLE YAML. Not a shortcut — DABs has no vector-search resource
# type at all. `bundle summary` exposes exactly four resource keys: apps, dashboards, jobs,
# pipelines. Declarative management of this endpoint is impossible, not merely unbuilt, and a
# script is the ceiling. Same for the agent serving endpoint, Lakebase CDF and both metric
# views — five documented exceptions to §8.5, this being one of them.
#
# COST, STATED PLAINLY. `--create` bills. The endpoint is ~$6.72/day while it exists and
# **billing continues for 24 h after the last index is deleted**, so this is not a resource to
# leave running "just in case". `--check` (the default) only reads control-plane state and is
# free — which is why it is the default. Nothing here is destructive; teardown stays a
# deliberate, separate, human command (`docs/RUNBOOK.md` step 2.1).
#
# THE POLL WATCHES FOR A DROP, NOT A PLATEAU. I-105: a sync that failed internally restarted
# from zero, and the only visible symptom was `indexed_row_count` going *backwards*. A plateau
# is normal — the count moves in bursts. A decrease is the failure, and the index API gives no
# other signal, so this script treats one as fatal and prints the pipeline-events command
# rather than guessing at a cause.

set -euo pipefail
cd "$(dirname "$0")/.."

ENDPOINT="fleetguard-vs"
CATALOG_SCHEMA="bootcamp_students.fleetguard"
INDEX="${CATALOG_SCHEMA}.complaint_chunk_idx"
SOURCE_TABLE="${CATALOG_SCHEMA}.silver_complaint_chunk_indexed"
EMBEDDING_MODEL="databricks-gte-large-en"
POLL_SECONDS="${POLL_SECONDS:-300}"

MODE="check"
PROFILE=""

usage() {
  cat <<USAGE
usage: $0 [--check | --create] --profile <name>

  --check    (default) read endpoint + index state. Free, no writes.
  --create   create the endpoint and index if absent, then poll until ready. BILLS.

Teardown is deliberately NOT here. See docs/RUNBOOK.md step 2.1.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)   MODE="check"; shift ;;
    --create)  MODE="create"; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# No default profile, on purpose. `abhi` is a SHARED bootcamp metastore holding ~296 other
# students' work; every other script in this repo demands the profile explicitly for the same
# reason, and a default here would be the one place that quietly guessed.
if [[ -z "$PROFILE" ]]; then
  echo "error: --profile is required (never auto-selected — this is a shared metastore)" >&2
  exit 2
fi

db() { databricks "$@" --profile "$PROFILE"; }

# `create-index` with `--json` REJECTS positional arguments ("when --json flag is specified, no
# positional arguments are allowed") — found live in Run 1. So name/endpoint_name/primary_key/
# index_type all go inside the body. Kept as a function so the --check path can print the exact
# body that --create would send, and so `tests/test_provision_search.py` can parse one source
# of truth rather than a copy.
index_body() {
  cat <<JSON
{
  "name": "${INDEX}",
  "endpoint_name": "${ENDPOINT}",
  "primary_key": "chunk_id",
  "index_type": "DELTA_SYNC",
  "delta_sync_index_spec": {
    "source_table": "${SOURCE_TABLE}",
    "pipeline_type": "TRIGGERED",
    "embedding_source_columns": [
      {"name": "chunk_text", "embedding_model_endpoint_name": "${EMBEDDING_MODEL}"}
    ],
    "columns_to_sync": ["chunk_id", "complaint_id", "make", "model", "component", "any_harm", "chunk_text"]
  }
}
JSON
}

index_row_count() {
  db vector-search-indexes get-index "$INDEX" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get("status") or {}; print(s.get("indexed_row_count", 0), str(s.get("ready", False)).lower())' \
    2>/dev/null || echo "MISSING false"
}

echo "profile:  $PROFILE"
echo "endpoint: $ENDPOINT"
echo "index:    $INDEX"
echo "source:   $SOURCE_TABLE"
echo

if [[ "$MODE" == "check" ]]; then
  echo "--- endpoint ---"
  db vector-search-endpoints get-endpoint "$ENDPOINT" 2>/dev/null || echo "absent"
  echo
  echo "--- index ---"
  read -r rows ready <<<"$(index_row_count)"
  if [[ "$rows" == "MISSING" ]]; then
    echo "absent"
  else
    printf 'indexed_row_count=%s ready=%s\n' "$rows" "$ready"
  fi
  echo
  echo "read-only. re-run with --create to provision (this bills ~\$6.72/day)."
  exit 0
fi

# ---------------------------------------------------------------- create (bills) ----------

# The expected count is READ, never hard-coded. It was 1,746,601, then 115,499 after I-111's
# rescope, and I-115 widened the fleet match again — so whatever is written here today is
# wrong by Run 2 almost by construction. Ask the table.
echo "measuring ${SOURCE_TABLE}..."
EXPECTED="$(db experimental aitools tools query "SELECT COUNT(*) AS n FROM ${SOURCE_TABLE}" \
  | python3 -c 'import json,sys,re
raw = sys.stdin.read()
m = re.findall(r"\d[\d,]*", raw)
print(max((int(x.replace(",","")) for x in m), default=0))')"
if [[ "$EXPECTED" -le 0 ]]; then
  echo "error: could not read a row count from ${SOURCE_TABLE}. Build it first:" >&2
  echo "  databricks bundle run build_chunk_index_source -t prod --profile $PROFILE" >&2
  exit 1
fi
printf 'source rows: %s  (~%s min at the measured 4,336 rows/min)\n' \
  "$EXPECTED" "$(( (EXPECTED + 4335) / 4336 ))"

cat <<WARN

THIS BILLS. Endpoint ~\$6.72/day, and billing continues 24 h AFTER the last index is deleted.
Tear down with docs/RUNBOOK.md step 2.1 as soon as the evidence is captured.

WARN
read -r -p "continue? [y/N] " reply
[[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "aborted."; exit 1; }

if db vector-search-endpoints get-endpoint "$ENDPOINT" >/dev/null 2>&1; then
  echo "endpoint $ENDPOINT already exists — reusing"
else
  echo "creating endpoint $ENDPOINT..."
  db vector-search-endpoints create-endpoint "$ENDPOINT" STANDARD
fi

read -r rows _ready <<<"$(index_row_count)"
if [[ "$rows" == "MISSING" ]]; then
  echo "creating index $INDEX..."
  # Via a temp file rather than a pipe: `--json @-` is not a documented form of this flag, and
  # a create-index call that fails on argument parsing after the endpoint is already billing is
  # the worst possible place to discover that.
  body_file="$(mktemp)"
  trap 'rm -f "$body_file"' EXIT
  index_body >"$body_file"
  db vector-search-indexes create-index --index-subtype HYBRID --json "@${body_file}"
else
  echo "index $INDEX already exists at $rows rows — resuming the poll"
fi

echo
echo "polling every ${POLL_SECONDS}s. A DROP in indexed_row_count is the failure (I-105);"
echo "a plateau is not."
last=-1
while true; do
  sleep "$POLL_SECONDS"
  read -r rows ready <<<"$(index_row_count)"
  if [[ "$rows" == "MISSING" ]]; then
    echo "error: index disappeared mid-build" >&2
    exit 1
  fi
  printf '%s  indexed_row_count=%s / %s  ready=%s\n' "$(date -u +%H:%M:%SZ)" "$rows" "$EXPECTED" "$ready"
  if (( last >= 0 && rows < last )); then
    cat <<DROP >&2

FAILED: indexed_row_count went BACKWARDS ($last -> $rows).
This is I-105 — the platform restarted the sync from zero after an internal failure. The index
API reports nothing else about it. Look at the pipeline, not at get-index:

  databricks pipelines list-pipeline-events <PIPELINE_ID> --profile $PROFILE

(the pipeline id is in the get-index response's delta_sync_index_spec.)
DROP
    exit 1
  fi
  last="$rows"
  if [[ "$ready" == "true" ]]; then
    echo
    if [[ "$rows" != "$EXPECTED" ]]; then
      echo "WARNING: ready, but $rows indexed rows for $EXPECTED source rows." >&2
      echo "Do not record this as a clean build until the gap is explained." >&2
      exit 1
    fi
    echo "READY — $rows rows, matching ${SOURCE_TABLE} exactly."
    echo "Record this count in docs/STATUS.md; four documents are waiting on it."
    exit 0
  fi
done
