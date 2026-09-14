import { useEffect, useState } from "react";

const DEFAULT_PAGE_SIZE = 50;

/**
 * Client-side pagination for an already-fetched, already-filtered-and-sorted table.
 *
 * Added after a UI/UX review (2026-09-14) found Work orders (331 rows) and Audit log (724
 * rows) rendering fully unpaginated — 22,600px and 64,700px pages with no way to jump past
 * them. Same "small enough to hold in memory" reasoning `useSort`/`useSearch` already rely
 * on: this paginates the array those hooks hand back, it doesn't re-fetch per page.
 *
 * Resets to page 1 whenever the *input array's identity* changes — `useSearch`/`useSort` both
 * memoize their output, so a reference change here means the search/filter/sort actually
 * produced a different result set, not just a re-render. Without this, changing a filter while
 * on page 3 would show an empty page instead of the new page 1.
 */
export function usePagination<T>(rows: T[], pageSize = DEFAULT_PAGE_SIZE) {
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [rows]);

  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const clampedPage = Math.min(page, pageCount);
  const start = (clampedPage - 1) * pageSize;
  const pageRows = rows.slice(start, start + pageSize);

  return { page: clampedPage, setPage, pageCount, pageRows, pageSize, totalRows: rows.length };
}

export function Pager({
  page,
  pageCount,
  onChange,
  totalRows,
  pageSize,
}: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  totalRows: number;
  pageSize: number;
}) {
  if (pageCount <= 1) return null;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalRows);
  return (
    <div className="pager">
      <span className="muted">
        {start}–{end} of {totalRows}
      </span>
      <button onClick={() => onChange(page - 1)} disabled={page <= 1}>
        ← Prev
      </button>
      <span className="muted">
        Page {page} of {pageCount}
      </span>
      <button onClick={() => onChange(page + 1)} disabled={page >= pageCount}>
        Next →
      </button>
    </div>
  );
}
