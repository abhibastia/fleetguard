/**
 * The measured backtest, stated with its control arm.
 *
 * Hardcoded from `gold_lead_time_summary` for MVP; the `/evidence` endpoint replaces these
 * constants without changing the presentation. The presentation is the point: a detection
 * rate alone is not evidence, so the placebo column is not optional and the limits are
 * stated beside the headline rather than in a footnote.
 */
const REAL = { n: 777, detected: 124, rate: 16.0, median: 197 };
const PLACEBO = { n: 606, detected: 67, rate: 11.1, median: 343 };

export function Evidence() {
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
            <td className="num">{REAL.n}</td>
            <td className="num">{REAL.detected}</td>
            <td className="num">
              <strong>{REAL.rate}%</strong>
            </td>
            <td className="num">
              <strong>{REAL.median} days</strong>
            </td>
          </tr>
          <tr>
            <td>
              Placebo <span className="muted">— volume-matched control</span>
            </td>
            <td className="num">{PLACEBO.n}</td>
            <td className="num">{PLACEBO.detected}</td>
            <td className="num">{PLACEBO.rate}%</td>
            <td className="num">{PLACEBO.median} days</td>
          </tr>
        </tbody>
      </table>

      <div className="panel" style={{ marginTop: 20, maxWidth: 720 }}>
        <p style={{ marginTop: 0 }}>
          <strong>1.44× lift</strong>, two-proportion z ≈ 2.62, <strong>p ≈ 0.009</strong>.
          Statistically real, practically modest.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          The <em>shape</em> is stronger evidence than the rate: real detections cluster near the
          investigation open date ({REAL.median} days) while control detections scatter toward the
          window midpoint ({PLACEBO.median} days) — what a detector tracking a genuine defect ramp
          produces, rather than one firing on background variance.
        </p>
      </div>

      <h3>Stated limits</h3>
      <ul className="muted" style={{ maxWidth: 720 }}>
        <li>
          It misses roughly five of every six investigations — {REAL.detected} of {REAL.n}.
        </li>
        <li>
          The control fires at {PLACEBO.rate}%, so most detections would have occurred on a busy
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
    </>
  );
}
