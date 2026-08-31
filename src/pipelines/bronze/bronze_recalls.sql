-- FleetGuard bronze: ODI recall campaigns (FLAT_RCL_POST_2010.txt)
--
-- DO_NOT_DRIVE / PARK_OUTSIDE (fields 28/29) are the real "Park It" flags. They were
-- added May 2025 and are backfilled unevenly — zero for 2010–2011. See docs/ISSUES.md I-014.
--
-- Note there is NO VIN-range column in this file. Campaign scope is
-- MAKETXT/MODELTXT/YEARTXT plus the BGMAN..ENDMAN manufacture window. See I-011.

CREATE OR REFRESH STREAMING TABLE bronze_recalls
CLUSTER BY (_ingest_date)
COMMENT 'Raw ODI recall campaigns, post-2010. One row per campaign-make-model-year. Source: FLAT_RCL_POST_2010.txt.'
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
  '/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/rcl/',
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
    RECORD_ID STRING, CAMPNO STRING, MAKETXT STRING, MODELTXT STRING, YEARTXT STRING,
    MFGCAMPNO STRING, COMPNAME STRING, MFGNAME STRING, BGMAN STRING, ENDMAN STRING,
    RCLTYPECD STRING, POTAFF STRING, ODATE STRING, INFLUENCED_BY STRING, MFGTXT STRING,
    RCDATE STRING, DATEA STRING, RPNO STRING, FMVSS STRING, DESC_DEFECT STRING,
    CONEQUENCE_DEFECT STRING, CORRECTIVE_ACTION STRING, NOTES STRING, RCL_CMPT_ID STRING,
    MFR_COMP_NAME STRING, MFR_COMP_DESC STRING, MFR_COMP_PTNO STRING,
    DO_NOT_DRIVE STRING, PARK_OUTSIDE STRING
  '
);
