import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type AuditLogEntry } from "../lib/api";
import { Pager, usePagination } from "../lib/pagination";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";

const ENTITY_TYPE_FILTER_ALL = "ALL" as const;
const ACTION_FILTER_ALL = "ALL" as const;

type SortKey = "entity_type" | "action" | "created_at";

// This is a human-facing compliance record, not a debug feed — internal measurement
// artifacts (the CDF latency probe) don't belong in "every campaign launch, work-order
// status change, (re)assignment, and cost log", and their UPPER_SNAKE entity type was also
// the one thing breaking an otherwise-consistent lower_snake casing convention. Found in a
// UI/UX review, 2026-09-14.
const HIDDEN_ENTITY_TYPES = new Set(["LATENCY_PROBE"]);

/** `defect_signal` → "Defect signal" — a snake_case DB value read differently from every
 *  other label in the console. Applied to both the filter dropdowns and the table column so
 *  the two can't say the same thing two different ways. */
function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ").toLowerCase();
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** One row per changed field, `rationale` (or any field over 60 chars) pulled out from the
 *  rest — the old comma-joined single string ran a full free-text rationale straight into the
 *  next field name with no separator ("...not a confirmed defect., fleet_vehicles: — → 1256").
 *  Structured rows can't concatenate that way. Found in a UI/UX review, 2026-09-14. */
function changeFields(e: AuditLogEntry): { key: string; before: string; after: string }[] {
  const after = e.after_state ?? {};
  const before = e.before_state ?? {};
  const keys = Object.keys(after).length > 0 ? Object.keys(after) : Object.keys(before);
  return keys.map((k) => ({
    key: k,
    before: formatValue(before[k]),
    after: formatValue(after[k]),
  }));
}

/**
 * The compliance record — every campaign launch, work-order status change, (re)assignment, and
 * cost log, in one place. `fleetguard_audit_log` has recorded all of this since Phase 7; this is
 * its first consumer. CSV export exists for handing this to someone who needs a record, not a
 * live query into the console.
 */
export function AuditLog() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>(ENTITY_TYPE_FILTER_ALL);
  const [actionFilter, setActionFilter] = useState<string>(ACTION_FILTER_ALL);

  useEffect(() => {
    let stale = false;
    api
      // 1000, not the 200 default: the log is already 724 rows and a search that can only see
      // the most recent 200 is worse than no search — it returns "no matches" for an entry
      // that exists. The backend caps at 2000, so this stays bounded.
      .auditLog(1000)
      .then((d) => {
        if (!stale) setEntries(d);
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
  // (over `entries ?? []`) rather than after the early returns below.
  const visible = useMemo(
    () => (entries ?? []).filter((e) => !HIDDEN_ENTITY_TYPES.has(e.entity_type)),
    [entries],
  );
  const entityTypeOptions = useMemo(
    () => Array.from(new Set(visible.map((e) => e.entity_type))).sort(),
    [visible],
  );
  const actionOptions = useMemo(
    () => Array.from(new Set(visible.map((e) => e.action))).sort(),
    [visible],
  );
  const filtered = useMemo(() => {
    return visible.filter((e) => {
      if (entityTypeFilter !== ENTITY_TYPE_FILTER_ALL && e.entity_type !== entityTypeFilter) {
        return false;
      }
      if (actionFilter !== ACTION_FILTER_ALL && e.action !== actionFilter) return false;
      return true;
    });
  }, [visible, entityTypeFilter, actionFilter]);
  // Search runs after the dropdowns and before the sort, so the three compose: narrow by
  // category, then find within it, then order. 724 rows behind two selects was the specific
  // thing a reviewer could not navigate on 2026-09-10.
  const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    filtered,
    (e) => `${e.entity_id} ${e.action} ${e.entity_type} ${e.actor_principal} ${e.created_at}`,
  );
  const { sorted, sortKey, sortDir, toggleSort } = useSort<AuditLogEntry, SortKey>(
    searched,
    (e, key) => e[key],
  );
  const { page, setPage, pageCount, pageRows, pageSize, totalRows } = usePagination(sorted);

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          The audit log is read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  const pageHead = (
    <div className="page-head">
      <h2>Audit log</h2>
      <p>
        Every campaign launch, work-order status change, (re)assignment, and cost log —
        attributed to a real identity, not editable after the fact.
      </p>
    </div>
  );

  if (!entries && error)
    return (
      <>
        {pageHead}
        <PageError title="Audit log could not be loaded." detail={error} />
      </>
    );
  if (!entries)
    return (
      <>
        <div className="stats">
          {[0, 1].map((i) => (
            <div className="stat" key={i}>
              <div className="skeleton tall" style={{ marginBottom: 0 }} />
            </div>
          ))}
        </div>
        <div className="skeleton wide tall" />
      </>
    );

  return (
    <>
      {pageHead}

      <div className="stats">
        <div className="stat">
          <div className="v">{visible.length}</div>
          {/* Excludes LATENCY_PROBE rows — this is the compliance-record count, not a raw
              table row count. */}
          <div className="k">Entries (most recent {visible.length})</div>
        </div>
        <div className="stat">
          <div className="v">{new Set(visible.map((e) => e.actor_principal)).size}</div>
          <div className="k">
            Distinct actor{new Set(visible.map((e) => e.actor_principal)).size === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {visible.length === 0 ? (
        <div className="panel">
          No audit entries yet. That is a result, not an error — nothing has been launched,
          updated, or logged so far.
        </div>
      ) : (
        <>
          <div className="filter-row">
            <select
              value={entityTypeFilter}
              onChange={(e) => setEntityTypeFilter(e.target.value)}
            >
              <option value={ENTITY_TYPE_FILTER_ALL}>All entity types</option>
              {entityTypeOptions.map((t) => (
                <option key={t} value={t}>
                  {titleCase(t)}
                </option>
              ))}
            </select>
            <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
              <option value={ACTION_FILTER_ALL}>All actions</option>
              {actionOptions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <SearchBox
              query={query}
              onChange={setQuery}
              placeholder="Search entity, actor, action…"
              matched={sorted.length}
              total={filtered.length}
              label="Search audit entries"
            />
            <a
              className="filter-clear"
              style={{ marginLeft: "auto", textDecoration: "none" }}
              href="/api/audit-log/export.csv"
            >
              Export CSV
            </a>
          </div>

          {sorted.length === 0 ? (
            <div className="panel">No audit entries match the current search or filters.</div>
          ) : (
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th className="sortable" onClick={() => toggleSort("created_at")}>
                      When
                      <SortIndicator columnKey="created_at" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th className="sortable" onClick={() => toggleSort("entity_type")}>
                      Entity
                      <SortIndicator columnKey="entity_type" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th>ID</th>
                    <th className="sortable" onClick={() => toggleSort("action")}>
                      Action
                      <SortIndicator columnKey="action" sortKey={sortKey} sortDir={sortDir} />
                    </th>
                    <th>Actor</th>
                    <th>Change</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((e) => {
                    // `rationale` (or anything long enough to be free text rather than a
                    // field value) gets its own block below the compact key:value list —
                    // the old comma-joined string ran a 400-word rationale straight into the
                    // next field name with no separator. Found in a UI/UX review, 2026-09-14.
                    const fields = changeFields(e);
                    const compact = fields.filter((f) => f.after.length <= 60 && f.before.length <= 60);
                    const long = fields.filter((f) => f.after.length > 60 || f.before.length > 60);
                    return (
                      <tr key={e.audit_id}>
                        <td className="muted">{e.created_at.slice(0, 19).replace("T", " ")}</td>
                        <td>{titleCase(e.entity_type)}</td>
                        <td className="muted">{e.entity_id}</td>
                        <td>
                          <span className="tag quiet">{titleCase(e.action)}</span>
                        </td>
                        <td className="muted">{e.actor_principal}</td>
                        <td className="muted">
                          {fields.length === 0 ? (
                            "—"
                          ) : (
                            <>
                              {compact.map((f) => (
                                <div key={f.key}>
                                  {f.key}: {f.before} → {f.after}
                                </div>
                              ))}
                              {long.map((f) => (
                                <div key={f.key} style={{ marginTop: compact.length ? 4 : 0 }}>
                                  <strong>{f.key}:</strong> {f.after}
                                </div>
                              ))}
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
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
