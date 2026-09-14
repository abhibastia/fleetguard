import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type CostBreakdown, type Evidence, type ServiceCampaign } from "../lib/api";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";

const PROGRESS_FILTER_ALL = "ALL" as const;
const PROGRESS_FILTER_OUTSTANDING = "OUTSTANDING" as const;
const PROGRESS_FILTER_CLOSED = "CLOSED" as const;

type SortKey = "vehicle_count" | "completed_count" | "approved_at" | "total_actual_cost";

/**
 * Launched service campaigns — what the demo checks after approving, and until now the only
 * way to see it again was to re-query Lakebase by hand. `GET /api/service-campaigns` has
 * existed since the approval gate was built; this is its first consumer.
 *
 * Progress per campaign is read from `fleetguard_work_order.status`, so a campaign that looks
 * "launched" but has made no progress (all still OPEN) and one that is fully closed out both
 * read clearly from this one view — that distinction was invisible before work-order status
 * tracking existed.
 */
export function ServiceCampaigns({ onOpen }: { onOpen: (serviceCampaignId: string) => void }) {
  const [campaigns, setCampaigns] = useState<ServiceCampaign[] | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [breakdown, setBreakdown] = useState<CostBreakdown | null>(null);
  const [breakdownError, setBreakdownError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [progressFilter, setProgressFilter] = useState<string>(PROGRESS_FILTER_ALL);
  // The depot breakdown was capped at 10 with a static "+N further depots" note and no way
  // to actually see the rest — found in follow-up feedback after the cap was added.
  const [showAllDepots, setShowAllDepots] = useState(false);

  useEffect(() => {
    let stale = false;
    api
      .serviceCampaigns()
      .then((d) => {
        if (!stale) setCampaigns(d);
      })
      .catch((e: ApiError) => {
        if (stale) return;
        if (e.status === 401) setGated(true);
        else setError(e.message);
      });
    // The measured lead-time figure is public (it's the Evidence page's own headline number)
    // — if this fails, the panel below just omits the lead-time line rather than failing the
    // whole page, since it's context, not the point of this view.
    api
      .evidence()
      .then((d) => {
        if (!stale) setEvidence(d);
      })
      .catch(() => {});
    // Same reasoning: a breakdown failure shouldn't block the campaign table below it — but
    // unlike the evidence fetch, a failure here used to disappear silently (both tables just
    // vanished, indistinguishable from "no costs logged yet"). Found in a UI/UX review,
    // 2026-09-14.
    api
      .costBreakdown()
      .then((d) => {
        if (!stale) setBreakdown(d);
      })
      .catch((e: ApiError) => {
        if (!stale) setBreakdownError(e.message);
      });
    return () => {
      stale = true;
    };
  }, []);

  // Hooks run every render regardless of loading state, so filtering/sorting is computed here
  // (over `campaigns ?? []`) rather than after the early returns below.
  const filtered = useMemo(() => {
    return (campaigns ?? []).filter((c) => {
      const outstanding = c.open_count + c.in_progress_count > 0;
      if (progressFilter === PROGRESS_FILTER_OUTSTANDING) return outstanding;
      if (progressFilter === PROGRESS_FILTER_CLOSED) return !outstanding;
      return true;
    });
  }, [campaigns, progressFilter]);
    const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    filtered,
    (c) => `${c.service_campaign_id} ${c.campaign_id} ${c.title} ${c.approved_by ?? ""}`,
  );
const { sorted, sortKey, sortDir, toggleSort } = useSort<ServiceCampaign, SortKey>(
    searched,
    (c, key) => c[key] ?? 0,
  );

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Launched campaigns are read under your Databricks identity. This public deployment has
          no sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  if (!campaigns && error)
    return (
      <>
        <div className="page-head">
          <h2>Launched campaigns</h2>
          <p>Every service campaign an approver has launched, most recent first.</p>
        </div>
        <PageError title="Launched campaigns could not be loaded." detail={error} />
      </>
    );
  if (!campaigns)
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

  const totalVehicles = campaigns.reduce((n, c) => n + c.vehicle_count, 0);
  const totalOpen = campaigns.reduce((n, c) => n + c.open_count + c.in_progress_count, 0);
  const totalCompleted = campaigns.reduce((n, c) => n + c.completed_count, 0);
  const fullyClosed = campaigns.filter(
    (c) => c.open_count + c.in_progress_count === 0 && c.vehicle_count > 0,
  ).length;
  const totalActualCost = campaigns.reduce((n, c) => n + c.total_actual_cost, 0);
  const totalCosted = campaigns.reduce((n, c) => n + c.costed_count, 0);
  const costedDepots = breakdown?.by_depot.filter((row) => row.total_actual_cost > 0) ?? [];

  return (
    <>
      <div className="page-head">
        <h2>Launched campaigns</h2>
        <p>
          Every service campaign an approver has launched, most recent first, with how far each
          one has actually gotten.
        </p>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="v">{campaigns.length}</div>
          <div className="k">Launched</div>
        </div>
        <div className="stat">
          <div className="v">{totalVehicles.toLocaleString()}</div>
          <div className="k">Vehicles dispatched</div>
        </div>
        <div className={totalOpen > 0 ? "stat is-danger" : "stat is-ok"}>
          <div className="v">{totalOpen.toLocaleString()}</div>
          <div className="k">Still outstanding</div>
        </div>
        <div className="stat is-ok">
          <div className="v">{fullyClosed}</div>
          <div className="k">Fully closed</div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {/* The actual subject of a tab titled "Launched campaigns" used to render LAST, after
          an uncapped 57-row depot cost table pushed the page to ~3,500px — a fleet-safety-team
          member checking on a campaign they just dispatched had to scroll past all of it first.
          Found in a UI/UX review, 2026-09-14: the cost breakdown is supporting detail, not the
          lede, so it now follows the campaign list instead of preceding it. */}
      {campaigns.length === 0 ? (
        <div className="panel">
          No service campaigns launched yet. That is a result, not an error — nothing has been
          approved from the recall queue so far.
        </div>
      ) : (
        <>
          <div className="filter-row">
            <SearchBox
              query={query}
              onChange={setQuery}
              placeholder="Search campaign, title or approver…"
              matched={sorted.length}
              total={filtered.length}
              label="Search launched campaigns"
            />
            <select value={progressFilter} onChange={(e) => setProgressFilter(e.target.value)}>
              <option value={PROGRESS_FILTER_ALL}>All campaigns</option>
              <option value={PROGRESS_FILTER_OUTSTANDING}>Outstanding work only</option>
              <option value={PROGRESS_FILTER_CLOSED}>Fully closed only</option>
            </select>
          </div>

          {sorted.length === 0 ? (
            <div className="panel">No launched campaigns match the current search or filter.</div>
          ) : (
            <div className="wrap">
              <table className="rows">
                <thead>
                  <tr>
                    <th>Service campaign</th>
                    <th>Recall</th>
                    <th>Title</th>
                    <th
                      className="num sortable"
                      onClick={() => toggleSort("vehicle_count")}
                    >
                      Vehicles<SortIndicator columnKey="vehicle_count" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th className="sortable" onClick={() => toggleSort("completed_count")}>
                      Progress<SortIndicator columnKey="completed_count" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th
                      className="num sortable"
                      onClick={() => toggleSort("total_actual_cost")}
                    >
                      Logged cost<SortIndicator columnKey="total_actual_cost" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th>Approved by</th>
                    <th className="sortable" onClick={() => toggleSort("approved_at")}>
                      Approved<SortIndicator columnKey="approved_at" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((c) => (
                    <tr key={c.service_campaign_id} onClick={() => onOpen(c.service_campaign_id)}>
                      <td className="muted">{c.service_campaign_id}</td>
                      <td>{c.campaign_id}</td>
                      <td>{c.title}</td>
                      <td className="num">{c.vehicle_count.toLocaleString()}</td>
                      <td>
                        {c.completed_count}/{c.vehicle_count} completed
                        {c.cancelled_count > 0 && (
                          <span className="muted"> · {c.cancelled_count} cancelled</span>
                        )}
                      </td>
                      <td className="num">
                        {c.costed_count > 0 ? (
                          <>
                            ${c.total_actual_cost.toLocaleString()}
                            <span className="muted">
                              {" "}
                              ({c.costed_count}/{c.vehicle_count})
                            </span>
                          </>
                        ) : (
                          <span className="muted">not logged</span>
                        )}
                      </td>
                      <td className="muted">{c.approved_by ?? "—"}</td>
                      <td className="muted">
                        {c.approved_at ? c.approved_at.slice(0, 10) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <div className="panel prose" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>Cost of early action — logged, not assumed</h3>
        {evidence && (
          <p>
            FleetGuard's detection signal fires a median{" "}
            <strong>{evidence.real.median_lead_days} days</strong> before NHTSA would typically
            open a formal investigation into a defect pattern (measured across{" "}
            {evidence.real.n} investigations since 2010 — see <strong>Evidence</strong>). That is
            lead time your fleet can spend on proactive service instead of reactive, urgent
            repairs once a recall lands.
          </p>
        )}
        <p>
          <strong>${totalActualCost.toLocaleString()}</strong> in repair cost has been logged
          across <strong>{totalCosted}</strong> of {totalCompleted.toLocaleString()} completed
          work orders ({totalVehicles.toLocaleString()} dispatched in total). No blended average
          is applied here — a steering-rack repair and a brake job cost different amounts, so
          the tables below are broken down by what was actually being fixed and where.
        </p>
        {totalCompleted > totalCosted && (
          <p className="footnote" style={{ marginBottom: 0 }}>
            {totalCompleted - totalCosted} completed work order
            {totalCompleted - totalCosted === 1 ? "" : "s"} have no cost logged yet — the totals
            above reflect only what has been entered, not an estimate for the rest. Log a cost on
            the <strong>Work orders</strong> tab to fill this in.
          </p>
        )}
      </div>

      {breakdownError ? (
        // A failed breakdown fetch used to just make both tables vanish, indistinguishable
        // from "nothing logged yet" — found in a UI/UX review, 2026-09-14.
        <PageError title="Cost breakdown could not be loaded." detail={breakdownError} />
      ) : (
        breakdown &&
        (breakdown.by_component.length > 0 || breakdown.by_depot.length > 0) && (
          <div className="cost-split" style={{ marginTop: 16 }}>
            {/* Bare tables with no heading and no .panel — the one break from this console's
                "everything sits in a card" rule. Found in the same review. */}
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>By component</h3>
              <div className="wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Component</th>
                      <th className="num">Logged cost</th>
                      <th className="num">Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {breakdown.by_component.map((row) => (
                      <tr key={row.key}>
                        <td>{row.key}</td>
                        <td className="num">${row.total_actual_cost.toLocaleString()}</td>
                        <td className="num muted">
                          {row.costed_count}/{row.total_work_orders}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>By depot</h3>
              <div className="wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Depot</th>
                      <th className="num">Logged cost</th>
                      <th className="num">Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* Capped at 10 by default — an uncapped 57-row depot table is what buried
                        the campaign list below the fold in the first place (see above). Unlike
                        Campaign.tsx's own "+N further" depot list (a search box, since a depot
                        manager's first question is "is MY depot here"), this list is small
                        enough that a plain show-all toggle is the right amount of interaction —
                        no need for a second search box on the same page. */}
                    {(showAllDepots ? costedDepots : costedDepots.slice(0, 10)).map((row) => (
                      <tr key={row.key}>
                        <td>{row.key}</td>
                        <td className="num">${row.total_actual_cost.toLocaleString()}</td>
                        <td className="num muted">
                          {row.costed_count}/{row.total_work_orders}
                        </td>
                      </tr>
                    ))}
                    {breakdown.by_depot.every((row) => row.total_actual_cost === 0) && (
                      <tr>
                        <td colSpan={3} className="muted">
                          No logged costs yet at any depot.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                {costedDepots.length > 10 && (
                  <button
                    className="linklike"
                    style={{ marginTop: 10 }}
                    onClick={() => setShowAllDepots((v) => !v)}
                  >
                    {showAllDepots
                      ? "Show fewer ←"
                      : `+${costedDepots.length - 10} further depots — show all →`}
                  </button>
                )}
              </div>
            </div>
          </div>
        )
      )}
    </>
  );
}
