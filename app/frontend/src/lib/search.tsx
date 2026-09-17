import { useMemo, useState } from "react";

/**
 * Client-side free-text filter for an already-fetched table.
 *
 * **Why this exists.** Every table in the console shipped as "all rows plus maybe a dropdown"
 * — the Audit log is 724 rows behind two selects, Emerging is 51, the campaign detail depot
 * breakdown is 60 with no control at all. A reviewer hit exactly that on 2026-09-10: *"there
 * should be search/filter option instead of all rows"*. Dropdowns answer "show me this
 * category"; they cannot answer "where is DEP-042" or "which work order touches this VIN",
 * which is what someone actually does with a table this size.
 *
 * Filters in memory for the same reason `useSort` does: the fetched page is dozens to low
 * hundreds of rows, so a round-trip per keystroke would be slower and add a loading state to a
 * control that should feel instant.
 *
 * Matching is case-insensitive substring across whatever fields the caller exposes, and terms
 * are AND-ed — typing "ford brakes" narrows rather than widens, which is what a search box
 * trains people to expect. Sort order is preserved: this composes with `useSort` rather than
 * competing with it.
 */
export function useSearch<T>(rows: T[], getSearchText: (row: T) => string) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () => filterRows(rows, getSearchText, query),
    [rows, query, getSearchText],
  );
  return { query, setQuery, filtered };
}

/**
 * The matching itself, as a plain function so it is testable without a React renderer — even
 * now that `@testing-library/react` exists in this project (added 2026-09-17 for view
 * component tests), a pure function is still simpler to pin than a hook rendered just to
 * assert on its return value. The hook above is a thin wrapper; this is where the behaviour
 * worth pinning lives.
 */
export function filterRows<T>(rows: T[], getSearchText: (row: T) => string, query: string): T[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return rows;
  return rows.filter((row) => {
    const hay = getSearchText(row).toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
}

/**
 * The search input itself, so every table gets the same affordance rather than each view
 * inventing one. `type="search"` gives the native clear button and the right on-screen
 * keyboard; the count is rendered beside it because a filter that silently hides rows is how
 * someone concludes the data is missing.
 */
export function SearchBox({
  query,
  onChange,
  placeholder,
  matched,
  total,
  label,
}: {
  query: string;
  onChange: (v: string) => void;
  placeholder: string;
  matched: number;
  total: number;
  label: string;
}) {
  return (
    <div className="search-box">
      <input
        type="search"
        className="search-input"
        value={query}
        placeholder={placeholder}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
      />
      {query.trim() !== "" && (
        <span className="search-count">
          {matched.toLocaleString()} of {total.toLocaleString()}
        </span>
      )}
    </div>
  );
}
