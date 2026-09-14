import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type ServiceCampaign, type Technician, type WorkOrder } from "../lib/api";
import { isOverdue } from "../lib/dates";
import { Pager, usePagination } from "../lib/pagination";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";

const STATUSES = ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"] as const;
// The KPI tiles above this table already read "IN PROGRESS" in sentence case; the <select>
// below them read the raw DB enum "IN_PROGRESS" — two labels for the same state, 200px apart.
// Found in a UI/UX review, 2026-09-14.
const STATUS_LABELS: Record<(typeof STATUSES)[number], string> = {
  OPEN: "Open",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};
const UNASSIGNED = "" as const; // <select> has no null value, so "" stands in for it locally
const STATUS_FILTER_ALL = "ALL" as const;
const DEPOT_FILTER_ALL = "ALL" as const;
const CAMPAIGN_FILTER_ALL = "ALL" as const;
// Matches the backend's own max (`Query(100, ge=1, le=500)`) — if a query returns exactly
// this many rows, there may be more the console isn't showing, and it says so rather than
// silently implying that's the whole list.
const WORK_ORDER_FETCH_LIMIT = 500;

type SortKey = "wo_id" | "vin" | "depot_id" | "due_date" | "status" | "actual_cost";

// `isOverdue` lives in lib/dates.ts, not here: it was wrong once (a UTC-vs-local parsing
// mismatch that flagged everything due *today* as overdue for any viewer behind UTC), and this
// project's rule is that logic which has already failed gets extracted where it can be tested.

/**
 * Work orders — what happens after "approve," which until now nothing showed.
 *
 * `approval.py` creates one row per exposed vehicle when a service campaign launches; this is
 * the first view that reads them back. Status changes go through the same approver allowlist
 * as launching a campaign — marking a safety recall "completed" when it wasn't is a real
 * compliance risk, not a casual edit — so a 403 here is expected for a signed-in but
 * unauthorised identity, and is shown inline rather than hidden client-side (the backend is
 * the actual enforcement point, same precedent as the Approve button in Campaign.tsx).
 */
export function WorkOrders({
  serviceCampaignId,
  onClearFilter,
}: {
  serviceCampaignId?: string;
  onClearFilter?: () => void;
}) {
  const [orders, setOrders] = useState<WorkOrder[] | null>(null);
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [campaigns, setCampaigns] = useState<ServiceCampaign[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>(STATUS_FILTER_ALL);
  const [depotFilter, setDepotFilter] = useState<string>(DEPOT_FILTER_ALL);
  // Seeded from the URL-driven prop (arriving via a "Launched" row click) but editable from
  // here too — previously the only way to scope to one campaign was that click-through, and
  // navigating to this tab directly via the nav bar had no way to narrow it at all.
  const [campaignFilter, setCampaignFilter] = useState<string>(
    serviceCampaignId ?? CAMPAIGN_FILTER_ALL,
  );
  const [overdueOnly, setOverdueOnly] = useState(false);
  // Local draft text per row while typing, committed on blur - a PATCH on every keystroke
  // would be both wasteful and would thrash the audit log with one row per digit.
  const [costDrafts, setCostDrafts] = useState<Record<string, string>>({});

  // A fresh URL-driven filter (a new row click) overrides whatever was picked manually here.
  useEffect(() => {
    setCampaignFilter(serviceCampaignId ?? CAMPAIGN_FILTER_ALL);
  }, [serviceCampaignId]);

  useEffect(() => {
    let stale = false;
    setOrders(null);
    api
      .workOrders({
        serviceCampaignId:
          campaignFilter !== CAMPAIGN_FILTER_ALL ? campaignFilter : undefined,
        limit: WORK_ORDER_FETCH_LIMIT,
      })
      .then((d) => {
        if (!stale) setOrders(d);
      })
      .catch((e: ApiError) => {
        if (stale) return;
        if (e.status === 401) setGated(true);
        else setError(e.message);
      });
    return () => {
      stale = true;
    };
  }, [campaignFilter]);

  useEffect(() => {
    let stale = false;
    // Fetched once, unscoped - filtered per row by depot_id client-side (§ below). A depot
    // roster is small (~2 people) and shared across every row at that depot, so this is one
    // request instead of one per distinct depot on the page.
    api
      .technicians()
      .then((t) => {
        if (!stale) setTechnicians(t);
      })
      .catch(() => {
        /* the roster is an enhancement to the assignment control, not required to read or
           change status - a failure here shouldn't block the rest of the page. */
      });
    return () => {
      stale = true;
    };
  }, []);

  useEffect(() => {
    let stale = false;
    // Populates the campaign dropdown below. A failure here degrades to "no dropdown
    // options" rather than blocking the page — the same tolerance as the technician roster.
    api
      .serviceCampaigns(500)
      .then((c) => {
        if (!stale) setCampaigns(c);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, []);

  async function changeStatus(wo: WorkOrder, newStatus: string) {
    setUpdating(wo.wo_id);
    setError(null);
    try {
      const updated = await api.updateWorkOrder(wo.wo_id, { status: newStatus });
      setOrders((prev) => prev?.map((o) => (o.wo_id === updated.wo_id ? updated : o)) ?? prev);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setUpdating(null);
    }
  }

  async function changeAssignment(wo: WorkOrder, technicianId: string) {
    setUpdating(wo.wo_id);
    setError(null);
    try {
      // technicianId === UNASSIGNED ("") means the "— Unassigned —" option was picked - send
      // an explicit null, not an omitted field, so the backend records a real unassign rather
      // than silently doing nothing (see the model_fields_set distinction in api.ts).
      const body = { assigned_to: technicianId === UNASSIGNED ? null : technicianId };
      const updated = await api.updateWorkOrder(wo.wo_id, body);
      setOrders((prev) => prev?.map((o) => (o.wo_id === updated.wo_id ? updated : o)) ?? prev);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setUpdating(null);
    }
  }

  async function commitCost(wo: WorkOrder) {
    const draft = costDrafts[wo.wo_id];
    if (draft === undefined) return; // never touched - nothing to commit
    const trimmed = draft.trim();
    const parsed = trimmed === "" ? null : Number(trimmed);
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setError(`"${draft}" is not a valid cost`);
      return;
    }
    if (parsed === (wo.actual_cost ?? null)) return; // unchanged - no PATCH needed
    setUpdating(wo.wo_id);
    setError(null);
    try {
      const updated = await api.updateWorkOrder(wo.wo_id, { actual_cost: parsed });
      setOrders((prev) => prev?.map((o) => (o.wo_id === updated.wo_id ? updated : o)) ?? prev);
      setCostDrafts((prev) => {
        const next = { ...prev };
        delete next[wo.wo_id];
        return next;
      });
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setUpdating(null);
    }
  }

  // Hooks run every render regardless of loading state, so filtering/sorting is computed here
  // (over `orders ?? []`) rather than after the early returns below.
  const depotOptions = useMemo(
    () => Array.from(new Set((orders ?? []).map((o) => o.depot_id))).sort(),
    [orders],
  );
  const filtered = useMemo(() => {
    return (orders ?? []).filter((o) => {
      if (statusFilter !== STATUS_FILTER_ALL && o.status !== statusFilter) return false;
      if (depotFilter !== DEPOT_FILTER_ALL && o.depot_id !== depotFilter) return false;
      if (overdueOnly && !isOverdue(o)) return false;
      return true;
    });
  }, [orders, statusFilter, depotFilter, overdueOnly]);
    const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    filtered,
    (w) => `${w.wo_id} ${w.vin} ${w.depot_id} ${w.assigned_to_name ?? ""} ${w.status} ${w.service_campaign_id ?? ""}`,
  );
const { sorted, sortKey, sortDir, toggleSort } = useSort<WorkOrder, SortKey>(
    searched,
    (o, key) => o[key] ?? "",
  );
  const { page, setPage, pageCount, pageRows, pageSize, totalRows } = usePagination(sorted);

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Work orders are read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  const pageHead = (
    <div className="page-head">
      <h2>Work orders</h2>
      <p>
        Created one per exposed vehicle when a service campaign launches. Status changes here
        are the record of whether the vehicle actually got fixed, not just dispatched.
      </p>
    </div>
  );

  if (!orders && error)
    return (
      <>
        {pageHead}
        <PageError title="Work orders could not be loaded." detail={error} />
      </>
    );
  if (!orders)
    return (
      <>
        <div className="stats">
          {[0, 1, 2, 3].map((i) => (
            <div className="stat" key={i}>
              <div className="skeleton tall" style={{ marginBottom: 0 }} />
            </div>
          ))}
        </div>
        <div className="skeleton wide tall" />
      </>
    );

  const counts = {
    open: orders.filter((o) => o.status === "OPEN").length,
    inProgress: orders.filter((o) => o.status === "IN_PROGRESS").length,
    completed: orders.filter((o) => o.status === "COMPLETED").length,
    // Wrapped, not point-free: `filter(isOverdue)` would pass the array **index** into
    // `isOverdue`'s optional `today` parameter, comparing a date string to a number for every
    // row after the first. TypeScript rejects it, which is the only reason it was not shipped.
    overdue: orders.filter((o) => isOverdue(o)).length,
  };

  return (
    <>
      {pageHead}

      {campaignFilter !== CAMPAIGN_FILTER_ALL && (
        <div className="filter-banner">
          <span>
            Showing <strong>{campaignFilter}</strong> only.
          </span>
          <button
            className="filter-clear"
            onClick={() => {
              setCampaignFilter(CAMPAIGN_FILTER_ALL);
              onClearFilter?.();
            }}
          >
            Clear filter
          </button>
        </div>
      )}

      <div className="stats">
        <div className="stat">
          <div className="v">{counts.open}</div>
          <div className="k">Open</div>
        </div>
        <div className="stat">
          <div className="v">{counts.inProgress}</div>
          <div className="k">In progress</div>
        </div>
        <div className="stat is-ok">
          <div className="v">{counts.completed}</div>
          <div className="k">Completed</div>
        </div>
        <div className={counts.overdue > 0 ? "stat is-danger" : "stat"}>
          <div className="v">{counts.overdue}</div>
          <div className="k">Overdue</div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {orders.length === WORK_ORDER_FETCH_LIMIT && (
        <div className="panel muted" style={{ marginBottom: 12 }}>
          Showing the {WORK_ORDER_FETCH_LIMIT} most recently created work orders — there may be
          more. Use the campaign filter to narrow this to an exact count.
        </div>
      )}

      {orders.length === 0 ? (
        <div className="panel">
          No work orders yet. That is a result, not an error — none have been created by an
          approved campaign so far.
        </div>
      ) : (
        <>
          <div className="filter-row">
            <SearchBox
              query={query}
              onChange={setQuery}
              placeholder="Search work order, VIN, depot, technician…"
              matched={sorted.length}
              total={filtered.length}
              label="Search work orders"
            />
            <select
              value={campaignFilter}
              onChange={(e) => setCampaignFilter(e.target.value)}
            >
              <option value={CAMPAIGN_FILTER_ALL}>All campaigns</option>
              {campaigns.map((c) => (
                <option key={c.service_campaign_id} value={c.service_campaign_id}>
                  {c.service_campaign_id} — {c.title}
                </option>
              ))}
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value={STATUS_FILTER_ALL}>All statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select value={depotFilter} onChange={(e) => setDepotFilter(e.target.value)}>
              <option value={DEPOT_FILTER_ALL}>All depots</option>
              {depotOptions.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <label className="filter-checkbox">
              <input
                type="checkbox"
                checked={overdueOnly}
                onChange={(e) => setOverdueOnly(e.target.checked)}
              />
              Overdue only
            </label>
          </div>

          {sorted.length === 0 ? (
            <div className="panel">No work orders match the current search or filters.</div>
          ) : (
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th className="sortable" onClick={() => toggleSort("wo_id")}>
                      Work order<SortIndicator columnKey="wo_id" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th>Campaign</th>
                    <th className="sortable" onClick={() => toggleSort("vin")}>
                      VIN<SortIndicator columnKey="vin" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th className="sortable" onClick={() => toggleSort("depot_id")}>
                      Depot<SortIndicator columnKey="depot_id" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th className="sortable" onClick={() => toggleSort("due_date")}>
                      Due<SortIndicator columnKey="due_date" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th>Completed</th>
                    <th>Assigned to</th>
                    <th className="sortable" onClick={() => toggleSort("status")}>
                      Status<SortIndicator columnKey="status" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th className="num sortable" onClick={() => toggleSort("actual_cost")}>
                      Cost ($)<SortIndicator columnKey="actual_cost" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((o) => (
                    <tr key={o.wo_id}>
                      <td className="muted">{o.wo_id}</td>
                      <td>{o.service_campaign_id ?? "—"}</td>
                      <td>{o.vin}</td>
                      {/* nowrap so a short id like "DEP-001" can't be squeezed onto two
                          lines by the wider Assigned-to/Status select columns next to it —
                          the table already scrolls horizontally (.wrap) if it needs to. */}
                      <td style={{ whiteSpace: "nowrap" }}>{o.depot_id}</td>
                      <td className="muted">
                        {o.due_date ?? "—"}
                        {isOverdue(o) && (
                          <span className="tag parkit" style={{ marginLeft: 8 }}>
                            OVERDUE
                          </span>
                        )}
                      </td>
                      <td className="muted">
                        {o.completed_at ? o.completed_at.slice(0, 10) : "—"}
                      </td>
                      <td>
                        {/* min-width so the selected technician's name doesn't clip inside a
                            table column the browser would otherwise compress — measured
                            "Michelle Sanch", "Barbara Sanch" in a UI/UX review, 2026-09-14. */}
                        <select
                          value={o.assigned_to ?? UNASSIGNED}
                          disabled={updating === o.wo_id}
                          onChange={(e) => changeAssignment(o, e.target.value)}
                          style={{ minWidth: 160 }}
                        >
                          <option value={UNASSIGNED}>— Unassigned —</option>
                          {technicians
                            .filter((t) => t.depot_id === o.depot_id)
                            .map((t) => (
                              <option key={t.technician_id} value={t.technician_id}>
                                {t.name}
                              </option>
                            ))}
                        </select>
                      </td>
                      <td>
                        {/* Same clipping issue, plus the raw enum ("IN_PROGRESS") read
                            differently from the sentence-case KPI tiles above this table —
                            both found in the same review. */}
                        <select
                          value={o.status}
                          disabled={updating === o.wo_id}
                          onChange={(e) => changeStatus(o, e.target.value)}
                          style={{ minWidth: 130 }}
                        >
                          {STATUSES.map((s) => (
                            <option key={s} value={s}>
                              {STATUS_LABELS[s]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="num">
                        {/* type="text" + inputMode="decimal" instead of type="number" — the
                            latter formats its displayed value against the browser/OS locale,
                            rendering "1039.66" as "1039,66" in some locales while the same
                            underlying value shows "$1,039.66" on the Launched tab. Display
                            formatting is the app's decision, not the input element's; parsing
                            still happens in commitCost, unchanged. Found in a UI/UX review,
                            2026-09-14. */}
                        <input
                          type="text"
                          inputMode="decimal"
                          placeholder="not logged"
                          disabled={updating === o.wo_id}
                          value={costDrafts[o.wo_id] ?? (o.actual_cost ?? "")}
                          onChange={(e) =>
                            setCostDrafts((prev) => ({ ...prev, [o.wo_id]: e.target.value }))
                          }
                          onBlur={() => commitCost(o)}
                          style={{ minWidth: 90 }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager page={page} pageCount={pageCount} onChange={setPage} totalRows={totalRows} pageSize={pageSize} />
            </div>
          )}
        </>
      )}
    </>
  );
}
