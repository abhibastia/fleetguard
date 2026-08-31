-- FleetGuard silver: conformed consumer complaints + quarantine
--
-- Three things here are deliberate and were each wrong at least once:
--
-- 1. DEDUP ON CMPLID, NEVER ODINO. ODINO repeats across the components of one complaint
--    (1,615,482 distinct across 2,240,289 rows). Deduplicating on it discards 624,807
--    legitimate component reports — 27.9% of the corpus. See docs/ISSUES.md I-023.
-- 2. PROD_TYPE branch runs BEFORE column-level expectations, so tire-only and
--    restraint-only columns are never evaluated against vehicle rows.
-- 3. Y/N and Yes/No fields are normalised with UPPER(TRIM(...)). DO_NOT_DRIVE is stored
--    title-case; a case-sensitive comparison silently returns zero rows (I-022).
--
-- Component comes from COMPDESC, which NHTSA already ships structured. No ai_extract
-- here — that runs per surfaced cluster in Phase 9 (I-009).
--
-- QUALITY ROUTING. Failure reasons are computed ONCE in the staging view and drive both
-- outputs, so the silver predicate and the quarantine predicate cannot drift apart. Every
-- in-scope row lands in exactly one of silver_complaint or silver_complaint_quarantine —
-- the two counts must always sum to the V+T row count. No silent drops (§6).

CREATE TEMPORARY VIEW stg_complaint AS
SELECT
  *,
  CONCAT_WS(',',
    CASE WHEN complaint_id  IS NULL THEN 'missing_complaint_id' END,
    CASE WHEN received_date IS NULL THEN 'unparseable_ldate'    END,
    CASE WHEN make          IS NULL THEN 'missing_make'         END,
    CASE WHEN incident_date IS NOT NULL AND incident_date > received_date
              THEN 'incident_after_received' END,
    CASE WHEN injured < 0 OR deaths < 0 THEN 'negative_harm_count' END
  ) AS _dq_failures
FROM (
  SELECT
    -- identity
    CMPLID                                              AS complaint_id,
    ODINO                                               AS odi_number,      -- group key, NOT unique
    UPPER(TRIM(PROD_TYPE))                              AS product_type,

    -- vehicle identity, normalised (NHTSA's changelog documents these strings drifting)
    UPPER(TRIM(MFR_NAME))                               AS manufacturer,
    UPPER(TRIM(MAKETXT))                                AS make,
    UPPER(TRIM(MODELTXT))                               AS model,
    TRY_CAST(NULLIF(TRIM(YEARTXT), '9999') AS INT)      AS model_year,
    UPPER(TRIM(COMPDESC))                               AS component,

    -- partial VIN: CHAR(11), identifies no individual vehicle. Enrichment only (I-011).
    NULLIF(TRIM(VIN), '')                               AS vin_partial,

    -- dates
    TRY_TO_TIMESTAMP(TRIM(LDATE),    'yyyyMMdd')        AS received_date,
    TRY_TO_TIMESTAMP(TRIM(FAILDATE), 'yyyyMMdd')        AS incident_date,

    -- harm outcomes. Y/N -> boolean; anything else -> NULL, so "unanswered" stays
    -- distinguishable from "answered no".
    CASE UPPER(TRIM(CRASH))             WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS crash,
    CASE UPPER(TRIM(FIRE))              WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS fire,
    CASE UPPER(TRIM(MEDICAL_ATTN))      WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS medical_attention,
    CASE UPPER(TRIM(POLICE_RPT_YN))     WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS police_report,
    CASE UPPER(TRIM(VEHICLES_TOWED_YN)) WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS vehicle_towed,

    -- counts: NULL (unanswered) must stay distinct from 0 (reported none)
    TRY_CAST(NULLIF(TRIM(INJURED), '') AS INT)          AS injured,
    TRY_CAST(NULLIF(TRIM(DEATHS),  '') AS INT)          AS deaths,

    -- tire-only attributes (populated only where product_type = 'T')
    NULLIF(TRIM(TIRE_SIZE), '')                         AS tire_size,
    NULLIF(TRIM(DOT), '')                               AS tire_dot_id,
    NULLIF(TRIM(LOC_OF_TIRE), '')                       AS tire_location,
    NULLIF(TRIM(TIRE_FAIL_TYPE), '')                    AS tire_failure_type,

    -- narrative retained in full. PII is tagged, never deleted, so the embedding
    -- pipeline keeps it while ABAC masks it per role (Phase 10).
    CDESCR                                              AS narrative,
    LENGTH(CDESCR)                                      AS narrative_length,

    TRY_CAST(NULLIF(TRIM(MILES), '') AS BIGINT)         AS mileage,
    UPPER(TRIM(CMPL_TYPE))                              AS complaint_source,
    UPPER(TRIM(STATE_OF_INCIDENT))                      AS incident_state,

    _source_file,
    _ingested_at
  FROM STREAM(bronze_complaints)
  WHERE UPPER(TRIM(PROD_TYPE)) IN ('V', 'T')   -- scope filter, applied before quality rules
);

-- ---------------------------------------------------------------------------
-- Conformed rows. The constraints are invariants that must hold *after* routing —
-- if one ever fires, the split logic itself is broken, not the source data.
-- ---------------------------------------------------------------------------
CREATE OR REFRESH STREAMING TABLE silver_complaint (
  CONSTRAINT routing_holds EXPECT (complaint_id IS NOT NULL AND received_date IS NOT NULL)
)
CLUSTER BY (make, model, component, received_date)
COMMENT 'Conformed vehicle and tire complaints, one row per CMPLID. Harm fields typed with NULL distinct from zero. Rows failing quality rules are in silver_complaint_quarantine, never dropped.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true', 'quality' = 'silver')
AS SELECT * EXCEPT (_dq_failures) FROM STREAM(stg_complaint) WHERE _dq_failures = '';

-- ---------------------------------------------------------------------------
-- Quarantine: same reasons, opposite side of the split.
-- ---------------------------------------------------------------------------
CREATE OR REFRESH STREAMING TABLE silver_complaint_quarantine
COMMENT 'Complaint rows failing silver quality rules, with reasons in _dq_failures. Growth here is a signal to investigate, not a normal state.'
TBLPROPERTIES ('quality' = 'quarantine')
AS SELECT * FROM STREAM(stg_complaint) WHERE _dq_failures <> '';
