-- FleetGuard bronze: ODI complaints (FLAT_CMPL.txt)
--
-- quote => '\0' is REQUIRED, not stylistic. These files are tab-delimited with no
-- quoting convention, but CDESCR is free text containing double quotes. Spark's CSV
-- reader treats " as a quote char by default and swallows tab delimiters, silently
-- mis-parsing 143 rows — with _rescued_data still reporting 0. See docs/ISSUES.md I-012.
--
-- Schema is declared explicitly (all STRING) so that any upstream column change lands
-- in _rescued_data instead of shifting every field one position to the left. Typing and
-- conformance are silver's job, per proposal §4.2.

CREATE OR REFRESH STREAMING TABLE bronze_complaints
CLUSTER BY (_ingest_date)
COMMENT 'Raw ODI consumer complaints, one row per complaint-component. Source: FLAT_CMPL.txt.'
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
  '/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/cmpl/',
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
    CMPLID STRING, ODINO STRING, MFR_NAME STRING, MAKETXT STRING, MODELTXT STRING,
    YEARTXT STRING, CRASH STRING, FAILDATE STRING, FIRE STRING, INJURED STRING,
    DEATHS STRING, COMPDESC STRING, CITY STRING, STATE STRING, VIN STRING,
    DATEA STRING, LDATE STRING, MILES STRING, OCCURENCES STRING, CDESCR STRING,
    CMPL_TYPE STRING, POLICE_RPT_YN STRING, PURCH_DT STRING, ORIG_OWNER_YN STRING,
    ANTI_BRAKES_YN STRING, CRUISE_CONT_YN STRING, NUM_CYLS STRING, DRIVE_TRAIN STRING,
    FUEL_SYS STRING, FUEL_TYPE STRING, TRANS_TYPE STRING, VEH_SPEED STRING,
    DOT STRING, TIRE_SIZE STRING, LOC_OF_TIRE STRING, TIRE_FAIL_TYPE STRING,
    ORIG_EQUIP_YN STRING, MANUF_DT STRING, SEAT_TYPE STRING, RESTRAINT_TYPE STRING,
    DEALER_NAME STRING, DEALER_TEL STRING, DEALER_CITY STRING, DEALER_STATE STRING,
    DEALER_ZIP STRING, PROD_TYPE STRING, REPAIRED_YN STRING, MEDICAL_ATTN STRING,
    VEHICLES_TOWED_YN STRING, STATE_OF_INCIDENT STRING, VEHICLE_OPERATOR STRING
  '
);
