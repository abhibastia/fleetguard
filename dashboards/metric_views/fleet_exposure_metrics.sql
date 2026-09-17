CREATE OR REPLACE VIEW bootcamp_students.fleetguard.fleet_exposure_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: bootcamp_students.fleetguard.gold_fleet_exposure
comment: "Governed fleet-exposure metrics for the Overview page. Sourced from gold_fleet_exposure (vin x campaign_number match rows; not unique on that pair, see I-059, the table has no committed source DDL, schema reconstructed from usage)."

dimensions:
  - name: Match Basis
    expr: match_basis
    comment: "EXACT or MODEL_VARIANT, how the VIN was matched to the campaign."
  - name: Segment
    expr: segment
    comment: "Vehicle segment, e.g. VAN, PICKUP, HEAVY."

measures:
  - name: Vehicles Exposed
    expr: COUNT(DISTINCT vin)
    comment: "Distinct VINs with at least one open-campaign match."

  - name: Depots Affected
    expr: COUNT(DISTINCT depot_id)
    comment: "Distinct depots with at least one exposed vehicle."

  - name: Open Campaigns
    expr: COUNT(DISTINCT campaign_number)
    comment: "Distinct campaign numbers present in the exposure table."

  - name: Urgent Campaigns
    expr: COUNT(DISTINCT CASE WHEN park_it OR do_not_drive THEN campaign_number END)
    comment: "Campaigns flagged park_it or do_not_drive."
$$
