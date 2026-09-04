import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

/**
 * Client-side sort for an already-fetched table. Rows are small (dozens to low hundreds per
 * table today), so re-sorting a copy in memory on every click is simpler and fast enough —
 * no reason to round-trip the server for this.
 */
export function useSort<T, K extends string>(rows: T[], getValue: (row: T, key: K) => string | number | null) {
  const [sortKey, setSortKey] = useState<K | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const sorted = useMemo(() => {
    if (!sortKey) return rows; // no explicit sort yet - preserve the order the backend sent
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = getValue(a, sortKey);
      const bv = getValue(b, sortKey);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const cmp = av < bv ? -1 : 1;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortDir, getValue]);

  function toggleSort(key: K) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  return { sorted, sortKey, sortDir, toggleSort };
}

/**
 * Always-visible sort affordance for a column header — a dimmed double-arrow when the column
 * is sortable but not the active sort, a solid directional arrow when it is. Discoverability
 * matters here: a header that only reveals it's sortable on hover or after the first click is
 * a control the user has to stumble into, not one they can see up front.
 */
export function SortIndicator<K extends string>({
  columnKey,
  sortKey,
  sortDir,
}: {
  columnKey: K;
  sortKey: K | null;
  sortDir: SortDir;
}) {
  const active = columnKey === sortKey;
  return (
    <span className={`sort-indicator${active ? " is-active" : ""}`}>
      {active ? (sortDir === "asc" ? "▲" : "▼") : "⇅"}
    </span>
  );
}
