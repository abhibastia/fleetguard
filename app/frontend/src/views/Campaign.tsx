import { useEffect, useState } from "react";
import { api, ApiError, type ApprovalResult } from "../lib/api";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { useFetch } from "../lib/useFetch";
import { ApprovalConfirmation } from "./ApprovalConfirmation";

/**
 * Campaign detail and the approval gate.
 *
 * The approval is the one irreversible action in the console: it writes a service campaign,
 * one work order per exposed vehicle, and an audit row — in a single transaction. So the
 * button states the exact count it is about to create, and the approver is taken from the
 * authenticated session by the backend, never sent from here.
 */
export function Campaign({ id, onBack }: { id: string; onBack: () => void }) {
  const [title, setTitle] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApprovalResult | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  // Every other view in this console distinguishes "not signed in" from "actually broken" —
  // Campaign.tsx never did, so a 401 here (reachable straight from the Queue row click)
  // rendered the same bare error bar as a real backend failure. Found in a UI/UX review,
  // 2026-09-14. `useFetch` gives this to every view uniformly now.
  const {
    data: c,
    error,
    gated,
  } = useFetch(
    () => api.campaign(id),
    [id],
    (d) => setTitle(`${d.park_it ? "Park It — " : ""}${d.component ?? id} remediation`),
  );

  useEffect(() => {
    // Reset the *approval* state on every id change, separately from useFetch's own reset of
    // `c`/`error`/`gated` above. This component stays mounted across an id change — reachable
    // via browser back/forward, or a pasted link, while already viewing a campaign — and
    // without this, approving campaign A and then navigating directly to campaign B would show
    // B's fresh exposure data underneath A's stale "approved" success panel. Found in the
    // 2026-09-02 review by tracing what happens on a *second* id, not just verifying the first
    // one worked.
    setResult(null);
    setBusy(false);
    setRationale("");
    setApproveError(null);
  }, [id]);

  async function approve() {
    setBusy(true);
    setApproveError(null);
    try {
      setResult(
        await api.approve(id, {
          title,
          rationale,
          due_in_days: c?.park_it ? 7 : 30,
        }),
      );
    } catch (e) {
      setApproveError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  // Computed BEFORE the early returns below, because `useSearch` is a hook: placing it after
  // `if (!c) return <skeleton/>` means it is skipped on the loading render and called on the
  // next one, which is React error #310. Caught in a browser on 2026-09-10 — typecheck and the
  // unit suite were both blind to it, since neither renders the component through a state
  // transition.
  const depots = c ? Object.entries(c.by_depot).sort((a, b) => b[1] - a[1]) : [];

  // The table shows the top 12 by exposure, so the other 48 depots were unreachable —
  // "+ 48 further depots" told you they existed and gave you no way to look at them. A depot
  // manager's first question is "is MY depot on this list", so a search shows every match
  // rather than the truncated head.
  const {
    query,
    setQuery,
    filtered: matchedDepots,
  } = useSearch(depots, ([d]) => d);
  const searching = query.trim() !== "";
  const shownDepots = searching ? matchedDepots : depots.slice(0, 12);

  if (gated && !c)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Campaign detail is read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab needs no session.
        </p>
      </div>
    );
  if (error && !c) return <PageError title="Campaign detail could not be loaded." detail={error} />;
  if (!c)
    return (
      <>
        <div className="skeleton half tall" />
        <div className="skeleton wide" />
        <div className="skeleton wide tall" />
      </>
    );


  return (
    <>
      <button className="crumb" onClick={onBack}>
        ← Queue
      </button>

      <div className="row">
        <div className="grow">
          <div className="page-head">
            <h2>
              {c.campaign_id} {c.park_it && <span className="tag parkit">PARK IT</span>}{" "}
              {c.service_campaign_id && <span className="tag launched">LAUNCHED</span>}
            </h2>
            <p>{c.component ?? "—"}</p>
          </div>

          {c.consequence && (
            <div className={c.park_it ? "error" : "panel"} style={{ marginBottom: 16 }}>
              <strong>Consequence.</strong> {c.consequence}
            </div>
          )}
          {c.remedy && (
            <p>
              <strong>Remedy.</strong> <span className="muted">{c.remedy}</span>
            </p>
          )}

          <h3>
            Exposure — {c.vehicles_exposed.toLocaleString()} vehicles across {depots.length} depots
          </h3>
          <SearchBox
            query={query}
            onChange={setQuery}
            placeholder="Find a depot…"
            matched={matchedDepots.length}
            total={depots.length}
            label="Search depots for this campaign"
          />
          <div className="wrap">
            <table>
              <thead>
                <tr>
                  <th>Depot</th>
                  <th className="num">Vehicles</th>
                </tr>
              </thead>
              <tbody>
                {shownDepots.map(([d, n]) => (
                  <tr key={d}>
                    <td>{d}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {searching && shownDepots.length === 0 && (
            <p className="muted">No depot matches “{query}” for this campaign.</p>
          )}
          {!searching && depots.length > 12 && (
            <p className="muted">
              + {depots.length - 12} further depots — search above to find a specific one
            </p>
          )}
        </div>

        <div className="panel" style={{ width: 350, flexShrink: 0 }}>
          <h3>Launch service campaign</h3>

          {result ? (
            <ApprovalConfirmation result={result} />
          ) : c.service_campaign_id ? (
            // Showing the approve form here would only ever end in the 409 the backend's
            // one-active-campaign-per-recall uniqueness index (I-063) enforces — this is the
            // same information, told before the click instead of after it.
            <div className="panel" style={{ margin: 0 }}>
              <p style={{ marginTop: 0 }}>
                <strong>Already launched</strong> as{" "}
                <code>{c.service_campaign_id}</code>.
              </p>
              <p className="muted" style={{ marginBottom: 0, fontSize: 13 }}>
                Approving again would be rejected — one recall can have only one active service
                campaign at a time.
              </p>
              <a
                className="crumb"
                style={{ display: "inline-block", marginTop: 12 }}
                href={`#/work-orders/${encodeURIComponent(c.service_campaign_id)}`}
              >
                View its work orders →
              </a>
            </div>
          ) : (
            <>
              <label htmlFor="t">Title — written into the audit log permanently</label>
              {/* A single-line <input> clipped its own generated value ("...RACK AND PINION
                  reme|") in this 350px sidebar — measured scrollWidth 355 vs clientWidth 310.
                  A textarea wraps instead of scrolling sideways, so the exact string an
                  approver is about to commit stays fully visible. Found in a UI/UX review,
                  2026-09-14. */}
              <textarea
                id="t"
                rows={2}
                value={title}
                onChange={(e) => setTitle(e.target.value.replace(/\n/g, " "))}
              />

              <label htmlFor="r">Rationale — recorded in the audit log</label>
              <textarea
                id="r"
                rows={4}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Why this campaign is being launched now"
              />

              {approveError && (
                <div className="error" style={{ marginTop: 12 }}>
                  {approveError}
                </div>
              )}

              <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
                Creates <strong>{c.vehicles_exposed.toLocaleString()}</strong> work orders in one
                transaction. This cannot be undone from the console.
              </p>
              <button
                className={c.park_it ? "danger" : "primary"}
                disabled={busy || rationale.trim().length < 3 || title.trim().length < 3}
                onClick={approve}
              >
                {busy
                  ? "Launching…"
                  : `Approve — ${c.vehicles_exposed.toLocaleString()} work orders`}
              </button>
              {!busy && (rationale.trim().length < 3 || title.trim().length < 3) && (
                <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  {title.trim().length < 3
                    ? "Add a title to enable Approve."
                    : "Add a rationale (min. 3 characters) to enable Approve."}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
