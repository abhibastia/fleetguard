import { useEffect, useState } from "react";
import { api, ApiError, type ApprovalResult, type CampaignDetail } from "../lib/api";

/**
 * Campaign detail and the approval gate.
 *
 * The approval is the one irreversible action in the console: it writes a service campaign,
 * one work order per exposed vehicle, and an audit row — in a single transaction. So the
 * button states the exact count it is about to create, and the approver is taken from the
 * authenticated session by the backend, never sent from here.
 */
export function Campaign({ id, onBack }: { id: string; onBack: () => void }) {
  const [c, setC] = useState<CampaignDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApprovalResult | null>(null);

  useEffect(() => {
    api
      .campaign(id)
      .then((d) => {
        setC(d);
        setTitle(`${d.park_it ? "Park It — " : ""}${d.component ?? id} remediation`);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [id]);

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.approve(id, { title, rationale, due_in_days: c?.park_it ? 7 : 30 }));
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !c) return <div className="error">{error}</div>;
  if (!c) return <p className="muted">Loading campaign…</p>;

  const depots = Object.entries(c.by_depot).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <button className="crumb" onClick={onBack}>
        ← Queue
      </button>

      <div className="row">
        <div className="grow">
          <h2 style={{ margin: "0 0 6px" }}>
            {c.campaign_id} {c.park_it && <span className="tag parkit">PARK IT</span>}
          </h2>
          <p className="muted" style={{ marginTop: 0 }}>
            {c.component ?? "—"}
          </p>

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
          <table>
            <thead>
              <tr>
                <th>Depot</th>
                <th className="num">Vehicles</th>
              </tr>
            </thead>
            <tbody>
              {depots.slice(0, 12).map(([d, n]) => (
                <tr key={d}>
                  <td>{d}</td>
                  <td className="num">{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {depots.length > 12 && (
            <p className="muted">+ {depots.length - 12} further depots</p>
          )}
        </div>

        <div className="panel" style={{ width: 340, flexShrink: 0 }}>
          <h3 style={{ marginTop: 0 }}>Launch service campaign</h3>

          {result ? (
            <div className="success">
              <strong>{result.service_campaign_id}</strong>
              <br />
              {result.work_orders_created} work orders created
              <br />
              approved by {result.approved_by}
              <br />
              due {result.due_date}
            </div>
          ) : (
            <>
              <label htmlFor="t">Title</label>
              <input id="t" value={title} onChange={(e) => setTitle(e.target.value)} />

              <label htmlFor="r">Rationale — recorded in the audit log</label>
              <textarea
                id="r"
                rows={4}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Why this campaign is being launched now"
              />

              {error && (
                <div className="error" style={{ marginTop: 12 }}>
                  {error}
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
                {busy ? "Launching…" : `Approve — ${c.vehicles_exposed.toLocaleString()} work orders`}
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
}
