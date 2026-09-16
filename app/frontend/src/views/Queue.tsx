import { useMemo, useState } from "react";
import { api, type QueueItem } from "../lib/api";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";
import { useFetch } from "../lib/useFetch";

const SEVERITY_FILTER_ALL = "ALL" as const;
const SEVERITY_FILTER_URGENT = "URGENT" as const;

type SortKey = "vehicles_exposed" | "depots_affected";

/**
 * The work queue — the operator's landing surface.
 *
 * Ordering comes from the backend and is deliberately **consequence before volume**: a
 * 25-vehicle do-not-drive defect outranks a 1,801-vehicle label recall. That default must
 * survive untouched — `useSort` only reorders after an explicit header click, never on
 * load, so an operator sees consequence-first until they deliberately ask for something
 * else. Only `vehicles_exposed`/`depots_affected` are sortable at all; severity itself isn't,
 * since the tags already carry more information than a re-sortable rank would.
 */
export function Queue({ onOpen }: { onOpen: (id: string) => void }) {
  const { data: items, error, gated } = useFetch(() => api.queue(), []);
  const [severityFilter, setSeverityFilter] = useState<string>(SEVERITY_FILTER_ALL);

  const filtered = useMemo(() => {
    return (items ?? []).filter((i) => {
      if (severityFilter === SEVERITY_FILTER_URGENT) return i.park_it || i.do_not_drive;
      return true;
    });
  }, [items, severityFilter]);
    const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    filtered,
    (q) => `${q.campaign_id} ${q.component ?? ""} ${q.consequence ?? ""}`,
  );
const { sorted, sortKey, sortDir, toggleSort } = useSort<QueueItem, SortKey>(
    searched,
    (i, key) => i[key],
  );

  // A 401 here is expected on the public deployment. Explaining that is far better than a
  // red error a viewer would read as a broken build.
  if (gated)
    return (
      <div className="panel">
        <h3>Sign-in required</h3>
        <p className="muted">
          The operator queue reads live fleet exposure under <em>your</em> Databricks identity, so
          it needs an authenticated session. User sign-in is not configured on this deployment.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          <strong>Evidence</strong> needs no sign-in — it carries the measured early-warning result
          and its control arm.
        </p>
      </div>
    );
  const pageHead = (
    <div className="page-head">
      <h2>Recall queue</h2>
      <p>
        Open campaigns matched to fleet vehicles, ordered by consequence before volume — a
        do-not-drive defect outranks a larger label recall.
      </p>
    </div>
  );

  if (error)
    return (
      <>
        {pageHead}
        <PageError title="Recall queue could not be loaded." detail={error} />
      </>
    );

  if (!items)
    return (
      <>
        <div className="stats">
          <div className="stat">
            <div className="skeleton tall" style={{ marginBottom: 0 }} />
          </div>
          <div className="stat">
            <div className="skeleton tall" style={{ marginBottom: 0 }} />
          </div>
        </div>
        <div className="skeleton wide tall" />
        <div className="skeleton wide" />
        <div className="skeleton half" />
      </>
    );

  // An empty queue is ambiguous — it could mean "no exposure" or "the filter is wrong".
  // Say which, rather than rendering a blank table.
  if (items.length === 0)
    return <div className="panel">No campaigns currently match any vehicle in the fleet.</div>;

  const urgent = items.filter((i) => i.park_it || i.do_not_drive).length;
  const vehicles = items.reduce((n, i) => n + i.vehicles_exposed, 0);

  return (
    <>
      {pageHead}

      <div className="stats">
        <div className={urgent > 0 ? "stat is-danger" : "stat"}>
          <div className="v">{urgent}</div>
          <div className="k">Immediate action</div>
          {items.length > 0 && (
            <div
              className="stat-bar"
              role="img"
              aria-label={`${urgent} of ${items.length} campaigns need immediate action`}
              title={`${urgent} of ${items.length} campaigns`}
            >
              <div
                className="stat-bar-fill"
                style={{ width: `${(urgent / items.length) * 100}%` }}
              />
            </div>
          )}
        </div>
        <div className="stat">
          <div className="v">{items.length}</div>
          <div className="k">Campaigns</div>
        </div>
        <div className="stat">
          <div className="v">{vehicles.toLocaleString()}</div>
          <div className="k">Vehicles exposed</div>
        </div>
      </div>

      <div className="filter-row">
            <SearchBox
              query={query}
              onChange={setQuery}
              placeholder="Search campaign or component…"
              matched={sorted.length}
              total={filtered.length}
              label="Search recall queue"
            />
        <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
          <option value={SEVERITY_FILTER_ALL}>All campaigns</option>
          <option value={SEVERITY_FILTER_URGENT}>Immediate action only</option>
        </select>
      </div>

      {sorted.length === 0 ? (
        <div className="panel">No campaigns match the current search or filter.</div>
      ) : (
        <div className="wrap">
          <table className="rows">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Component</th>
                <th>Severity</th>
                <th className="num sortable" onClick={() => toggleSort("vehicles_exposed")}>
                  Vehicles<SortIndicator columnKey="vehicles_exposed" sortKey={sortKey} sortDir={sortDir} />
                </th>
                <th className="num sortable" onClick={() => toggleSort("depots_affected")}>
                  Depots<SortIndicator columnKey="depots_affected" sortKey={sortKey} sortDir={sortDir} />
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((i) => (
                <tr key={i.campaign_id} onClick={() => onOpen(i.campaign_id)}>
                  <td>
                    <strong>{i.campaign_id}</strong>
                    {i.service_campaign_id && (
                      <span
                        className="tag launched"
                        style={{ marginLeft: 8 }}
                        title={`Already launched as ${i.service_campaign_id}`}
                      >
                        LAUNCHED
                      </span>
                    )}
                  </td>
                  <td className="muted">{i.component ?? "—"}</td>
                  <td>
                    {i.park_it && <span className="tag parkit">PARK IT</span>}
                    {!i.park_it && i.do_not_drive && (
                      <span className="tag dnd">DO NOT DRIVE</span>
                    )}
                    {/* A bare dash here would sit next to two rows with a real pill and read
                        as missing data rather than "checked, no elevated severity" — the
                        third real state gets the same badge shape, just neutral, same
                        pattern .tag.quiet already uses on the Emerging tab. */}
                    {!i.park_it && !i.do_not_drive && (
                      <span className="tag quiet">STANDARD</span>
                    )}
                  </td>
                  <td className="num">{i.vehicles_exposed.toLocaleString()}</td>
                  <td className="num">{i.depots_affected}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
