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
    api
      .signals(fleetOnly)
      .then(setData)
      .catch((e: ApiError) => (e.status === 401 ? setGated(true) : setError(e.message)));
  }, [fleetOnly]);

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          Emerging signals are read under your Databricks identity. This public deployment has
          no sign-in — the <strong>Evidence</strong> tab shows the measured performance of this
          same detector and needs no session.
        </p>
      </div>
    );
  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="muted">Running the detector…</p>;

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Emerging defects</h2>
      <p className="muted">
        Series with a sustained complaint anomaly — <strong>z ≥ 3.0 for ≥2 consecutive
        months</strong> against their own trailing year — that NHTSA has <em>not</em> recalled.
        Same detector the Evidence tab measures.
      </p>

      <p style={{ marginTop: 0 }}>
        <strong>{data.total}</strong> detected in the last 12 months ·{" "}
        <strong>{data.live}</strong> still firing ·{" "}
        <strong style={{ color: data.fleet_relevant > 0 ? "var(--danger)" : undefined }}>
          {data.fleet_relevant} affecting your fleet
        </strong>
        {data.as_of_month && <span className="muted"> · data through {data.as_of_month}</span>}
      </p>

      <label className="muted" style={{ display: "block", margin: "10px 0 16px", fontSize: 13 }}>
        <input
          type="checkbox"
          checked={fleetOnly}
          onChange={(e) => setFleetOnly(e.target.checked)}
          style={{ marginRight: 6 }}
        />
        Only signals touching fleet vehicles
      </label>

      {data.signals.length === 0 ? (
        <div className="panel">
          No signals match this filter. That is a result, not an error — the detector ran and
          found nothing.
        </div>
      ) : (
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
                  {s.is_live && <span className="tag dnd" style={{ marginLeft: 8 }}>LIVE</span>}
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
      )}

      <p className="muted" style={{ fontSize: 12, maxWidth: 760, marginTop: 18 }}>
        A signal is a <em>detected anomaly</em>, not a confirmed defect and not a recall. The
        detector finds roughly one in six investigations ahead of NHTSA, against 11.1% on a
        matched control — an edge, not an oracle. Harm share is shown for triage and is not
        part of detection.
      </p>
    </>
  );
}
