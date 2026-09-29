# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — live recall campaign polling
# MAGIC
# MAGIC The reactive half's real-time trigger. Flat files refresh daily; this endpoint
# MAGIC surfaces a campaign as soon as NHTSA publishes it, and carries the `parkIt` /
# MAGIC `parkOutSide` booleans the Park It path depends on.
# MAGIC
# MAGIC **The endpoint is make/model/year scoped — there is no "recent recalls" call.**
# MAGIC `recallsByVehicle` requires all three parameters. Omitting `model` or `modelYear`
# MAGIC returns `Count: 0` with `"Results returned successfully"` rather than an error, so a
# MAGIC partially-built query fails silently and looks like "no recalls". Polling therefore
# MAGIC means sweeping the fleet's distinct (make, model, model_year) combinations.
# MAGIC
# MAGIC **This makes §4.1's literal "every 60 seconds" unachievable**, and §8.3's ~85-second
# MAGIC worst case with it. The roster has ~163 combos; polling every one of them once a
# MAGIC minute would be ~235k requests/day against a public API that §3 explicitly commits
# MAGIC to not using in bulk. What is achievable is a **paced full sweep** — see the latency
# MAGIC table printed at the end, which is measured rather than asserted.
# MAGIC
# MAGIC Only combos **present in the fleet** are polled. That is both the efficient choice
# MAGIC and the correct one: a campaign against a vehicle we do not own is not exposure.

# COMMAND ----------

import datetime
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from pyspark.sql import Row
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# RETRY/VALIDATION POLICY LIVES IN `src/fleetguard/http_retry.py`, NOT HERE.
#
# It is pure, dependency-injected logic with 60 unit tests that run in CI with no network
# and no clock — a 429 carrying `Retry-After`, a truncated body and a changed response
# shape are all ordinary test inputs there, and none of them are reproducible on demand
# against a live government API. Inlining the policy would put the one part of this
# notebook that most needs testing in the one place this project cannot test.
#
# The bundle uploads the whole `src/` tree, so the package sits one directory up from this
# notebook. `os.getcwd()` is the notebook's own directory under Files-in-Workspace, which is
# what makes the relative hop work both in the bundle and when running the file locally.
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), "..")))
from fleetguard.http_retry import (  # noqa: E402
    failure_pct,
    fetch_json,
    summarise_statuses,
    validate_recalls_payload,
)

API = "https://api.nhtsa.gov/recalls/recallsByVehicle"
UA = {"User-Agent": "FleetGuard/1.0 (capstone; contact abhisek.bastia17@gmail.com)"}

# Pacing. 2 requests/second is polite for a public government API and keeps a full
# roster sweep inside ~90 seconds.
REQ_PER_SEC = 2.0

# WIDGETS ARE DECLARED, NOT JUST READ. Until 2026-09-20 this notebook called
# `dbutils.widgets.get("max_combos")` against a widget nothing ever created, inside a bare
# `except` — so `MAX_COMBOS` was *always* 0 and the job YAML had no parameter to set it
# with. Dead configuration surface that read as a live knob.
try:
    dbutils.widgets.text("catalog", "bootcamp_students")
    dbutils.widgets.text("schema", "fleetguard")
    dbutils.widgets.text("max_combos", "0", "Max combos (0 = all)")
    dbutils.widgets.text("failure_gate_pct", "10", "Abort alert rebuild above this % failed")
except Exception:  # noqa: BLE001 - running outside Databricks (import check, local lint)
    pass


def _widget(name: str, default: str) -> str:
    try:
        return dbutils.widgets.get(name) or default
    except Exception:  # noqa: BLE001
        return default


CATALOG = _widget("catalog", "bootcamp_students")
SCHEMA = _widget("schema", "fleetguard")
spark.sql(f"USE {CATALOG}.{SCHEMA}")

MAX_COMBOS = int(_widget("max_combos", "0"))

# Above this share of failed combos the sweep refuses to rebuild `gold_recall_alert`.
# See the gate cell near the bottom for why a *destructive* rebuild needs a floor under it.
FAILURE_GATE_PCT = float(_widget("failure_gate_pct", "10"))

RUN_ID = uuid.uuid4().hex[:12]

# COMMAND ----------

API_SCHEMA = StructType(
    [
        StructField("campaign_number", StringType()),
        StructField("make", StringType()),
        StructField("model", StringType()),
        StructField("model_year", IntegerType()),
        StructField("component", StringType()),
        StructField("manufacturer", StringType()),
        StructField("summary", StringType()),
        StructField("consequence", StringType()),
        StructField("remedy", StringType()),
        StructField("notes", StringType()),
        StructField("report_received_date", StringType()),
        StructField("park_it", BooleanType()),
        StructField("park_outside", BooleanType()),
        StructField("over_the_air_update", BooleanType()),
        StructField("fetched_at", TimestampType()),
    ]
)

spark.sql("""
CREATE TABLE IF NOT EXISTS ops_recall_poll_state (
  make STRING, model STRING, model_year INT,
  last_polled_at TIMESTAMP,
  last_campaign_count INT,
  last_status STRING
) USING DELTA
COMMENT 'Round-robin cursor for recall API polling. Least-recently-polled combos go first.'
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Poll set — fleet combos, least recently polled first

# COMMAND ----------

# POLL USING THE RECALL VOCABULARY, NOT vPIC's.
#
# The API rejects vPIC model names it does not recognise — and it signals this with
# HTTP 400 while the body still reads "Results returned successfully", so a naive client
# records a client error and moves on. Measured: CHEVROLET/SILVERADO/2019 -> 400, but
# CHEVROLET/SILVERADO 1500/2019 -> 200 with 10 campaigns. FORD/F-250 -> 400,
# FORD/F-250 SD -> 200. Polling vPIC names lost 65 of 163 combos (40%).
#
# gold_fleet_exposure already carries `recall_model` — the NHTSA-vocabulary name proven
# to join against the flat file — so it is the correct poll key. This is the same
# vocabulary mismatch as ISSUES I-030, surfacing for the third time.
combos = spark.sql("""
SELECT e.make, e.recall_model AS model, e.model_year,
       COUNT(DISTINCT e.vin) AS fleet_vehicles,
       MAX(s.last_polled_at) AS last_polled_at
FROM gold_fleet_exposure e
LEFT JOIN ops_recall_poll_state s
  ON s.make = e.make AND s.model = e.recall_model AND s.model_year = e.model_year
GROUP BY e.make, e.recall_model, e.model_year
ORDER BY last_polled_at ASC NULLS FIRST, fleet_vehicles DESC
""").collect()

if MAX_COMBOS:
    combos = combos[:MAX_COMBOS]

coverage = spark.sql("""
SELECT
  (SELECT COUNT(*) FROM gold_fleet_vehicle) AS fleet_total,
  (SELECT COUNT(DISTINCT vin) FROM gold_fleet_exposure) AS pollable_vehicles
""").collect()[0]
print(f"combos to poll: {len(combos)}")
print(
    f"fleet coverage: {coverage['pollable_vehicles']:,} of {coverage['fleet_total']:,} vehicles "
    f"({coverage['pollable_vehicles'] / coverage['fleet_total']:.1%}) have a known recall-vocabulary name"
)

# COMMAND ----------


def _open(url: str):
    """The one place this notebook actually touches the network.

    Injected into `fetch_json` so the retry loop itself stays testable off platform.
    """
    return urllib.request.urlopen(urllib.request.Request(url, headers=dict(UA)), timeout=45)


def poll(make, model, year):
    """Return (records, status). Never raises — a dead combo must not kill the sweep.

    Status vocabulary, deliberately kept legible because `ops_recall_poll_state.last_status`
    is read by a dashboard: `ok` · `retried_ok` (transient fault, recovered) · `malformed`
    (HTTP 200 whose body is not the shape we expect) · `http_<code>` · `error_<Type>`.

    `malformed` is the new one and it is the point. Previously a 200 carrying a completely
    different document produced zero records and the status `ok` — indistinguishable from
    "this combo genuinely has no open recalls". NHTSA already ships one endpoint whose
    status and body disagree (I-031), so "the body is the shape we think it is" was never a
    safe assumption to leave unchecked.
    """
    qs = urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
    outcome = fetch_json(f"{API}?{qs}", opener=_open, sleeper=time.sleep)
    if not outcome.ok:
        return [], outcome.status

    results, reason = validate_recalls_payload(outcome.body)
    if reason is not None:
        print(f"  malformed response for {make}/{model}/{year}: {reason}")
        return [], "malformed"

    now = datetime.datetime.now()
    out = []
    for x in results:
        out.append(
            Row(
                campaign_number=(x.get("NHTSACampaignNumber") or "").strip().upper() or None,
                make=(x.get("Make") or "").strip().upper() or None,
                model=(x.get("Model") or "").strip().upper() or None,
                model_year=int(x["ModelYear"]) if str(x.get("ModelYear", "")).isdigit() else None,
                component=(x.get("Component") or "").strip().upper() or None,
                manufacturer=(x.get("Manufacturer") or "").strip() or None,
                summary=x.get("Summary"),
                consequence=x.get("Consequence"),
                remedy=x.get("Remedy"),
                notes=x.get("Notes"),
                # API returns DD/MM/YYYY — kept verbatim here and parsed downstream so a
                # format change surfaces as a parse failure rather than a silent wrong date.
                report_received_date=x.get("ReportReceivedDate"),
                park_it=bool(x.get("parkIt")),
                park_outside=bool(x.get("parkOutSide")),
                over_the_air_update=bool(x.get("overTheAirUpdate")),
                fetched_at=now,
            )
        )
    return out, outcome.status


records, state_rows = [], []
t0 = time.time()
interval = 1.0 / REQ_PER_SEC

for c in combos:
    tick = time.time()
    recs, status = poll(c["make"], c["model"], c["model_year"])
    records.extend(recs)
    state_rows.append(
        Row(
            make=c["make"],
            model=c["model"],
            model_year=c["model_year"],
            last_polled_at=datetime.datetime.now(),
            last_campaign_count=len(recs),
            last_status=status,
        )
    )
    elapsed = time.time() - tick
    if elapsed < interval:
        time.sleep(interval - elapsed)

sweep_seconds = time.time() - t0
print(
    f"swept {len(combos)} combos in {sweep_seconds:.1f}s "
    f"({len(combos) / max(sweep_seconds, 1):.1f} req/s)"
)
print(f"campaign records returned: {len(records):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run-level accounting
# MAGIC
# MAGIC `ops_recall_poll_state` records the outcome of each *combo*. Nothing recorded the
# MAGIC outcome of the **run**, so there was no number to threshold on and no history of how
# MAGIC reliable this integration actually is. `ops_recall_api_sweep` is one row per sweep,
# MAGIC and it is what `gold_api_poll_health` aggregates for the dashboard.

# COMMAND ----------

summary = summarise_statuses(r["last_status"] for r in state_rows)
pct_failed = failure_pct(summary, len(combos))

print(f"run {RUN_ID}: " + " ".join(f"{k}={v}" for k, v in summary.items()))
print(f"failure rate: {pct_failed:.1f}% (gate at {FAILURE_GATE_PCT:.1f}%)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The gate
# MAGIC
# MAGIC **`gold_recall_alert` is rebuilt with `CREATE OR REPLACE`, which is destructive.** A
# MAGIC sweep in which most combos failed would previously rebuild the alert table from
# MAGIC whatever partial view of the world it managed to collect, and report SUCCESS — into a
# MAGIC job whose notification blocks are empty. The alerts would silently narrow and nobody
# MAGIC would be told.
# MAGIC
# MAGIC So the destructive step now has a floor under it. Note what is deliberately **not**
# MAGIC gated: the `bronze_recall_api` append and the `ops_recall_poll_state` MERGE both still
# MAGIC happen on a bad sweep. They are append/upsert — skipping them would discard the
# MAGIC evidence of the failure, which is the opposite of what you want. It is only the
# MAGIC `CREATE OR REPLACE` that must be withheld.

# COMMAND ----------

gate_tripped = pct_failed > FAILURE_GATE_PCT
if gate_tripped:
    print(
        f"GATE TRIPPED — {pct_failed:.1f}% of {len(combos)} combos failed, above the "
        f"{FAILURE_GATE_PCT:.1f}% threshold. gold_recall_alert will NOT be rebuilt; the "
        "existing table is left intact rather than replaced from a partial sweep."
    )

# COMMAND ----------

if records:
    (
        spark.createDataFrame(records, schema=API_SCHEMA)
        .write.mode("append")
        .saveAsTable("bronze_recall_api")
    )
    spark.sql("""
    COMMENT ON TABLE bronze_recall_api IS
    'Raw recallsByVehicle responses, append-only. One row per campaign-vehicle returned per poll. Carries parkIt/parkOutSide, which the daily flat file only gained in May 2025.'
    """)

STATE_SCHEMA = StructType(
    [
        StructField("make", StringType()),
        StructField("model", StringType()),
        StructField("model_year", IntegerType()),
        StructField("last_polled_at", TimestampType()),
        StructField("last_campaign_count", IntegerType()),
        StructField("last_status", StringType()),
    ]
)
state_df = spark.createDataFrame(state_rows, schema=STATE_SCHEMA)
state_df.createOrReplaceTempView("_new_state")
spark.sql("""
MERGE INTO ops_recall_poll_state t
USING _new_state s
  ON t.make = s.make AND t.model = s.model AND t.model_year = s.model_year
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")
print("poll state updated")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Alerts — campaigns the API knows about that the flat file does not
# MAGIC
# MAGIC This is the reactive product surface: a campaign visible here but absent from
# MAGIC `silver_recall` posted since the last daily flat-file load. Joined to the fleet so
# MAGIC an alert carries actual exposure rather than just a campaign number.

# COMMAND ----------

if gate_tripped:
    print("skipping gold_recall_alert rebuild — see the gate cell above")
else:
    spark.sql("""
    CREATE OR REPLACE TABLE gold_recall_alert
    COMMENT 'Campaigns seen from the live API but not yet in the daily flat file, with fleet exposure attached. The reactive half of the product.'
    AS
    WITH latest_api AS (
      SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (
          PARTITION BY campaign_number, make, model, model_year ORDER BY fetched_at DESC) AS rn
        FROM bronze_recall_api
      ) WHERE rn = 1
    ),
    -- NOVELTY IS BY CAMPAIGN NUMBER ALONE.
    -- NHTSACampaignNumber is a globally unique NHTSA identifier. An earlier version also
    -- joined on make/model/model_year and reported 9 "new" campaigns that were all already
    -- in the flat file — the model strings simply differed (API 'F-250' vs flat file
    -- 'F-250 SD'). That would have shown a demo audience new campaigns that were not new.
    novel AS (
      SELECT a.*
      FROM latest_api a
      LEFT ANTI JOIN silver_recall r
        ON r.campaign_number = a.campaign_number
    )
    SELECT
      n.campaign_number, n.make, n.model, n.model_year, n.component,
      n.park_it, n.park_outside, n.report_received_date, n.consequence, n.summary,
      n.fetched_at,
      COUNT(v.vin)                        AS vehicles_exposed,
      COUNT(DISTINCT v.depot_id)          AS depots_affected
    FROM novel n
    LEFT JOIN gold_fleet_vehicle v
      ON v.make = n.make AND v.model = n.model AND v.model_year = n.model_year
    GROUP BY n.campaign_number, n.make, n.model, n.model_year, n.component,
             n.park_it, n.park_outside, n.report_received_date, n.consequence,
             n.summary, n.fetched_at
    """)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Record the run, then fail if the gate tripped
# MAGIC
# MAGIC The ops row is written **before** the raise, on purpose: a failed sweep is exactly the
# MAGIC run whose accounting you want to keep. Raising first would lose the evidence of the
# MAGIC thing that went wrong.

# COMMAND ----------

SWEEP_SCHEMA = StructType(
    [
        StructField("run_id", StringType()),
        StructField("started_at", TimestampType()),
        StructField("sweep_seconds", DoubleType()),
        StructField("combos", IntegerType()),
        StructField("ok", IntegerType()),
        StructField("retried", IntegerType()),
        StructField("http_4xx", IntegerType()),
        StructField("http_5xx", IntegerType()),
        StructField("malformed", IntegerType()),
        StructField("errors", IntegerType()),
        StructField("failure_pct", DoubleType()),
        StructField("campaign_rows", IntegerType()),
        StructField("alert_table_refreshed", BooleanType()),
    ]
)

spark.createDataFrame(
    [
        Row(
            run_id=RUN_ID,
            started_at=datetime.datetime.fromtimestamp(t0),
            sweep_seconds=float(sweep_seconds),
            combos=len(combos),
            ok=summary["ok"],
            retried=summary["retried"],
            http_4xx=summary["http_4xx"],
            http_5xx=summary["http_5xx"],
            malformed=summary["malformed"],
            errors=summary["errors"],
            failure_pct=float(pct_failed),
            campaign_rows=len(records),
            alert_table_refreshed=not gate_tripped,
        )
    ],
    schema=SWEEP_SCHEMA,
).write.mode("append").saveAsTable("ops_recall_api_sweep")

spark.sql("""
COMMENT ON TABLE ops_recall_api_sweep IS
'One row per recall-API sweep: combos attempted, outcome counts, failure rate, and whether the failure gate allowed gold_recall_alert to be rebuilt. Source for gold_api_poll_health.'
""")
print(f"recorded sweep {RUN_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Measured sweep latency
# MAGIC
# MAGIC Worst-case publication-to-visible is (sweep duration + gap between runs), because a
# MAGIC campaign published just after its combo was polled waits a full cycle. Reported here
# MAGIC so §8.3 can state a measured number instead of an assumed one.

# COMMAND ----------

print(f"combos swept        : {len(combos)}")
print(f"sweep duration      : {sweep_seconds:.1f}s at {REQ_PER_SEC} req/s")
print(f"requests per sweep  : {len(combos)}")
print()
print("worst-case detection latency by schedule:")
for mins in (5, 10, 15, 30, 60):
    per_day = len(combos) * (24 * 60 / mins)
    print(
        f"  every {mins:>2} min -> worst case ~{sweep_seconds / 60 + mins:4.1f} min"
        f"   ({per_day:,.0f} requests/day)"
    )

# COMMAND ----------

display(
    spark.sql("""
SELECT last_status, COUNT(*) AS combos, SUM(last_campaign_count) AS campaign_rows
FROM ops_recall_poll_state GROUP BY 1 ORDER BY combos DESC
""")
)

display(
    spark.sql("""
SELECT campaign_number, make, model, model_year, park_it, vehicles_exposed, depots_affected
FROM gold_recall_alert ORDER BY vehicles_exposed DESC LIMIT 15
""")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fail the run last, after every diagnostic above has printed
# MAGIC
# MAGIC Deliberately the final cell. Raising earlier would suppress the status histogram and
# MAGIC the sweep-latency table — precisely the output someone needs to diagnose *why* the
# MAGIC gate tripped. The ops row is already committed by this point, so the evidence
# MAGIC survives the failure.

# COMMAND ----------

if gate_tripped:
    raise RuntimeError(
        f"recall API sweep {RUN_ID} failed its quality gate: {pct_failed:.1f}% of "
        f"{len(combos)} combos did not return a usable payload (threshold "
        f"{FAILURE_GATE_PCT:.1f}%). gold_recall_alert was left intact. "
        f"Counts: {summary}. Coverage has been 200/200 ok since I-031 was fixed, so a "
        "sustained 4xx rate most likely means the fleet's model vocabulary has drifted "
        "from NHTSA's again (I-030)."
    )
