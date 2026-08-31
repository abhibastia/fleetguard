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
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from pyspark.sql import Row
from pyspark.sql.types import (
    BooleanType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

API = "https://api.nhtsa.gov/recalls/recallsByVehicle"
UA = {"User-Agent": "FleetGuard/1.0 (capstone; contact abhisek.bastia17@gmail.com)"}

# Pacing. 2 requests/second is polite for a public government API and keeps a full
# roster sweep inside ~90 seconds.
REQ_PER_SEC = 2.0
# 0 = sweep every fleet combo. Set the `max_combos` job parameter to cap it for a
# quick demo run without touching the code.
try:
    MAX_COMBOS = int(dbutils.widgets.get("max_combos"))
except Exception:
    MAX_COMBOS = 0

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
print(f"fleet coverage: {coverage['pollable_vehicles']:,} of {coverage['fleet_total']:,} vehicles "
      f"({coverage['pollable_vehicles']/coverage['fleet_total']:.1%}) have a known recall-vocabulary name")

# COMMAND ----------


def poll(make, model, year):
    """Return (records, status). Never raises — a dead combo must not kill the sweep."""
    qs = urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
    req = urllib.request.Request(f"{API}?{qs}", headers=dict(UA))
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        return [], f"http_{e.code}"
    except Exception as e:  # noqa: BLE001 - network flakiness must not abort the sweep
        return [], f"error_{type(e).__name__}"

    now = datetime.datetime.now()
    out = []
    for x in body.get("results") or []:
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
    return out, "ok"


records, state_rows = [], []
t0 = time.time()
interval = 1.0 / REQ_PER_SEC

for i, c in enumerate(combos):
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
