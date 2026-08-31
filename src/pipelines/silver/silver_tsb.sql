-- FleetGuard silver: conformed manufacturer communications / technical service bulletins.
--
-- Same row-vs-entity gap as investigations (docs/ISSUES.md I-024): 5,801,279 rows but
-- 258,438 distinct NHTSA_ID — each bulletin repeats per make/model/year, ~22x. Both
-- grains are published so "5.8M rows" is never mistaken for "5.8M bulletins".
--
-- TSBs are Model A's corroborating signal: a manufacturer bulletin on the same component
-- raises confidence that a complaint cluster reflects a real defect rather than noise.

CREATE OR REFRESH STREAMING TABLE silver_tsb (
  CONSTRAINT valid_nhtsa_id EXPECT (nhtsa_id IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT valid_make     EXPECT (make IS NOT NULL)     ON VIOLATION DROP ROW,
  CONSTRAINT has_a_date     EXPECT (communication_date IS NOT NULL OR added_date IS NOT NULL) ON VIOLATION DROP ROW
)
CLUSTER BY (make, model, communication_date)
COMMENT 'Conformed manufacturer communications at make/model/year grain. 5.8M rows / 258,438 distinct bulletins — see silver_tsb_bulletin for the bulletin grain.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true', 'quality' = 'silver')
AS
SELECT
  UPPER(TRIM(NHTSA_ID))                             AS nhtsa_id,
  NULLIF(TRIM(TSB_DOCUMENT_ID), '')                 AS document_id,
  UPPER(TRIM(MAKE))                                 AS make,
  UPPER(TRIM(MODEL))                                AS model,
  TRY_CAST(NULLIF(TRIM(YEARTXT), '9999') AS INT)    AS model_year,
  UPPER(TRIM(COMMUNICATION_TYPE))                   AS communication_type,
  UPPER(TRIM(NHTSA_COMPONENTS))                     AS components,
  UPPER(TRIM(MFR_COMPONENT_SYSTEM))                 AS mfr_component_system,
  UPPER(TRIM(MFR_COMPONENT_SUBSYSTEM))              AS mfr_component_subsystem,
  TRY_TO_TIMESTAMP(TRIM(MFR_COMM_DATE), 'yyyyMMdd') AS communication_date,
  TRY_TO_TIMESTAMP(TRIM(DATEA), 'yyyyMMdd')         AS added_date,
  NULLIF(TRIM(MFR_CAMPAIGN_ID), '')                 AS mfr_campaign_id,
  SUMMARY                                           AS summary,
  _source_file,
  _ingested_at
FROM STREAM(bronze_tsbs);

-- ---------------------------------------------------------------------------
-- One row per bulletin (258,438), for any count that means "how many bulletins".
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_tsb_bulletin
COMMENT 'One row per distinct manufacturer communication (258,438). Use this, not the 5.8M row count, when counting bulletins.'
AS
SELECT
  nhtsa_id,
  MAX(document_id)              AS document_id,
  MIN(communication_date)       AS communication_date,
  MAX(communication_type)       AS communication_type,
  COUNT(*)                      AS vehicle_rows,
  COLLECT_SET(make)             AS makes,
  MIN(model_year)               AS model_year_min,
  MAX(model_year)               AS model_year_max,
  MAX(components)               AS components,
  MAX(summary)                  AS summary
FROM silver_tsb
GROUP BY nhtsa_id;
