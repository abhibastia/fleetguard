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
  if (!data)
    return (
      <>
        <div className="stats">
          {[0, 1, 2].map((i) => (
            <div className="stat" key={i}>
              <div className="skeleton tall" style={{ marginBottom: 0 }} />
            </div>
          ))}
        </div>
        <div className="skeleton wide tall" />
        <div className="skeleton half" />
      </>
    );

  const { real, placebo } = data;

  return (
    <>
      <div className="page-head">
        <h2>Does early warning actually work?</h2>
        <p>
          Measured against every ODI investigation opened since 2010, with a volume-matched control
          arm. The gap between the arms is the evidence — the real arm alone is not.
        </p>
      </div>

      <div className="stats">
        <div className="stat is-ok">
          <div className="v">{real.rate_pct.toFixed(1)}%</div>
          <div className="k">Detected · real arm</div>
        </div>
        <div className="stat">
          <div className="v">{placebo.rate_pct.toFixed(1)}%</div>
          <div className="k">Detected · placebo</div>
        </div>
        <div className="stat">
          <div className="v">{data.lift}×</div>
          <div className="k">Lift</div>
        </div>
        <div className="stat">
          <div className="v">{real.median_lead_days}d</div>
          <div className="k">Median lead</div>
        </div>
      </div>

      <div className="wrap">
        <table className="prose">
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
                <strong>{real.rate_pct.toFixed(1)}%</strong>
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
              <td className="num">{placebo.rate_pct.toFixed(1)}%</td>
              <td className="num">{placebo.median_lead_days} days</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="panel prose" style={{ marginTop: 20 }}>
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
      <ul className="muted prose">
        <li>
          It misses roughly five of every six investigations — {real.detected} of {real.n}.
        </li>
        <li>
          The control fires at {placebo.rate_pct.toFixed(1)}%, so most detections would have
          occurred on a busy series with no defect. This is an edge, not an oracle.
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
      <p className="footnote">
        Source: <code>{data.source_table}</code> · lift and z recomputed from the arm counts ·
        snapshot generated {data.generated_at}
      </p>

      <hr className="divider" />

      <div className="page-head">
        <h2>Model B — how confident is a fuzzy match?</h2>
        <p>
          Exact make/model/year matching resolves only 91 of 163 fleet combinations. The rest —
          nearly 3 in 4 exposure rows — are <strong>variant</strong> matches, where NHTSA's model
          string differs from vPIC's (<code>F-250</code> vs <code>F-250 SD</code>). Model B scores
          how likely a variant really is the same vehicle; it ranks ambiguity, it does not decide
          it.
        </p>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="v">{(data.model_b.precision * 100).toFixed(1)}%</div>
          <div className="k">Precision</div>
        </div>
        <div className="stat">
          <div className="v">{(data.model_b.recall * 100).toFixed(1)}%</div>
          <div className="k">Recall</div>
        </div>
        <div className="stat">
          <div className="v">{data.model_b.roc_auc.toFixed(2)}</div>
          <div className="k">ROC-AUC</div>
        </div>
        <div className="stat">
          <div className="v">{data.model_b.golden_set_size}</div>
          <div className="k">Golden-set pairs</div>
        </div>
      </div>

      <h3>Stated limits</h3>
      <ul className="muted prose">
        <li>
          Threshold {data.model_b.threshold.toFixed(2)} is tuned for{" "}
          <strong>recall over precision</strong>: a fleet manager missing a genuine match leaves a
          vehicle exposed to a real defect; a false positive costs one wasted inspection. The two
          errors are not symmetric.
        </li>
        <li>
          The golden set (<strong>{data.model_b.golden_set_size} pairs</strong>,{" "}
          {data.model_b.golden_set_positive} positive) is derived from NHTSA's own recall
          description text — real regulatory language, not human-adjudicated and not synthetic —
          and evaluated on a {data.model_b.test_set_size}-row held-out split.
        </li>
        <li>
          A first training pass scored a suspicious precision of 100% — traced to a feature that
          was tautologically tied to how the training labels themselves were built, not to
          anything learned. It was found, removed, and retrained; the numbers above are from the
          corrected run (model version {data.model_b.model_version}).
        </li>
      </ul>
    </>
  );
}
