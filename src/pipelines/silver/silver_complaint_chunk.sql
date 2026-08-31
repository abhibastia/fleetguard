-- FleetGuard silver: complaint narrative chunks — the AI Search embedding source.
--
-- 512-token target, ~2,048 characters at the usual ~4 chars/token heuristic, with a
-- 256-character overlap so a defect description split across a boundary still retrieves.
--
-- HONEST NOTE ON THE RATIO. `CDESCR` is `CHAR(2048)`, so a complaint narrative physically
-- cannot exceed ~530 tokens. Measured: mean 517 chars, p95 1,477, max 2,132 — only 1,390
-- of 2,209,123 rows (0.06%) split at all. This table is therefore ~1.0006 chunks per
-- complaint, and the chunker is not doing heavy lifting here. It is retained because it
-- gives one uniform retrieval path, satisfies the stated chunking requirement, and is
-- real splitting logic that will matter on longer sources — investigation summaries
-- average 2,417 chars with 66% over one chunk. See docs/ISSUES.md I-026.
--
-- Splitting is on character offsets rather than word boundaries. For 0.06% of rows, with
-- a 256-character overlap covering any word broken at a seam, that is a deliberate
-- simplicity trade rather than an oversight.

CREATE OR REFRESH STREAMING TABLE silver_complaint_chunk
CLUSTER BY (make, model, component)
COMMENT 'Complaint narratives chunked for embedding. ~1.0006 chunks per complaint — CDESCR is CHAR(2048), so splits are rare by construction. Carries harm metadata so retrieval can be filtered to complaints involving fire or injury.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'true',   -- prerequisite for AI Search Delta Sync
  'quality' = 'silver'
)
AS
SELECT
  -- Primary key for the vector index: one complaint may yield several chunks.
  CONCAT(complaint_id, '-', CAST(chunk_index AS STRING))          AS chunk_id,
  complaint_id,
  chunk_index,
  chunk_total,
  SUBSTRING(narrative, chunk_index * 1792 + 1, 2048)              AS chunk_text,

  -- Retrieval filters. §4.3 wants the agent able to restrict a semantic search to
  -- complaints that actually involved a fire or an injury.
  product_type,
  make,
  model,
  model_year,
  component,
  received_date,
  incident_date,
  crash,
  fire,
  injured,
  deaths,
  medical_attention,
  police_report,
  (crash OR fire OR COALESCE(injured, 0) > 0 OR COALESCE(deaths, 0) > 0) AS any_harm,
  odi_number,
  _ingested_at
FROM (
  SELECT
    *,
    -- Chunk count must be driven by the WINDOW (2048), not the stride (1792).
    -- ceil(len/stride) is wrong: a 1,793-char narrative would yield a second chunk
    -- containing a single character. That inflated the table to 1.0269 chunks per
    -- complaint (59,485 spurious splits) and would have paid to embed 1-char chunks.
    --   chunks = max(1, ceil((len - window) / stride) + 1)
    GREATEST(CAST(CEIL((LENGTH(narrative) - 2048) / 1792.0) AS INT) + 1, 1) AS chunk_total,
    EXPLODE(
      SEQUENCE(0, GREATEST(CAST(CEIL((LENGTH(narrative) - 2048) / 1792.0) AS INT), 0))
    )                                                              AS chunk_index
  FROM STREAM(silver_complaint)
  -- A narrative shorter than ~5 words carries no retrievable signal and still costs
  -- tokens to embed. Measured: the corpus contains narratives as short as 1 character.
  WHERE narrative IS NOT NULL AND LENGTH(TRIM(narrative)) >= 20
);
