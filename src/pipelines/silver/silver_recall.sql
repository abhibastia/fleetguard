-- FleetGuard silver: conformed recall campaigns + quarantine
--
-- Scope is (make, model, model_year) + the BGMAN..ENDMAN manufacture window. There are
-- NO VIN ranges in this data — see docs/ISSUES.md I-011. The deterministic match in §7
-- is set membership on those columns, not a VIN range check.
--
-- DO_NOT_DRIVE / PARK_OUTSIDE are stored title-case 'Yes'/'No'. Normalised here so no
-- downstream predicate can silently return zero rows (I-022).
--
-- 224 rows carry a manufacture window that ends before it starts. They are quarantined
-- rather than dropped, because an inverted window is exactly the kind of scope defect
-- that would silently under-match a fleet.

CREATE TEMPORARY VIEW stg_recall AS
SELECT
  *,
  CONCAT_WS(',',
    CASE WHEN recall_record_id     IS NULL THEN 'missing_record_id'   END,
    CASE WHEN campaign_number      IS NULL THEN 'missing_campaign_no' END,
    CASE WHEN report_received_date IS NULL THEN 'unparseable_rcdate'  END,
    CASE WHEN manufacture_start IS NOT NULL AND manufacture_end IS NOT NULL
              AND manufacture_start > manufacture_end
              THEN 'inverted_manufacture_window' END
  ) AS _dq_failures
FROM (
  SELECT
    RECORD_ID                                             AS recall_record_id,
    NULLIF(UPPER(TRIM(CAMPNO)), '')                       AS campaign_number,
    UPPER(TRIM(MAKETXT))                                  AS make,
    UPPER(TRIM(MODELTXT))                                 AS model,
    TRY_CAST(NULLIF(TRIM(YEARTXT), '9999') AS INT)        AS model_year,
    UPPER(TRIM(COMPNAME))                                 AS component,
    UPPER(TRIM(MFGNAME))                                  AS manufacturer,
    UPPER(TRIM(RCLTYPECD))                                AS recall_type,   -- V/T/E/C
    UPPER(TRIM(INFLUENCED_BY))                            AS initiated_by,  -- MFR/ODI/OVSC

    -- the manufacture window that, with make/model/year, defines campaign scope
    TRY_TO_TIMESTAMP(TRIM(BGMAN), 'yyyyMMdd')             AS manufacture_start,
    TRY_TO_TIMESTAMP(TRIM(ENDMAN), 'yyyyMMdd')            AS manufacture_end,

    TRY_CAST(NULLIF(TRIM(POTAFF), '') AS BIGINT)          AS units_potentially_affected,

    -- RCDATE (Part 573 received) is 100% populated; ODATE (owner notified) is 98.1%.
    -- RCDATE is therefore the stable campaign timestamp.
    TRY_TO_TIMESTAMP(TRIM(RCDATE), 'yyyyMMdd')            AS report_received_date,
    TRY_TO_TIMESTAMP(TRIM(ODATE),  'yyyyMMdd')            AS owner_notified_date,

    -- the real "Park It" flags, case-normalised
    UPPER(TRIM(DO_NOT_DRIVE)) = 'YES'                     AS do_not_drive,
    UPPER(TRIM(PARK_OUTSIDE)) = 'YES'                     AS park_outside,
    (UPPER(TRIM(DO_NOT_DRIVE)) = 'YES'
     OR UPPER(TRIM(PARK_OUTSIDE)) = 'YES')                AS park_it,

    DESC_DEFECT                                           AS defect_description,
    CONEQUENCE_DEFECT                                     AS consequence_description,
    CORRECTIVE_ACTION                                     AS corrective_action,
    NULLIF(TRIM(FMVSS), '')                               AS fmvss_number,

    _source_file,
    _ingested_at
  FROM STREAM(bronze_recalls)
);

CREATE OR REFRESH STREAMING TABLE silver_recall (
  CONSTRAINT routing_holds EXPECT (campaign_number IS NOT NULL AND report_received_date IS NOT NULL)
)
CLUSTER BY (make, model, model_year)
COMMENT 'Conformed recall campaigns, post-2010. One row per campaign-make-model-year. Scope is make/model/year plus manufacture window — this file carries no VIN ranges.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true', 'quality' = 'silver')
AS SELECT * EXCEPT (_dq_failures) FROM STREAM(stg_recall) WHERE _dq_failures = '';

CREATE OR REFRESH STREAMING TABLE silver_recall_quarantine
COMMENT 'Recall rows failing silver quality rules, with reasons in _dq_failures.'
TBLPROPERTIES ('quality' = 'quarantine')
AS SELECT * FROM STREAM(stg_recall) WHERE _dq_failures <> '';
