import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type SignalSummary } from "../lib/api";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";

/**
 * Emerging defect signals — the proactive half, and the half the project's argument rests on.
 *
 * The queue shows recalls that already exist. This shows ramps that NHTSA has not acted on
 * yet, from the same detector the backtest measured (16.0% vs 11.1% placebo, 1.44×).
 *
 * The headline is deliberately two numbers, not one: "N emerging across NHTSA, M affecting
 * your fleet". Either alone misleads — the first is not actionable, and the second, shown by
 * itself, makes a quiet week indistinguishable from a broken query.
 */
export function Signals() {
  const [data, setData] = useState<SignalSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [fleetOnly, setFleetOnly] = useState(false);

  // Search over the fetched page. `fleetOnly` is a server-side query param (it refetches), so
  // this filters what came back rather than duplicating that switch client-side.
  const {
    query,
    setQuery,
    filtered: visible,
  } = useSearch(
    data?.signals ?? [],
    (s) => `${s.make ?? ""} ${s.model ?? ""} ${s.component} ${s.series_key ?? ""}`,
  );
  // The KPI tile reads "6 affecting your fleet" while the default table mixed those 6 in
  // among 44 with FLEET: 0 — a fleet-safety-team reader had to hunt for the rows the tile
  // itself said mattered. Sorted here, not left to whatever order the backend happens to
  // return, so this table can't drift out of sync with its own headline again. Found in a
  // UI/UX review, 2026-09-14.
  const ordered = useMemo(
    () => [...visible].sort((a, b) => b.fleet_vehicles - a.fleet_vehicles),
    [visible],
  );

  useEffect(() => {
    // Same guard as views/Campaign.tsx's fetch, same reason: toggling the checkbox twice
    // quickly can let the first (now-stale) response resolve after the second, silently
    // replacing the correctly-filtered view with the wrong one.
    let stale = false;
    api
      .signals(fleetOnly)
      .then((d) => {
        if (!stale) setData(d);
      })
      .catch((e: ApiError) => {
        if (stale) return;
        if (e.status === 401) setGated(true);
        else setError(e.message);
      });
    return () => {
      stale = true;
    };
  }, [fleetOnly]);

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Emerging signals are read under your Databricks identity. This public deployment has no
          sign-in — the <strong>Evidence</strong> tab shows the measured performance of this same
          detector and needs no session.
        </p>
      </div>
    );
  const pageHead = (
    <div className="page-head">
      <h2>Emerging defects</h2>
      <p>
        Series with a sustained complaint anomaly —{" "}
        <strong>z ≥ 3.0 for ≥2 consecutive months</strong> against their own trailing year — that
        NHTSA has <em>not</em> recalled. The same detector the Evidence tab measures.
      </p>
    </div>
  );

  if (error)
    return (
      <>
        {pageHead}
        <PageError title="Emerging defects could not be loaded." detail={error} />
      </>
    );
  if (!data)
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
        <div className="skeleton wide" />
      </>
    );

  return (
    <>
      {pageHead}

      <div className="stats">
        <div className={data.fleet_relevant > 0 ? "stat is-danger" : "stat"}>
          <div className="v">{data.fleet_relevant}</div>
          <div className="k">Affecting your fleet</div>
        </div>
        <div className="stat">
          <div className="v">{data.live}</div>
          <div className="k">Still firing</div>
        </div>
        <div className="stat">
          <div className="v">{data.total}</div>
          <div className="k">Detected · 12 months</div>
        </div>
      </div>
      {/* Was a 4th .stats tile — a date isn't a count, and it rendered visibly smaller than
          its neighbours, breaking the row's read as "a set of KPIs". Found in a UI/UX review,
          2026-09-14. */}
      {data.as_of_month && (
        <p className="muted" style={{ marginTop: -10, marginBottom: 16, fontSize: 12.5 }}>
          Data through {data.as_of_month}.
        </p>
      )}

      <div className="filter-row">
        <SearchBox
          query={query}
          onChange={setQuery}
          placeholder="Search make, model or component…"
          matched={visible.length}
          total={data.signals.length}
          label="Search emerging signals"
        />
        <label className="check">
          <input
            type="checkbox"
            checked={fleetOnly}
            onChange={(e) => setFleetOnly(e.target.checked)}
          />
          Only signals touching fleet vehicles
        </label>
      </div>

      {ordered.length === 0 ? (
        <div className="panel">
          No signals match the current search or filter. That is a result, not an error — the
          detector ran and found nothing.
        </div>
      ) : (
        <div className="wrap">
          {/* Four badge vocabularies (QUIET/LIVE/AGENT/VARIANT) with nothing on the page
              explaining any of them — found in a UI/UX review, 2026-09-14. Each badge below
              already carries (or now carries) a `title` tooltip; this is the always-visible
              version of the same information for anyone not hovering. */}
          <p className="muted footnote" style={{ marginBottom: 10 }}>
            <span className="tag live" style={{ marginRight: 4 }}>LIVE</span> anomaly reaches the
            latest month ·{" "}
            <span className="tag quiet" style={{ marginRight: 4 }}>QUIET</span> anomaly has
            stopped ·{" "}
            <span className="tag agent" style={{ marginRight: 4 }}>AGENT</span> opened by the
            assistant, no detector run ·{" "}
            <span className="tag quiet" style={{ marginRight: 4 }}>VARIANT</span> fleet count
            matched across a model-name spelling difference, not an exact name.
          </p>
          <table>
            <thead>
              <tr>
                <th>Series</th>
                <th>Component</th>
                <th className="num">Peak z</th>
                <th className="num">Complaints</th>
                <th className="num">Harm</th>
                <th className="num">Fleet</th>
                <th>Last fired</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((s) => (
                <tr key={s.signal_id}>
                  <td>
                    <strong>
                      {s.make} {s.model}
                    </strong>
                    {/* An agent-opened signal has no detector run behind it, so LIVE/QUIET
                        — which describe whether an anomaly run reaches the latest month —
                        would be meaningless. Badging it by origin says what it actually is;
                        rendering NULL is_live as "QUIET" would have claimed a measurement
                        that was never taken. */}
                    {s.source === "AGENT" ? (
                      <span
                        className="tag agent"
                        style={{ marginLeft: 8 }}
                        title="Opened by the assistant from complaint evidence — no detector run behind it."
                      >
                        AGENT
                      </span>
                    ) : s.is_live ? (
                      <span
                        className="tag live"
                        style={{ marginLeft: 8 }}
                        title="This anomaly run reaches the latest month of data — still firing."
                      >
                        LIVE
                      </span>
                    ) : (
                      <span
                        className="tag quiet"
                        style={{ marginLeft: 8 }}
                        title="This anomaly run ended before the latest month — no longer firing."
                      >
                        QUIET
                      </span>
                    )}
                    {/* The write path's central claim, shown where the claim is made. The
                        agent holds no database access; the console performs its write under
                        the caller's own OBO token, so this is a real person, and Postgres RLS
                        applied to that write exactly as it does to a UI click. It was already
                        recorded correctly — but only visible in the Audit log a tab away,
                        which made the strongest property of the write path effectively
                        invisible. Detector rows have no opener and render nothing. */}
                    {s.source === "AGENT" && s.opened_by && (
                      <div className="muted" style={{ fontSize: "0.78rem", marginTop: 2 }}>
                        opened by {s.opened_by}
                      </div>
                    )}
                  </td>
                  <td className="muted">{s.component}</td>
                  {/* No z-score on an agent-opened signal: it did not run the detector.
                      An em-dash reads as "not measured"; a 0 would read as "measured, and
                      found nothing". */}
                  <td className="num">
                    {s.max_z != null ? (
                      s.max_z.toFixed(1)
                    ) : (
                      <span className="muted" title="Agent-opened signals don't run the z-score detector, so there's no score to show — not a measurement of zero.">
                        —
                      </span>
                    )}
                  </td>
                  <td className="num">{s.complaint_count}</td>
                  {/* Harm share is triage context. It plays no part in whether a signal fires —
                    the detector is pure volume anomaly (I-051) — so it must never be styled
                    as if it were a score. */}
                  <td className="num muted">
                    {s.harm_share != null ? `${Math.round(s.harm_share * 100)}%` : "—"}
                  </td>
                  {/* The count carries its match tier, because the two are not equally strong.
                    A MODEL_VARIANT match joins NHTSA's spelling to vPIC's across a word
                    boundary — RAM `PROMASTER` matches 2,418 vehicles, 315 of them
                    `PROMASTER CITY`, a different class of van. Presenting that as a bare
                    number would trade I-079's wrong zero for a misleading precision, so
                    variants are labelled and exact matches are left unadorned. */}
                  <td className="num">
                    {s.fleet_vehicles > 0 ? (
                      <>
                        <strong>{s.fleet_vehicles.toLocaleString()}</strong>
                        {s.match_basis === "MODEL_VARIANT" && (
                          <span className="tag quiet" title="Matched on a model variant (e.g. PROMASTER → PROMASTER 1500), not an exact model name. Confirm before acting.">
                            VARIANT
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="muted">0</span>
                    )}
                  </td>
                  <td className="muted">{s.run_end}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="footnote" style={{ marginTop: 18 }}>
        A signal is a <em>detected anomaly</em>, not a confirmed defect and not a recall. The
        detector finds roughly one in six investigations ahead of NHTSA, against 11.1% on a matched
        control — an edge, not an oracle. Harm share is shown for triage and is not part of
        detection.
      </p>
    </>
  );
}
