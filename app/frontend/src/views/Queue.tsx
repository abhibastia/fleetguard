import { useEffect, useState } from "react";
import { api, ApiError, type QueueItem } from "../lib/api";

/**
 * The work queue — the operator's landing surface.
 *
 * Ordering comes from the backend and is deliberately **consequence before volume**: a
 * 25-vehicle do-not-drive defect outranks a 1,801-vehicle label recall. The UI must not
 * re-sort by count, or it would undo that judgement.
 */
export function Queue({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);

  useEffect(() => {
    api
      .queue()
      .then(setItems)
      .catch((e: ApiError) => (e.status === 401 ? setGated(true) : setError(e.message)));
  }, []);

  // A 401 here is expected until the Databricks OAuth app is registered. Explaining that is
  // far better than a red error a viewer would read as a broken deployment.
  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted">
          The operator queue reads live fleet exposure under <em>your</em> Databricks identity,
          so it needs an authenticated session. User sign-in is not yet configured on this
          deployment.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          The <strong>Evidence</strong> tab needs no sign-in — it carries the measured
          early-warning result and its control arm.
        </p>
      </div>
    );
  if (error) return <div className="error">{error}</div>;
  if (!items) return <p className="muted">Loading exposure…</p>;

  // An empty queue is ambiguous — it could mean "no exposure" or "the filter is wrong".
  // Say which, rather than rendering a blank table.
  if (items.length === 0)
    return <div className="panel">No campaigns currently match any vehicle in the fleet.</div>;

  const urgent = items.filter((i) => i.park_it || i.do_not_drive).length;

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {items.length} campaigns affecting the fleet
        {urgent > 0 && (
          <>
            {" · "}
            <strong style={{ color: "var(--danger)" }}>{urgent} requiring immediate action</strong>
          </>
        )}
      </p>
      <table>
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Component</th>
            <th>Severity</th>
            <th className="num">Vehicles</th>
            <th className="num">Depots</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.campaign_id} onClick={() => onOpen(i.campaign_id)}>
              <td>
                <strong>{i.campaign_id}</strong>
              </td>
              <td className="muted">{i.component ?? "—"}</td>
              <td>
                {i.park_it && <span className="tag parkit">PARK IT</span>}
                {!i.park_it && i.do_not_drive && <span className="tag dnd">DO NOT DRIVE</span>}
                {!i.park_it && !i.do_not_drive && <span className="muted">—</span>}
              </td>
              <td className="num">{i.vehicles_exposed.toLocaleString()}</td>
              <td className="num">{i.depots_affected}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
