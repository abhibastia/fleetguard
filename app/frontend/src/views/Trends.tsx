import { api } from "../lib/api";
import { BarChart, type BarChartDatum } from "../lib/BarChart";
import { PageError } from "../lib/PageError";
import { useFetch } from "../lib/useFetch";

/**
 * Historical trend — every other view in this console answers "what's true right now"; this
 * answers "is it getting better or worse." `fleetguard_recall_campaign.issued_at` is the real
 * NHTSA filing date (not a demo timestamp), joined to the fleet's own real exposure match —
 * 13 years of it, already loaded into Lakebase from Phase 2, no SQL Warehouse call needed.
 *
 * Two charts, not one: campaign count and vehicles exposed move together most years but not
 * always (a handful of high-volume campaigns can move vehicle count without moving campaign
 * count much, or vice versa) — collapsing them into one chart would hide that.
 *
 * The most recent year is flagged as partial rather than plotted like every other bar — found
 * during review: the data runs through `latest_issued_at`, not through year-end, so a shorter
 * final bar would otherwise read as "recalls declined" when it may just mean "the year isn't
 * over." Same discipline as the rest of this app never implying a completeness it doesn't have.
 */
export function Trends() {
  const { data, error, gated } = useFetch(() => api.recallTrend(), []);

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          The recall trend is read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  if (!data && error)
    return (
      <>
        <div className="page-head">
          <h2>Recall trend</h2>
          <p>Fleet-relevant recall campaigns by year.</p>
        </div>
        <PageError title="Recall trend could not be loaded." detail={error} />
      </>
    );
  if (!data)
    return (
      <>
        <div className="stats">
          {[0, 1, 2].map((i) => (
            <div className="stat" key={i}>
              <div className="skeleton tall" style={{ marginBottom: 0 }} />
            </div>
          ))}
        </div>
        <div className="skeleton wide tall" />
      </>
    );

  const { points, latest_issued_at } = data;

  if (points.length === 0)
    return (
      <div className="panel">
        No dated recall campaigns loaded yet. That is a result, not an error — the fleet
        registry has not been populated on this deployment.
      </div>
    );

  const totalCampaigns = points.reduce((n, p) => n + p.campaigns, 0);
  const totalUrgent = points.reduce((n, p) => n + p.urgent_campaigns, 0);
  const latest = points[points.length - 1];
  const first = points[0];

  // A year is "partial" if the data's own latest real date falls before that year's end -
  // not derived from today's date, since the corpus's own freshness may lag further still.
  const latestDate = latest_issued_at ? new Date(`${latest_issued_at}T00:00:00Z`) : null;
  const isPartialYear = (year: number) =>
    latestDate !== null &&
    latestDate.getUTCFullYear() === year &&
    !(latestDate.getUTCMonth() === 11 && latestDate.getUTCDate() === 31);
  const partialNote =
    latestDate && isPartialYear(latest.year)
      ? `Data runs through ${latest_issued_at}, not year-end — the shorter ${latest.year} bar (marked *) reflects an incomplete year, not fewer recalls.`
      : null;

  const campaignData: BarChartDatum[] = points.map((p) => ({
    label: String(p.year),
    value: p.campaigns,
    highlightValue: p.urgent_campaigns,
    partial: isPartialYear(p.year),
    title: `${p.year}${isPartialYear(p.year) ? " (partial)" : ""}: ${p.campaigns} campaign${
      p.campaigns === 1 ? "" : "s"
    }${p.urgent_campaigns > 0 ? `, ${p.urgent_campaigns} Park It / Do Not Drive` : ""}`,
  }));
  const vehicleData: BarChartDatum[] = points.map((p) => ({
    label: String(p.year),
    value: p.vehicles_exposed,
    partial: isPartialYear(p.year),
    title: `${p.year}${isPartialYear(p.year) ? " (partial)" : ""}: ${p.vehicles_exposed.toLocaleString()} vehicles exposed`,
  }));

  return (
    <>
      <div className="page-head">
        <h2>Recall trend</h2>
        <p>
          Fleet-relevant recall campaigns by year, {first.year}–{latest.year} — NHTSA's real
          filing date, joined to the fleet's own exposure match.
        </p>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="v">{points.length}</div>
          <div className="k">Years with fleet-relevant recalls</div>
        </div>
        <div className="stat">
          <div className="v">{totalCampaigns.toLocaleString()}</div>
          <div className="k">Campaigns, all years</div>
        </div>
        <div className={totalUrgent > 0 ? "stat is-danger" : "stat is-ok"}>
          <div className="v">{totalUrgent.toLocaleString()}</div>
          <div className="k">Park It / Do Not Drive campaigns</div>
        </div>
        <div className="stat">
          <div className="v">{latest.campaigns}</div>
          <div className="k">
            Campaigns in {latest.year}
            {isPartialYear(latest.year) && <span className="muted"> (partial)</span>}
          </div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Campaigns per year</h3>
        <p className="muted footnote" style={{ marginTop: 0 }}>
          Red segment is the portion that carried a Park It or Do Not Drive order.
          {partialNote && ` ${partialNote}`}
        </p>
        <BarChart data={campaignData} />
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>Vehicles exposed per year</h3>
        <p className="muted footnote" style={{ marginTop: 0 }}>
          Distinct fleet vehicles matched to a campaign filed that year — a large campaign can
          move this without moving the campaign count much, and vice versa, which is why this
          is a separate chart rather than folded into the one above.
          {partialNote && ` ${partialNote}`}
        </p>
        <BarChart data={vehicleData} />
      </div>
    </>
  );
}
