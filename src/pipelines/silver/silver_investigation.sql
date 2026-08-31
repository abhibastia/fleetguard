-- FleetGuard silver: conformed ODI defect investigations — the backtest ground truth.
--
-- Two grains, both published, because conflating them overstates the evidence base by
-- two orders of magnitude (docs/ISSUES.md I-010):
--   silver_investigation        — 154,367 rows, make/model/year granular
--   silver_investigation_case   — one row per distinct investigation
--
-- open_date (ODATE) is the reference point the lead-time backtest measures against, so
-- a row without one cannot participate. 157 such rows exist; they are quarantined rather
-- than dropped, since a missing open date on an investigation is worth seeing.
--
-- campaign_number (CAMPNO), where present, is NHTSA's documented link to the recall the
-- investigation produced — the join behind silver_recall.

CREATE TEMPORARY VIEW stg_investigation AS
SELECT
  *,
  CONCAT_WS(',',
    CASE WHEN action_number IS NULL THEN 'missing_action_number' END,
    CASE WHEN open_date     IS NULL THEN 'unparseable_odate'     END,
    CASE WHEN close_date IS NOT NULL AND open_date IS NOT NULL AND close_date < open_date
              THEN 'closed_before_opened' END
  ) AS _dq_failures
FROM (
  SELECT
    NULLIF(UPPER(TRIM(NHTSA_ACTION_NUMBER)), '')    AS action_number,
    UPPER(TRIM(MAKE))                               AS make,
    UPPER(TRIM(MODEL))                              AS model,
    TRY_CAST(NULLIF(TRIM(YEAR), '9999') AS INT)     AS model_year,
    UPPER(TRIM(COMPNAME))                           AS component,
    UPPER(TRIM(MFR_NAME))                           AS manufacturer,
    TRY_TO_TIMESTAMP(TRIM(ODATE), 'yyyyMMdd')       AS open_date,
    TRY_TO_TIMESTAMP(TRIM(CDATE), 'yyyyMMdd')       AS close_date,
    NULLIF(UPPER(TRIM(CAMPNO)), '')                 AS campaign_number,
    SUBJECT                                         AS subject,
    SUMMARY                                         AS summary,
    _source_file,
    _ingested_at
  FROM STREAM(bronze_investigations)
);

CREATE OR REFRESH STREAMING TABLE silver_investigation (
  CONSTRAINT routing_holds EXPECT (action_number IS NOT NULL AND open_date IS NOT NULL)
)
CLUSTER BY (make, model, open_date)
COMMENT 'Conformed ODI investigations at make/model/year grain. See silver_investigation_case for the investigation grain — that, not this row count, is the backtest population.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true', 'quality' = 'silver')
AS SELECT * EXCEPT (_dq_failures) FROM STREAM(stg_investigation) WHERE _dq_failures = '';

CREATE OR REFRESH STREAMING TABLE silver_investigation_quarantine
COMMENT 'Investigation rows failing silver quality rules, with reasons in _dq_failures.'
TBLPROPERTIES ('quality' = 'quarantine')
AS SELECT * FROM STREAM(stg_investigation) WHERE _dq_failures <> '';

-- ---------------------------------------------------------------------------
-- One row per investigation. THIS is the backtest denominator, not the row count.
-- Materialized view rather than a streaming table — it aggregates.
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_investigation_case
COMMENT 'One row per distinct ODI investigation. The backtest population: filter open_date >= 2010 for the in-scope set.'
AS
SELECT
  action_number,
  MIN(open_date)                                  AS open_date,
  MAX(close_date)                                 AS close_date,
  MAX(campaign_number)                            AS campaign_number,
  MAX(campaign_number) IS NOT NULL                AS led_to_recall,
  COUNT(*)                                        AS vehicle_rows,
  COUNT(DISTINCT make)                            AS distinct_makes,
  COLLECT_SET(make)                               AS makes,
  COLLECT_SET(component)                          AS components,
  MIN(model_year)                                 AS model_year_min,
  MAX(model_year)                                 AS model_year_max,
  MAX(subject)                                    AS subject
FROM silver_investigation
GROUP BY action_number;
