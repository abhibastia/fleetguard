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
  // Distinct from "the four states are still null" — that's ALSO true for a millisecond on
  // every normal load, before any request has had a chance to return. Without this, the
  // "not available" message rendered as a false failure on every cold visit (reproduced at
  // 350ms in a UI/UX review, 2026-09-14) because it read absence-so-far as absence-forever.
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let live = true;
    // Evidence is the only one that must succeed; it needs no session. The rest are allowed to
    // fail — a signed-out visitor gets the argument without the operational state, rather than
    // an error page. `.catch(() => null)` is deliberate here and not swallowing a real bug:
    // these exact 401/403s are the documented signed-out behaviour of those routes. Each
    // promise below therefore always *resolves* (never rejects), so `Promise.all` — not
    // `allSettled` — is enough to know when every attempt has finished, one way or another.
    Promise.all([
      api.evidence().then((e) => live && setEvidence(e)).catch(() => null),
      api.queue(50).then((q) => live && setQueue(q)).catch(() => null),
      api.signals().then((s) => live && setSignals(s)).catch(() => null),
      api.depotRisk().then((d) => live && setDepots(d)).catch(() => null),
    ]).then(() => live && setSettled(true));
    return () => {
      live = false;
    };
  }, []);

  const urgent = queue?.filter((q) => q.park_it || q.do_not_drive).length ?? null;
  const exposed = queue?.reduce((n, q) => n + q.vehicles_exposed, 0) ?? null;
  const overdue = depots?.reduce((n, d) => n + d.overdue_work_orders, 0) ?? null;
  const outstanding = depots?.reduce((n, d) => n + d.outstanding_work_orders, 0) ?? null;
  // Every fleet read failed, and every fetch has actually had a chance to. Both surfaces
  // attach identity outside this app, so this is not "you are signed out" — it is a token
  // the backend could not use, or a backend that is not answering. Say that, rather than
  // offering a sign-in that does not exist here, and rather than saying anything at all
  // before the requests have even settled.
  const fleetUnavailable = settled && queue === null && depots === null && signals === null;

  return (
    <div className="home">
      <section className="panel home-hero">
        <h2 style={{ marginTop: 0 }}>Built for the fleet safety team — and the leadership above them</h2>
        <p className="muted" style={{ maxWidth: "88ch" }}>
          FleetGuard resolves an NHTSA recall against your fleet's VIN roster in seconds, and
          watches complaint volume for the same defect patterns <em>before</em> the regulator
          opens a formal investigation — two capabilities most fleets only get after the fact.
          The <strong>fleet safety team</strong> works the queue below: scope a campaign, rank by
          consequence, dispatch work orders under human approval — every action attributed and
          audited. <strong>Safety leadership</strong> reads the two panels under this one instead
          — the measured evidence behind the early-warning half, and whether the fleet's open
          work is actually getting closed. Same NHTSA data and Databricks pipeline underneath,
          two different jobs.
        </p>
      </section>

      {evidence ? (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>The measured claim</h3>
          <div className="stats">
            <div className="stat">
              {/* Matches Evidence.tsx's own formatting (toFixed(1), raw p-value with ≈) —
                  this page and Evidence.tsx used to render the same numbers two different
                  ways (16% vs 16.0%, "p 0.009" vs "p ≈ 0.0087"), reading as two different
                  measurements one click apart. Found in a UI/UX review, 2026-09-14. */}
              <div className="v">{evidence.real.rate_pct.toFixed(1)}%</div>
              <div className="k">Detected before NHTSA acted</div>
            </div>
            <div className="stat">
              <div className="v">{evidence.placebo.rate_pct.toFixed(1)}%</div>
              <div className="k">Placebo control</div>
            </div>
            <div className="stat">
              <div className="v">{evidence.lift.toFixed(2)}×</div>
              <div className="k">Lift · p ≈ {evidence.p_value}</div>
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
      ) : settled ? (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>The measured claim</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            The measured result could not be loaded right now — it is normally served
            unauthenticated, so this points at the backend rather than your session.
          </p>
        </section>
      ) : (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>The measured claim</h3>
          <div className="stats">
            {[0, 1, 2, 3].map((i) => (
              <div className="stat" key={i}>
                <div className="skeleton tall" style={{ marginBottom: 0 }} />
              </div>
            ))}
          </div>
        </section>
      )}

      {!settled ? (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>Live fleet state</h3>
          <div className="stats">
            {[0, 1, 2, 3].map((i) => (
              <div className="stat" key={i}>
                <div className="skeleton tall" style={{ marginBottom: 0 }} />
              </div>
            ))}
          </div>
        </section>
      ) : fleetUnavailable ? (
        <section className="panel">
          <h3 style={{ marginTop: 0 }}>Live fleet state</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            Live fleet data is not available right now.
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
          {/* Guarded on `depots` alone, not on `outstanding` — the two are never
              independently null (outstanding is derived from depots), but the old version
              rendered the fragment "across — depots." with no subject when depots failed
              while queue/signals succeeded. Found in a UI/UX review, 2026-09-14. */}
          <p className="muted" style={{ marginBottom: 0 }}>
            {depots !== null
              ? `${outstanding!.toLocaleString()} work orders still outstanding across ${depots.length} depots.`
              : "Depot rollup unavailable right now."}
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
