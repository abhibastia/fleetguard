-- FleetGuard bronze: ODI defect investigations (FLAT_INV.txt)
--
-- This is the backtest ground truth. Rows are make/model/year granular, so the row
-- count (154,367) is NOT the investigation count — there are 5,344 distinct
-- NHTSA_ACTION_NUMBER values, 777 opened 2010+. See docs/ISSUES.md I-010.
--
-- CAMPNO, where present, links to bronze_recalls.CAMPNO — NHTSA's documented join
-- between an investigation and the recall campaign it produced.

CREATE OR REFRESH STREAMING TABLE bronze_investigations
CLUSTER BY (_ingest_date)
COMMENT 'Raw ODI defect investigations since 1972. ODATE is the investigation open date — the backtest reference point. Source: FLAT_INV.txt.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'true',
  'quality' = 'bronze'
)
AS
SELECT
  -- Cluster key FIRST. Delta collects file stats on the first 32 columns only, and
  -- liquid clustering requires stats on the cluster key. Trailing it after 51 data
  -- columns fails with DELTA_CLUSTERING_COLUMN_MISSING_STATS. See docs/ISSUES.md I-019.
  CAST(current_timestamp() AS DATE)    AS _ingest_date,
  current_timestamp()                  AS _ingested_at,
  _metadata.file_path                  AS _source_file,
  _metadata.file_modification_time     AS _source_modified_at,
  *
FROM STREAM read_files(
  '/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/inv/',
  format            => 'csv',
  sep               => '\t',
  header            => false,
  quote             => '\0',
  encoding          => 'ISO-8859-1',
  multiLine         => false,
  -- No Hive-style partitioning in these directories. Left to infer, Auto Loader
  -- attempts partition discovery and fails with CF_PARTITON_INFERENCE_ERROR.
  partitionColumns  => '',
  rescuedDataColumn => '_rescued_data',
  schema            => '
    NHTSA_ACTION_NUMBER STRING, MAKE STRING, MODEL STRING, YEAR STRING,
    COMPNAME STRING, MFR_NAME STRING, ODATE STRING, CDATE STRING, CAMPNO STRING,
    SUBJECT STRING, SUMMARY STRING
  '
);
