# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 1, step 1: land the NHTSA flat files
# MAGIC
# MAGIC Downloads the four ODI flat-file artefacts into
# MAGIC `/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/`.
# MAGIC
# MAGIC **Change detection uses `If-Modified-Since` only.** `static.nhtsa.gov` also returns
# MAGIC an `ETag`, but it ignores `If-None-Match` — sending the exact advertised ETag still
# MAGIC returns `200` and the full body (measured 2026-08-31). Building on the ETag would
# MAGIC look correct and silently re-download ~2 GB on every run.

# COMMAND ----------

import datetime
import io
import os
import shutil
import urllib.request
import zipfile

from pyspark.sql import Row
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

CATALOG = "bootcamp_students"
SCHEMA = "fleetguard"
VOLUME = "nhtsa_flat_files"
VOL = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
WATERMARK = f"{CATALOG}.{SCHEMA}.ops_ingest_watermark"

BASE = "https://static.nhtsa.gov/odi/ffdd"

# name -> (url, subdirectory, expected tab-delimited field count)
#
# Each source lands in its OWN subdirectory. Auto Loader monitors a directory, not a
# file — `STREAM read_files('<dir>/FILE.txt')` fails with "Input path ... is not a
# directory" (docs/ISSUES.md I-020). Separate subdirectories also stop four different
# schemas sharing one listing.
SOURCES = {
    "FLAT_CMPL": (f"{BASE}/cmpl/FLAT_CMPL.zip", "cmpl", 51),
    "FLAT_RCL_POST_2010": (f"{BASE}/rcl/FLAT_RCL_POST_2010.zip", "rcl", 29),
    "FLAT_INV": (f"{BASE}/inv/FLAT_INV.zip", "inv", 11),
    "TSBS_RECEIVED_2025-2026": (f"{BASE}/tsbs/TSBS_RECEIVED_2025-2026.zip", "tsbs", 14),
}

UA = {"User-Agent": "FleetGuard/1.0 (capstone; contact abhisek.bastia17@gmail.com)"}

# COMMAND ----------

spark.sql(f"""
  CREATE TABLE IF NOT EXISTS {WATERMARK} (
    source          STRING  COMMENT 'logical source name',
    url             STRING,
    last_modified   STRING  COMMENT 'Last-Modified header value, verbatim, for If-Modified-Since',
    etag            STRING  COMMENT 'recorded for reference only — the host ignores If-None-Match',
    bytes_written   BIGINT,
    landed_file     STRING,
    checked_at      TIMESTAMP,
    outcome         STRING  COMMENT 'downloaded | not_modified | failed'
  )
  USING DELTA
  COMMENT 'Ingest watermarks for the NHTSA flat files. Drives If-Modified-Since change detection.'
""")


def current_watermark(source: str):
    rows = spark.sql(
        f"SELECT last_modified FROM {WATERMARK} "
        f"WHERE source = '{source}' AND outcome = 'downloaded' "
        f"ORDER BY checked_at DESC LIMIT 1"
    ).collect()
    return rows[0]["last_modified"] if rows else None


# Explicit schema, not inference: on the 304 path `etag` and `landed_file` are both
# None, and Spark cannot infer a type for an all-null column (CANNOT_DETERMINE_TYPE).
WATERMARK_SCHEMA = StructType(
    [
        StructField("source", StringType(), True),
        StructField("url", StringType(), True),
        StructField("last_modified", StringType(), True),
        StructField("etag", StringType(), True),
        StructField("bytes_written", LongType(), True),
        StructField("landed_file", StringType(), True),
        StructField("checked_at", TimestampType(), True),
        StructField("outcome", StringType(), True),
    ]
)


def record(source, url, last_modified, etag, bytes_written, landed_file, outcome):
    row = Row(
        source=source,
        url=url,
        last_modified=last_modified,
        etag=etag,
        bytes_written=int(bytes_written),
        landed_file=landed_file,
        checked_at=datetime.datetime.now(),
        outcome=outcome,
    )
    (
        spark.createDataFrame([row], schema=WATERMARK_SCHEMA)
        .write.mode("append")
        .saveAsTable(WATERMARK)
    )


# COMMAND ----------


def migrate_legacy_layout():
    """Move any root-level .txt into its per-source subdirectory.

    Idempotent and server-side, so switching layouts costs a rename rather than a
    2.6 GB re-download (the watermark would otherwise 304 and never re-place the file).
    """
    for src, (_, subdir, _f) in SOURCES.items():
        os.makedirs(f"{VOL}/{subdir}", exist_ok=True)
    for entry in os.listdir(VOL):
        if not entry.endswith(".txt"):
            continue
        for src, (_, subdir, _f) in SOURCES.items():
            stem = src if not src.startswith("TSBS") else "TSBS_RECEIVED"
            if entry.startswith(stem):
                dest = f"{VOL}/{subdir}/{entry}"
                print(f"  migrating {entry} → {subdir}/")
                shutil.move(f"{VOL}/{entry}", dest)
                break


migrate_legacy_layout()

# COMMAND ----------


def fetch(source: str, url: str, subdir: str, expected_fields):
    """Download `url` into the volume unless the host says it hasn't changed."""
    prior = current_watermark(source)

    req = urllib.request.Request(url, headers=dict(UA))
    if prior:
        # The one conditional header this host actually honours.
        req.add_header("If-Modified-Since", prior)

    try:
        resp = urllib.request.urlopen(req, timeout=900)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            print(f"  {source}: 304 Not Modified — skipped (watermark {prior})")
            record(
                source=source,
                url=url,
                last_modified=prior,
                etag=None,
                bytes_written=0,
                landed_file=None,
                outcome="not_modified",
            )
            return
        print(f"  {source}: FAILED HTTP {e.code}")
        record(
            source=source,
            url=url,
            last_modified=None,
            etag=None,
            bytes_written=0,
            landed_file=None,
            outcome="failed",
        )
        raise

    last_mod = resp.headers.get("Last-Modified")
    etag = resp.headers.get("ETag")
    payload = resp.read()
    print(f"  {source}: 200, {len(payload):,} bytes zipped · Last-Modified {last_mod}")

    written, name = 0, None
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".txt"):
                continue
            name = os.path.basename(member)
            dest = f"{VOL}/{subdir}/{name}"
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
            written = os.path.getsize(dest)
            print(f"      → {dest}  ({written:,} bytes)")

            if expected_fields:
                with open(dest, encoding="latin-1", errors="replace") as fh:
                    head = fh.readline().rstrip("\n").rstrip("\r").split("\t")
                got = len(head)
                flag = "ok" if got == expected_fields else "MISMATCH"
                print(f"        field count: {got} (expected {expected_fields}) [{flag}]")

    record(
        source=source,
        url=url,
        last_modified=last_mod,
        etag=etag,
        bytes_written=written,
        landed_file=name,
        outcome="downloaded",
    )


for src, (u, sub, fields) in SOURCES.items():
    print(f"{src}:")
    fetch(src, u, sub, fields)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Landed files

# COMMAND ----------

for _s, (_u, _sub, _f) in SOURCES.items():
    print(f"--- {_sub}/ ---")
    display(spark.sql(f"LIST '{VOL}/{_sub}'"))

# COMMAND ----------

display(
    spark.sql(f"""
      SELECT source, outcome, last_modified, bytes_written, landed_file, checked_at
      FROM {WATERMARK} ORDER BY checked_at DESC, source
    """)
)
