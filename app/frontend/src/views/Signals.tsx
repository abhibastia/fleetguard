import { useEffect, useState } from "react";
import { api, ApiError, type SignalSummary } from "../lib/api";

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
  if (error) return <div className="error">{error}</div>;
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
      <div className="page-head">
        <h2>Emerging defects</h2>
        <p>
          Series with a sustained complaint anomaly —{" "}
          <strong>z ≥ 3.0 for ≥2 consecutive months</strong> against their own trailing year — that
          NHTSA has <em>not</em> recalled. The same detector the Evidence tab measures.
        </p>
      </div>

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
        <div className="stat">
          <div className="v" style={{ fontSize: 17, paddingTop: 5 }}>
            {data.as_of_month ?? "—"}
          </div>
          <div className="k">Data through</div>
        </div>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={fleetOnly}
          onChange={(e) => setFleetOnly(e.target.checked)}
        />
        Only signals touching fleet vehicles
      </label>

      {data.signals.length === 0 ? (
        <div className="panel">
          No signals match this filter. That is a result, not an error — the detector ran and found
          nothing.
        </div>
      ) : (
        <div className="wrap">
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
              {data.signals.map((s) => (
                <tr key={s.signal_id}>
                  <td>
                    <strong>
                      {s.make} {s.model}
                    </strong>
                    {s.is_live ? (
                      <span className="tag live" style={{ marginLeft: 8 }}>
                        LIVE
                      </span>
                    ) : (
                      <span className="tag quiet" style={{ marginLeft: 8 }}>
                        QUIET
                      </span>
                    )}
                  </td>
                  <td className="muted">{s.component}</td>
                  <td className="num">{s.max_z?.toFixed(1)}</td>
                  <td className="num">{s.complaint_count}</td>
                  {/* Harm share is triage context. It plays no part in whether a signal fires —
                    the detector is pure volume anomaly (I-051) — so it must never be styled
                    as if it were a score. */}
                  <td className="num muted">
                    {s.harm_share != null ? `${Math.round(s.harm_share * 100)}%` : "—"}
                  </td>
                  <td className="num">
                    {s.fleet_vehicles > 0 ? (
                      <strong>{s.fleet_vehicles.toLocaleString()}</strong>
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
