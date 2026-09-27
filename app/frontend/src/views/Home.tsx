import { useEffect, useState } from "react";
import {
  api,
  type Corpus,
  type DepotRisk,
  type Evidence,
  type QueueItem,
  type SignalSummary,
} from "../lib/api";
import { FlowDiagram } from "./HomeFlow";

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
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [queue, setQueue] = useState<QueueItem[] | null>(null);
  const [signals, setSignals] = useState<SignalSummary | null>(null);
  const [depots, setDepots] = useState<DepotRisk[] | null>(null);
  // Populated from /healthz rather than hardcoded: the dashboard's host, workspace and id
  // differ per deployment (Free Edition is a separate workspace on a separate metastore), and
  // this console's built JS is shared byte-for-byte between targets (see
  // scripts/sync_free_edition_app.sh), so a literal here can only ever be right for one of
  // them. Null until /healthz answers, or if the deployment never set FLEETGUARD_DASHBOARD_URL.
  const [dashboardUrl, setDashboardUrl] = useState<string | null>(null);
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
      // Public like evidence, so it renders signed-out too — but still `.catch`ed, because a
      // missing snapshot is a 503 by design (routers/corpus.py) and must degrade to "no scale
      // strip", never to an error page.
      api.corpus().then((c) => live && setCorpus(c)).catch(() => null),
      api.queue(50).then((q) => live && setQueue(q)).catch(() => null),
      api.signals().then((s) => live && setSignals(s)).catch(() => null),
      api.depotRisk().then((d) => live && setDepots(d)).catch(() => null),
      api.health().then((h) => live && setDashboardUrl(h.dashboard_url)).catch(() => null),
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
      {/* The headline used to be "Built for the fleet safety team — and the leadership they
          report to", which named the AUDIENCE and never said what the product does. Someone
          landing cold learned nothing from it. The contrast below is the whole pitch in two
          lines; the detail that used to be a 90-word grey block is now two short columns
          nobody has to commit to reading in order. */}
      <section className="panel home-hero">
        <h2 style={{ marginTop: 0 }}>
          <span className="home-hero-lede">A recall tells you a defect exists.</span>
          <span className="home-hero-turn">
            FleetGuard tells you which of your vehicles it's in.
          </span>
        </h2>

        <div className="home-hero-cols">
          <div>
            <h4>When a campaign posts</h4>
            <p className="muted">
              Scope it against the fleet's own VIN roster, rank by consequence before volume —
              a do-not-drive defect outranks a larger label recall — then approve and dispatch
              one work order per exposed vehicle, in a single transaction. Every write runs
              under the signed-in operator's identity and lands in an append-only audit log.
            </p>
          </div>
          <div>
            <h4>Before one posts</h4>
            <p className="muted">
              Complaint volume is watched for the same defect patterns months before the
              regulator opens a formal investigation. That half is modest and measured against
              a placebo control rather than asserted — the numbers are directly below, including
              the improvements that came back negative.
            </p>
          </div>
        </div>

        <FlowDiagram />
      </section>

      {corpus && (
        <section className="home-scale" aria-label="Corpus scale">
          {/* Deliberately lighter than `.stats` — this is context for the panels below, not a
              fifth competing metric block. Every figure comes from /api/corpus, which is
              derived by scripts/export_corpus.py and committed with provenance; typing them
              in here is the exact mistake I-115 removed from README and Assistant.tsx. */}
          <div className="home-scale-item">
            <b>{(corpus.complaints / 1e6).toFixed(2)}M</b>
            <span>NHTSA complaints</span>
          </div>
          <div className="home-scale-item">
            <b>{(corpus.tsbs / 1e6).toFixed(1)}M</b>
            <span>service bulletins</span>
          </div>
          <div className="home-scale-item">
            <b>{corpus.recalls.toLocaleString()}</b>
            <span>recall records</span>
          </div>
          <div className="home-scale-item">
            <b>{corpus.fleet_vehicles.toLocaleString()}</b>
            <span>fleet vehicles</span>
          </div>
          <div className="home-scale-item">
            <b>{corpus.fleet_depots}</b>
            <span>depots</span>
          </div>
        </section>
      )}

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
        {dashboardUrl && (
          <a
            className="panel home-path home-path-external"
            href={dashboardUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <h3>Analytics dashboard ↗</h3>
            <p className="muted">
              Fleet, exposure, signals and trust across 12 datasets — the fuller rollup for
              safety leadership. Opens in Databricks; needs its own sign-in.
            </p>
          </a>
        )}
      </section>
    </div>
  );
}
