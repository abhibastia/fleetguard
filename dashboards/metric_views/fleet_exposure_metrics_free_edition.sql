-- Free-edition variant of fleet_exposure_metrics.sql — same measures, different metastore.
--
-- WHY A SEPARATE FILE. Metric views are UC objects, created once via the SQL Statement API
-- (I-065), not a bundle resource or a notebook widget — there is no `${var.catalog}` mechanism
-- reaching this SQL text. Free Edition is a wholly separate account/workspace/metastore from
-- `abhi` (`fleetguard.capstone`, not `bootcamp_students.fleetguard`), so the fully-qualified
-- `CREATE VIEW` name and its `source:` line cannot be shared with the prod file. Same reasoning
-- as app/backend_free_edition/app.yaml existing alongside app/backend/app.yaml.

CREATE OR REPLACE VIEW fleetguard.capstone.fleet_exposure_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
source: fleetguard.capstone.gold_fleet_exposure
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
