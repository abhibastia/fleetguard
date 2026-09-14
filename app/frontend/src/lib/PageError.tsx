/**
 * The initial-load failure state, shared across every view that fetches on mount.
 *
 * Before this existed, a failed first load replaced the *entire* view with a bare
 * `<div className="error">{message}</div>` — no title, no page-head, nothing to tell an
 * operator which surface broke or what to do about it. Found in a UI/UX review
 * (2026-09-14): eight views collapsed to a raw backend `detail` string on any 500, with the
 * tab header the only surviving clue about what page they were even looking at.
 *
 * `retryLabel` defaults to a full reload rather than a proper re-fetch: every affected view
 * fetches once in a `useEffect` on mount with no re-fetch trigger, and hash-based routing
 * (see `App.tsx`) survives a reload, so this is the smallest fix that actually gets a fresh
 * request without plumbing a retry callback through eight components.
 */
export function PageError({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p className="muted">
        Try again — if it keeps happening, the detail below is worth reporting.
      </p>
      <p className="footnote">{detail}</p>
      <button className="linklike" onClick={() => window.location.reload()}>
        Retry →
      </button>
    </div>
  );
}
