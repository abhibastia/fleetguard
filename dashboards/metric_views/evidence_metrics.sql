CREATE OR REPLACE VIEW bootcamp_students.fleetguard.evidence_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: bootcamp_students.fleetguard.gold_lead_time_summary
comment: "Governed backtest metrics for the Evidence page. Sourced from gold_lead_time_summary (not v3 -- v3's detected_v3 is the abandoned semantic-clustering result)."

dimensions:
  - name: Arm
    expr: arm
    comment: "REAL (investigated series) or PLACEBO (never investigated)."

measures:
  - name: Investigations
    expr: SUM(n)
    comment: "Investigation count. Group by Arm to split real vs placebo."

  - name: Detected
    expr: SUM(detected)
    comment: "Investigations the detector flagged before the reference date."

  - name: Detection Rate %
    expr: SUM(detected) * 100.0 / SUM(n)
    comment: "Detected / Investigations, as a percentage. Group by Arm."

  - name: Median Lead Days
    expr: AVG(median_lead_days)
    comment: "One row per arm in the source, so AVG is an exact passthrough."

  - name: Real Detection Rate %
    expr: SUM(CASE WHEN arm LIKE 'REAL%' THEN detected END) * 100.0 / SUM(CASE WHEN arm LIKE 'REAL%' THEN n END)
    comment: "Real-arm rate as a single value. Published figure: 16.0%."

  - name: Placebo Detection Rate %
    expr: SUM(CASE WHEN arm LIKE 'PLACEBO%' THEN detected END) * 100.0 / SUM(CASE WHEN arm LIKE 'PLACEBO%' THEN n END)
    comment: "Placebo-arm rate as a single value. Published figure: 11.1%."

  - name: Lift
    expr: (SUM(CASE WHEN arm LIKE 'REAL%' THEN detected END) * 1.0 / SUM(CASE WHEN arm LIKE 'REAL%' THEN n END)) / (SUM(CASE WHEN arm LIKE 'PLACEBO%' THEN detected END) * 1.0 / SUM(CASE WHEN arm LIKE 'PLACEBO%' THEN n END))
    comment: "Real rate / placebo rate. Published figure: 1.44x."

  - name: Real Median Lead Days
    expr: MAX(CASE WHEN arm LIKE 'REAL%' THEN median_lead_days END)
    comment: "Published figure: 197 days."

  - name: Placebo Median Lead Days
    expr: MAX(CASE WHEN arm LIKE 'PLACEBO%' THEN median_lead_days END)
    comment: "Published figure: 343 days."
$$
