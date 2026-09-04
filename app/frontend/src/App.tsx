import { useEffect, useRef, useState } from "react";
import { api, type AuthStatus, type Health, type Me } from "./lib/api";
import { applyTheme, currentTheme, type Theme } from "./lib/theme";
import { AuditLog } from "./views/AuditLog";
import { Assistant } from "./views/Assistant";
import { Campaign } from "./views/Campaign";
import { DepotRisk } from "./views/DepotRisk";
import { Evidence } from "./views/Evidence";
import { Queue } from "./views/Queue";
import { ServiceCampaigns } from "./views/ServiceCampaigns";
import { SignIn } from "./views/SignIn";
import { Signals } from "./views/Signals";
import { Trends } from "./views/Trends";
import { WorkOrders } from "./views/WorkOrders";

type View =
  | { name: "queue" }
  | { name: "signals" }
  | { name: "campaign"; id: string }
  | { name: "evidence" }
  | { name: "work-orders"; serviceCampaignId?: string }
  | { name: "launched" }
  | { name: "audit-log" }
  | { name: "depot-risk" }
  | { name: "trends" };

/**
 * The console is a single page, but its tabs are addressable.
 *
 * Without this the Evidence page — the one surface that needs no sign-in and carries the
 * measured result — could not be linked to. Hash routing rather than the History API because
 * it needs no server rewrite beyond the SPA fallback that already exists.
 */
function viewFromHash(): View {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (h.startsWith("campaign/")) return { name: "campaign", id: decodeURIComponent(h.slice(9)) };
  if (h === "emerging") return { name: "signals" };
  if (h === "evidence") return { name: "evidence" };
  if (h.startsWith("work-orders/")) {
    return { name: "work-orders", serviceCampaignId: decodeURIComponent(h.slice(12)) };
  }
  if (h === "work-orders") return { name: "work-orders" };
  if (h === "launched") return { name: "launched" };
  if (h === "audit-log") return { name: "audit-log" };
  if (h === "depot-risk") return { name: "depot-risk" };
  if (h === "trends") return { name: "trends" };
  return { name: "queue" };
}

function hashForView(v: View): string {
  if (v.name === "campaign") return `#/campaign/${encodeURIComponent(v.id)}`;
  if (v.name === "signals") return "#/emerging";
  if (v.name === "evidence") return "#/evidence";
  if (v.name === "work-orders") {
    return v.serviceCampaignId
      ? `#/work-orders/${encodeURIComponent(v.serviceCampaignId)}`
      : "#/work-orders";
  }
  if (v.name === "launched") return "#/launched";
  if (v.name === "audit-log") return "#/audit-log";
  if (v.name === "depot-risk") return "#/depot-risk";
  if (v.name === "trends") return "#/trends";
  return "#/queue";
}

/** A shield over a fleet, in one glyph. Inline rather than a file so it inherits the
 *  palette and costs no extra request. */
function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2.5 4.5 5.5v6c0 4.6 3.1 8.7 7.5 10 4.4-1.3 7.5-5.4 7.5-10v-6L12 2.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M12 8.6v6.8M8.7 11.3h6.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 13.6A8.2 8.2 0 0 1 10.4 4a8.4 8.4 0 1 0 9.6 9.6Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Shows the theme you would switch *to*, which is the convention users expect from a
 *  single-button toggle — and says so in the label, because an icon alone is ambiguous. */
function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  function flip() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  }

  return (
    <button
      className="theme-toggle"
      onClick={flip}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

export function App() {
  const [view, setView] = useState<View>(viewFromHash);
  // Whether the visitor arrived with an explicit destination. Only a *default* landing is
  // ours to change; a shared link must go where it says.
  const [landed] = useState(() => window.location.hash !== "");
  const [me, setMe] = useState<Me | null>(null);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  // Back/forward must work: a browser button that silently does nothing is worse than no
  // routing at all.
  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Write the hash whenever the view changes, without pushing a duplicate entry when the
  // change *came from* the hash.
  useEffect(() => {
    const want = hashForView(view);
    if (window.location.hash !== want) window.location.hash = want;
  }, [view]);

  // Identity is displayed because every approval is attributed to it. An operator should be
  // able to see who they are signed in as before launching 1,801 work orders.
  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null));
    // Unauthenticated by design — the console must be able to ask "should I show a sign-in
    // screen?" without already being signed in.
    api
      .authStatus()
      .then(setAuth)
      .catch(() => setAuth(null));
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // Campaign detail is reached *from* the queue, so it keeps the queue tab marked current
  // rather than leaving no tab active — which reads as "you are nowhere".
  const tab = view.name === "campaign" ? "queue" : view.name;

  // Closed only when sign-in is actually configured and the visitor is not signed in. If the
  // deployment has no sign-in at all, showing a gate nobody can pass would strand them.
  const gateClosed = Boolean(auth?.enabled && !auth.signed_in);

  // An anonymous visitor with no destination lands on Evidence, not on a login wall.
  //
  // Two reasons, and the second is the important one. (1) Google Safe Browsing flagged this
  // host as "Dangerous site": a zero-reputation shared subdomain whose entire public surface
  // is a "Continue with GitHub" prompt is a textbook phishing signature. (2) It was the wrong
  // front door anyway — this URL exists to show the measured result to people who will never
  // sign in, and the first thing they met was a form.
  // Fires at most once. `landed` never changes after mount, so without this guard the
  // effect fires again on every later transition back to "queue" — including a user
  // deliberately clicking the Recall queue tab after being redirected — and silently
  // bounces them back to Evidence, making the tab look broken. Found in the 2026-09-02
  // repo review by tracing the interaction past the first render, not just checking that
  // the initial redirect worked.
  const autoRedirected = useRef(false);
  useEffect(() => {
    if (autoRedirected.current) return;
    if (gateClosed && !landed && view.name === "queue") {
      autoRedirected.current = true;
      setView({ name: "evidence" });
    }
  }, [gateClosed, landed, view.name]);

  return (
    <>
      <header>
        <h1 className="brand">
          <span className="brand-mark-chip">
            <Mark />
          </span>
          FleetGuard
        </h1>

        <nav className="tabs">
          <button
            onClick={() => setView({ name: "queue" })}
            aria-current={tab === "queue" ? "page" : undefined}
          >
            Recall queue
          </button>
          <button
            onClick={() => setView({ name: "signals" })}
            aria-current={tab === "signals" ? "page" : undefined}
          >
            Emerging
          </button>
          <button
            onClick={() => setView({ name: "evidence" })}
            aria-current={tab === "evidence" ? "page" : undefined}
          >
            Evidence
          </button>
          <button
            onClick={() => setView({ name: "launched" })}
            aria-current={tab === "launched" ? "page" : undefined}
          >
            Launched
          </button>
          <button
            onClick={() => setView({ name: "work-orders" })}
            aria-current={tab === "work-orders" ? "page" : undefined}
          >
            Work orders
          </button>
          <button
            onClick={() => setView({ name: "audit-log" })}
            aria-current={tab === "audit-log" ? "page" : undefined}
          >
            Audit log
          </button>
          <button
            onClick={() => setView({ name: "depot-risk" })}
            aria-current={tab === "depot-risk" ? "page" : undefined}
          >
            Depots
          </button>
          <button
            onClick={() => setView({ name: "trends" })}
            aria-current={tab === "trends" ? "page" : undefined}
          >
            Trends
          </button>
        </nav>

        <span className="who">
          {/* Green means "an identity is attached to what you do here". A successful /me
              call with no user_name is NOT that — it is the dev/static path, and showing
              green beside "not signed in" would contradict the words next to it. */}
          <span className={auth?.user_name || me?.user_name ? "dot" : "dot off"} />
          {auth?.user_name ?? me?.user_name ?? "not signed in"}
          {auth?.signed_in && !auth.may_approve && <span className="muted">· read-only</span>}
          {!auth?.signed_in && me && <span className="muted">· {me.token_source}</span>}
          {auth?.signed_in && (
            <form method="post" action="/api/auth/logout" style={{ margin: 0 }}>
              <button className="crumb" style={{ margin: 0, fontSize: 12 }} type="submit">
                Sign out
              </button>
            </form>
          )}
          {gateClosed && auth?.login_url && (
            <a className="signin-link" href={auth.login_url}>
              Sign in
            </a>
          )}
        </span>

        <ThemeToggle />
      </header>

      <main>
        {/* Say what is being served. A console showing point-in-time data while implying it
            is live is the interface version of reporting a failed query as "no results". */}
        {health?.data_mode === "snapshot" && view.name !== "evidence" && (
          <div className="snapshot-note">
            <strong>SNAPSHOT</strong>
            <span>
              Fleet data captured from live Lakebase
              {health.snapshot_captured_at ? ` on ${health.snapshot_captured_at.slice(0, 10)}` : ""}
              , not queried live — this public deployment holds no Databricks credential. Approving
              a campaign is disabled here.
            </span>
          </div>
        )}

        {/* Evidence stays reachable without a session: it is a published result about public
            NHTSA data, and it is the reason the public URL exists. Everything else reads
            fleet data and waits behind the gate. */}
        {gateClosed && view.name !== "evidence" && (
          <SignIn enabled={auth?.enabled ?? false} provider={auth?.provider ?? "github"} loginUrl={auth?.login_url ?? null} />
        )}

        {!gateClosed && view.name === "queue" && (
          <div className="split">
            <div>
              <Queue onOpen={(id) => setView({ name: "campaign", id })} />
            </div>
            <Assistant />
          </div>
        )}
        {!gateClosed && view.name === "campaign" && (
          <Campaign id={view.id} onBack={() => setView({ name: "queue" })} />
        )}
        {!gateClosed && view.name === "signals" && <Signals />}
        {!gateClosed && view.name === "launched" && (
          <ServiceCampaigns
            onOpen={(serviceCampaignId) => setView({ name: "work-orders", serviceCampaignId })}
          />
        )}
        {!gateClosed && view.name === "work-orders" && (
          <WorkOrders
            serviceCampaignId={view.serviceCampaignId}
            onClearFilter={
              view.serviceCampaignId ? () => setView({ name: "work-orders" }) : undefined
            }
          />
        )}
        {!gateClosed && view.name === "audit-log" && <AuditLog />}
        {!gateClosed && view.name === "depot-risk" && <DepotRisk />}
        {!gateClosed && view.name === "trends" && <Trends />}
        {view.name === "evidence" && <Evidence />}
      </main>
    </>
  );
}
