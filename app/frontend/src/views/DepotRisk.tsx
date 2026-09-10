import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type DepotRisk as DepotRiskRow } from "../lib/api";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";

const REGION_FILTER_ALL = "ALL" as const;

type SortKey =
  | "fleet_size"
  | "urgent_vehicles_exposed"
  | "total_vehicles_exposed"
  | "distinct_campaigns"
  | "outstanding_work_orders"
  | "overdue_work_orders";

/** High/medium/none by urgent exposure as a share of the depot's own fleet, not raw count —
 *  a depot with 10 urgent vehicles out of 50 is worse off than one with 10 out of 300, and a
 *  raw-count-only heatmap would rank them the same. */
function riskTier(d: DepotRiskRow): "high" | "medium" | "none" {
  if (d.fleet_size === 0 || d.urgent_vehicles_exposed === 0) return "none";
  const pct = d.urgent_vehicles_exposed / d.fleet_size;
  return pct >= 0.05 ? "high" : "medium";
}

/**
 * Depot-level risk — the fleet-wide view no single-campaign or single-work-order screen
 * gives you. `fleetguard_depot` has existed since Phase 2 with nothing reading it; `GET
 * /api/depot-risk` is its first consumer.
 *
 * Deliberately no single blended "risk score" — a composite index with hidden weights is the
 * same mistake caught once already in this project (I-069, a flat cost multiplier that looked
 * data-driven but wasn't). Real component numbers, sortable and filterable, tell the truth
 * better than one invented number would. The heatmap tint on "Urgent" is the one visual
 * shortcut taken, and it's a plain ratio (urgent vehicles ÷ fleet size), not a hidden formula.
 */
export function DepotRisk() {
  const [depots, setDepots] = useState<DepotRiskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [regionFilter, setRegionFilter] = useState<string>(REGION_FILTER_ALL);
  const [overdueOnly, setOverdueOnly] = useState(false);

  useEffect(() => {
    let stale = false;
    api
      .depotRisk()
      .then((d) => {
        if (!stale) setDepots(d);
      })
      .catch((e: ApiError) => {
        if (stale) return;
        if (e.status === 401) setGated(true);
        else setError(e.message);
      });
    return () => {
      stale = true;
    };
  }, []);

  // Hooks run every render regardless of loading state, so filtering/sorting is computed here
  // (over `depots ?? []`) rather than after the early returns below.
  const regionOptions = useMemo(
    () => Array.from(new Set((depots ?? []).map((d) => d.region))).sort(),
    [depots],
  );
  const filtered = useMemo(() => {
    return (depots ?? []).filter((d) => {
      if (regionFilter !== REGION_FILTER_ALL && d.region !== regionFilter) return false;
      if (overdueOnly && d.overdue_work_orders === 0) return false;
      return true;
    });
  }, [depots, regionFilter, overdueOnly]);
    const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    filtered,
    (d) => `${d.depot_id} ${d.depot_name} ${d.region} ${d.city} ${d.state}`,
  );
const { sorted, sortKey, sortDir, toggleSort } = useSort<DepotRiskRow, SortKey>(
    searched,
    (d, key) => d[key],
  );

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Depot risk is read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  if (!depots && error) return <div className="error">{error}</div>;
  if (!depots)
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

  const totalUrgent = depots.reduce((n, d) => n + d.urgent_vehicles_exposed, 0);
  const totalOverdue = depots.reduce((n, d) => n + d.overdue_work_orders, 0);
  const highRiskCount = depots.filter((d) => riskTier(d) === "high").length;

  return (
    <>
      <div className="page-head">
        <h2>Depot risk</h2>
        <p>
          All {depots.length} depots, ranked by vehicles currently exposed to a Park It or Do
          Not Drive campaign — the same consequence-before-volume ranking the Recall queue
          uses, rolled up per depot.
        </p>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="v">{depots.length}</div>
          <div className="k">Depots</div>
        </div>
        <div className={highRiskCount > 0 ? "stat is-danger" : "stat is-ok"}>
          <div className="v">{highRiskCount}</div>
          <div className="k">High risk (&ge;5% of fleet urgent)</div>
          {depots.length > 0 && (
            <div className="stat-bar" title={`${highRiskCount} of ${depots.length} depots`}>
              <div
                className="stat-bar-fill"
                style={{ width: `${(highRiskCount / depots.length) * 100}%` }}
              />
            </div>
          )}
        </div>
        <div className="stat">
          <div className="v">{totalUrgent.toLocaleString()}</div>
          <div className="k">Urgent vehicles, fleet-wide</div>
        </div>
        <div className={totalOverdue > 0 ? "stat is-danger" : "stat is-ok"}>
          <div className="v">{totalOverdue.toLocaleString()}</div>
          <div className="k">Overdue work orders, fleet-wide</div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {depots.length === 0 ? (
        <div className="panel">
          No depot data loaded yet. That is a result, not an error — the fleet registry has not
          been populated on this deployment.
        </div>
      ) : (
        <>
          <div className="filter-row">
            <SearchBox
              query={query}
              onChange={setQuery}
              placeholder="Search depot, city or region…"
              matched={sorted.length}
              total={filtered.length}
              label="Search depots"
            />
            <select value={regionFilter} onChange={(e) => setRegionFilter(e.target.value)}>
              <option value={REGION_FILTER_ALL}>All regions</option>
              {regionOptions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <label className="filter-checkbox">
              <input
                type="checkbox"
                checked={overdueOnly}
                onChange={(e) => setOverdueOnly(e.target.checked)}
              />
              Overdue work orders only
            </label>
          </div>

          {sorted.length === 0 ? (
            <div className="panel">No depots match the current search or filters.</div>
          ) : (
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th>Depot</th>
                    <th>Region</th>
                    <th className="num sortable" onClick={() => toggleSort("fleet_size")}>
                      Fleet size
                      <SortIndicator columnKey="fleet_size" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th
                      className="num sortable"
                      onClick={() => toggleSort("urgent_vehicles_exposed")}
                    >
                      Urgent
                      <SortIndicator
                        columnKey="urgent_vehicles_exposed"
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </th>
                    <th
                      className="num sortable"
                      onClick={() => toggleSort("total_vehicles_exposed")}
                    >
                      Total exposed
                      <SortIndicator
                        columnKey="total_vehicles_exposed"
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </th>
                    <th className="num sortable" onClick={() => toggleSort("distinct_campaigns")}>
                      Campaigns
                      <SortIndicator
                        columnKey="distinct_campaigns"
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </th>
                    <th
                      className="num sortable"
                      onClick={() => toggleSort("outstanding_work_orders")}
                    >
                      Outstanding WOs
                      <SortIndicator
                        columnKey="outstanding_work_orders"
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </th>
                    <th className="num sortable" onClick={() => toggleSort("overdue_work_orders")}>
                      Overdue WOs
                      <SortIndicator
                        columnKey="overdue_work_orders"
                        sortKey={sortKey}
                        sortDir={sortDir}
                      />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((d) => {
                    const tier = riskTier(d);
                    const pct = d.fleet_size > 0 ? (d.urgent_vehicles_exposed / d.fleet_size) * 100 : 0;
                    return (
                      <tr key={d.depot_id}>
                        <td>
                          <strong>{d.depot_name}</strong>
                          <div className="muted" style={{ fontSize: 11 }}>
                            {d.depot_id} · {d.city}, {d.state}
                          </div>
                        </td>
                        <td className="muted">{d.region}</td>
                        <td className="num">{d.fleet_size.toLocaleString()}</td>
                        <td className={`num risk-cell risk-${tier}`}>
                          {d.urgent_vehicles_exposed.toLocaleString()}
                          {d.urgent_vehicles_exposed > 0 && (
                            <span className="muted"> ({pct.toFixed(1)}%)</span>
                          )}
                        </td>
                        <td className="num">{d.total_vehicles_exposed.toLocaleString()}</td>
                        <td className="num">{d.distinct_campaigns.toLocaleString()}</td>
                        <td className="num">{d.outstanding_work_orders.toLocaleString()}</td>
                        <td className={d.overdue_work_orders > 0 ? "num risk-cell risk-high" : "num"}>
                          {d.overdue_work_orders.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
