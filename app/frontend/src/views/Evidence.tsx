import { useEffect, useState } from "react";
import { api, ApiError, type Evidence as EvidenceData } from "../lib/api";

/**
 * The measured backtest, stated with its control arm.
 *
 * Figures come from `GET /api/evidence` — a snapshot generated from
 * `gold_lead_time_summary` by `scripts/export_evidence.py`, with the lift and the
 * two-proportion z recomputed from the arm counts. They are no longer typed by hand, and
 * the page shows where they came from.
 *
 * The presentation is the point: a detection rate alone is not evidence, so the placebo
 * column is not optional and the limits sit beside the headline rather than in a footnote.
 */
export function Evidence() {
  const [data, setData] = useState<EvidenceData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .evidence()
      .then(setData)
      .catch((e: ApiError) => setError(e.message));
  }, []);

  // Never render zeros on failure — a blank result table would read as "the method found
  // nothing" rather than "the numbers failed to load".
  if (error) return <div className="error">Evidence unavailable: {error}</div>;
  if (!data) return <p className="muted">Loading measured results…</p>;

  const { real, placebo } = data;

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Does early warning actually work?</h2>
      <p className="muted">
        Measured against every ODI investigation opened since 2010, with a volume-matched
        control arm. The gap between the arms is the evidence — the real arm alone is not.
      </p>

      <table style={{ maxWidth: 720 }}>
        <thead>
          <tr>
            <th>Arm</th>
            <th className="num">Investigations</th>
            <th className="num">Detected</th>
            <th className="num">Rate</th>
            <th className="num">Median lead</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <strong>Real</strong> <span className="muted">— investigated series</span>
            </td>
            <td className="num">{real.n}</td>
            <td className="num">{real.detected}</td>
            <td className="num">
              <strong>{real.rate_pct}%</strong>
            </td>
            <td className="num">
              <strong>{real.median_lead_days} days</strong>
            </td>
          </tr>
          <tr>
            <td>
              Placebo <span className="muted">— volume-matched control</span>
            </td>
            <td className="num">{placebo.n}</td>
            <td className="num">{placebo.detected}</td>
            <td className="num">{placebo.rate_pct}%</td>
            <td className="num">{placebo.median_lead_days} days</td>
          </tr>
        </tbody>
      </table>

      <div className="panel" style={{ marginTop: 20, maxWidth: 720 }}>
        <p style={{ marginTop: 0 }}>
          <strong>{data.lift}× lift</strong>, two-proportion z ≈ {data.z},{" "}
          <strong>p ≈ {data.p_value}</strong>. Statistically real, practically modest.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          The <em>shape</em> is stronger evidence than the rate: real detections cluster near the
          investigation open date ({real.median_lead_days} days) while control detections scatter
          toward the window midpoint ({placebo.median_lead_days} days) — what a detector tracking a
          genuine defect ramp produces, rather than one firing on background variance.
        </p>
      </div>

      <h3>Stated limits</h3>
      <ul className="muted" style={{ maxWidth: 720 }}>
        <li>
          It misses roughly five of every six investigations — {real.detected} of {real.n}.
        </li>
        <li>
          The control fires at {placebo.rate_pct}%, so most detections would have occurred on a busy
          series with no defect. This is an edge, not an oracle.
        </li>
        <li>
          It predicts that an <strong>investigation will open</strong> — not that a recall will be
          issued, and not which VINs are affected.
        </li>
        <li>
          Adding semantic clustering was tested and <strong>made detection worse</strong> (11.2%,
          with zero extra lead time). That negative result is published rather than buried.
        </li>
      </ul>

      {/* Provenance, not decoration: a claimed measurement that cannot be traced to a query
          is indistinguishable from a claimed measurement that was typed in. */}
      <p className="muted" style={{ fontSize: 12, maxWidth: 720 }}>
        Source: <code>{data.source_table}</code> · lift and z recomputed from the arm counts ·
        snapshot generated {data.generated_at}
      </p>
    </>
  );
}
