-- FleetGuard bronze: manufacturer communications / technical service bulletins
--
-- Glob rather than a single filename: the corpus ships as seven 5-year chunks totalling
-- 5,801,279 rows. The ingest job currently lands only TSBS_RECEIVED_2025-2026; the glob
-- means adding historical chunks needs no pipeline change.
--
-- The frequently-quoted "2.4M rows" figure is only the 2020-2024 chunk. See docs/ISSUES.md I-008.

CREATE OR REFRESH STREAMING TABLE bronze_tsbs
CLUSTER BY (_ingest_date)
COMMENT 'Raw manufacturer communications / TSBs. Bulletins frequently precede recalls and act as a corroborating signal for Model A. Source: TSBS_RECEIVED_*.txt.'
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
  '/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/tsbs/',
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
    NHTSA_ID STRING, REPLACEMENT_TSB_NO STRING, DATEA STRING, TSB_DOCUMENT_ID STRING,
    MFR_COMM_DATE STRING, MFR_CAMPAIGN_ID STRING, COMMUNICATION_TYPE STRING,
    MAKE STRING, MODEL STRING, YEARTXT STRING, NHTSA_COMPONENTS STRING,
    MFR_COMPONENT_SYSTEM STRING, MFR_COMPONENT_SUBSYSTEM STRING, SUMMARY STRING
  '
);
