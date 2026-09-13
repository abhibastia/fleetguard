import { useEffect, useState } from "react";
import {
  api,
  type DepotRisk,
  type Evidence,
  type QueueItem,
  type SignalSummary,
} from "../lib/api";

/**
 * The landing page.
 *
 * **Why this exists.** The console opened straight onto the Recall queue — a table of 50
 * campaigns with no explanation of what the system is or what it just told you. A reviewer put
 * it plainly on 2026-09-10: *"looks like there is no home page"*. Someone arriving cold needs
 * to know what they are looking at before they can judge whether the numbers are good.
 *
 * **What it does not do:** invent new numbers. Every figure here comes from an endpoint that
 * already backs a tab, so this page cannot drift from the pages it summarises. The measured
 * result comes from `/api/evidence`, which is deliberately unauthenticated — so the headline
 * claim renders for a signed-out visitor too, and only the live fleet state needs a session.
 */
export function Home({ onNavigate }: { onNavigate: (view: string) => void }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [queue, setQueue] = useState<QueueItem[] | null>(null);
  const [signals, setSignals] = useState<SignalSummary | null>(null);
  const [depots, setDepots] = useState<DepotRisk[] | null>(null);

  useEffect(() => {
    let live = true;
    // Evidence is the only one that must succeed; it needs no session. The rest are allowed to
    // fail — a signed-out visitor gets the argument without the operational state, rather than
    // an error page. `.catch(() => null)` is deliberate here and not swallowing a real bug:
    // these exact 401/403s are the documented signed-out behaviour of those routes.
    api.evidence().then((e) => live && setEvidence(e)).catch(() => null);
    api.queue(50).then((q) => live && setQueue(q)).catch(() => null);
    api.signals().then((s) => live && setSignals(s)).catch(() => null);
    api.depotRisk().then((d) => live && setDepots(d)).catch(() => null);
    return () => {
      live = false;
    };
  }, []);

  const urgent = queue?.filter((q) => q.park_it || q.do_not_drive).length ?? null;
  const exposed = queue?.reduce((n, q) => n + q.vehicles_exposed, 0) ?? null;
  const overdue = depots?.reduce((n, d) => n + d.overdue_work_orders, 0) ?? null;
  const outstanding = depots?.reduce((n, d) => n + d.outstanding_work_orders, 0) ?? null;
  // Every fleet read failed. Both surfaces attach identity outside this app, so this is
  // not "you are signed out" — it is a token the backend could not use, or a backend that
  // is not answering. Say that, rather than offering a sign-in that does not exist here.
  const fleetUnavailable = queue === null && depots === null && signals === null;

  return (
    <div className="home">
      <section className="panel home-hero">
        <h2 style={{ marginTop: 0 }}>Built for the fleet safety team — and the leadership above them</h2>
        <p className="muted" style={{ maxWidth: "72ch" }}>
          A fleet operator learns about a safety defect the same way a private owner does — when
          the recall posts. The <strong>fleet safety team</strong> works the queue below: resolve a
          campaign against the VIN roster, rank by consequence, dispatch work orders under human
          approval. <strong>Safety leadership</strong> reads the two panels under this one instead —
          the measured claim behind the early-warning half, and whether the fleet's open work is
          actually getting closed. Same data, two jobs.
        </p>
      </section>

      {evidence && (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>The measured claim</h3>
          <div className="stats">
            <div className="stat">
              <div className="v">{evidence.real.rate_pct}%</div>
              <div className="k">Detected before NHTSA acted</div>
            </div>
            <div className="stat">
              <div className="v">{evidence.placebo.rate_pct}%</div>
              <div className="k">Placebo control</div>
            </div>
            <div className="stat">
              <div className="v">{evidence.lift.toFixed(2)}×</div>
              <div className="k">Lift · p {evidence.p_value.toFixed(3)}</div>
            </div>
            <div className="stat">
              <div className="v">{evidence.real.median_lead_days}</div>
              <div className="k">Median lead, days</div>
            </div>
          </div>
          <p className="muted" style={{ marginBottom: 0, maxWidth: "72ch" }}>
            Modest and real, against a volume-matched control arm — not a headline number. The
            obvious improvements were tested and published as negatives.{" "}
            <button className="linklike" onClick={() => onNavigate("evidence")}>
              See the evidence →
            </button>
          </p>
        </section>
      )}

      {fleetUnavailable ? (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>Live fleet state</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            Live fleet data is not available right now. The measured result above is served
            from the application itself and needs no fleet connection.
          </p>
        </section>
      ) : (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>Live fleet state</h3>
          <div className="stats">
            <div className={urgent ? "stat is-danger" : "stat"}>
              <div className="v">{urgent ?? "—"}</div>
              <div className="k">Park It campaigns open</div>
            </div>
            <div className="stat">
              <div className="v">{exposed?.toLocaleString() ?? "—"}</div>
              <div className="k">Vehicles exposed</div>
            </div>
            <div className="stat">
              <div className="v">{signals?.fleet_relevant ?? "—"}</div>
              <div className="k">Emerging signals touching the fleet</div>
            </div>
            <div className={overdue ? "stat is-danger" : "stat"}>
              <div className="v">{overdue ?? "—"}</div>
              <div className="k">Overdue work orders</div>
            </div>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            {outstanding !== null && `${outstanding.toLocaleString()} work orders still outstanding `}
            across {depots?.length ?? "—"} depots.
          </p>
        </section>
      )}

      <section className="home-paths">
        <button className="panel home-path" onClick={() => onNavigate("queue")}>
          <h3>Recall queue →</h3>
          <p className="muted">
            A campaign posts. Scope it against the roster, rank by consequence before volume,
            approve, and dispatch one work order per exposed vehicle — in one transaction.
          </p>
        </button>
        <button className="panel home-path" onClick={() => onNavigate("signals")}>
          <h3>Emerging signals →</h3>
          <p className="muted">
            Complaint ramps NHTSA has <em>not</em> recalled. A signal is not an investigation and
            an investigation is not a recall — the tab says which is which.
          </p>
        </button>
        <button className="panel home-path" onClick={() => onNavigate("evidence")}>
          <h3>Evidence →</h3>
          <p className="muted">
            The measured backtest behind the claim above, published rather than asserted —
            including the improvements that were tested and came back negative.
          </p>
        </button>
        <a
          className="panel home-path home-path-external"
          href="https://dbc-7b106152-caf3.cloud.databricks.com/dashboardsv3/01f1a7257e801a2ebb71bdc18fc2113a/published"
          target="_blank"
          rel="noopener noreferrer"
        >
          <h3>Analytics dashboard ↗</h3>
          <p className="muted">
            Fleet, exposure, signals and trust across 12 datasets — the fuller rollup for safety
            leadership. Opens in Databricks; needs its own sign-in.
          </p>
        </a>
      </section>
    </div>
  );
}
