import { useEffect, useRef, useState } from "react";
import { api, type AuthStatus, type Health, type Me } from "./lib/api";
import { applyTheme, currentTheme, type Theme } from "./lib/theme";
import { AuditLog } from "./views/AuditLog";
import { Assistant } from "./views/Assistant";
import { Campaign } from "./views/Campaign";
import { DepotRisk } from "./views/DepotRisk";
import { Evidence } from "./views/Evidence";
import { Home } from "./views/Home";
import { Queue } from "./views/Queue";
import { ServiceCampaigns } from "./views/ServiceCampaigns";
import { Signals } from "./views/Signals";
import { Trends } from "./views/Trends";
import { WorkOrders } from "./views/WorkOrders";

type View =
  | { name: "home" }
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
  if (h === "queue") return { name: "queue" };
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
  return { name: "home" };
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
  if (v.name === "queue") return "#/queue";
  return "#/";
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

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 5.5h16v10.2H9.4L5 19.5v-3.8H4V5.5Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 6.5h16M4 12h16M4 17.5h16"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
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
  const [me, setMe] = useState<Me | null>(null);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  // The assistant used to exist only beside the Queue tab — asking it something while looking
  // at Work Orders meant navigating away and losing that context. A floating dock keeps it
  // reachable from every tab instead; closed by default so it never fires an API call until
  // someone actually wants it.
  const [assistantOpen, setAssistantOpen] = useState(false);
  const assistantFabRef = useRef<HTMLButtonElement>(null);
  const assistantDockRef = useRef<HTMLDivElement>(null);
  // The 9-tab horizontal nav is now a permanent left sidebar, off-canvas below 900px (this
  // app's one existing "narrow viewport" threshold). Kept as its own state/effect pair rather
  // than merged into the assistant's — each disclosure surface owns its own open/close
  // contract.
  const [navOpen, setNavOpen] = useState(false);
  const navToggleRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  // A floating panel with no dialog semantics: `role=null`, focus never moved into it on
  // open, and Escape did nothing — found in a UI/UX review, 2026-09-14. Move focus to the
  // dock's first focusable control on open, close on Escape, and return focus to the FAB
  // that opened it (the standard disclosure-widget contract, not just an a11y nicety — the
  // dock has no other way to close via keyboard).
  useEffect(() => {
    if (!assistantOpen) return;
    const firstField = assistantDockRef.current?.querySelector<HTMLElement>("input, textarea, button");
    firstField?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setAssistantOpen(false);
        assistantFabRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [assistantOpen]);

  // Same disclosure contract as the assistant dock above: focus the first control on open,
  // close on Escape. No cyclic Tab trap and no `inert`/`aria-hidden` on `<main>` — deliberately
  // matching the assistant's own (lighter) modal contract rather than making this drawer
  // stricter than everything else in the app.
  useEffect(() => {
    if (!navOpen) return;
    const firstLink = sidebarRef.current?.querySelector<HTMLElement>("button, a");
    firstLink?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setNavOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // Return focus to the hamburger on every close path (Escape, backdrop click, or picking a
  // destination) — a drawer has more ways to dismiss than the assistant dock's single close
  // button, so this is a separate effect rather than folded into the Escape handler above.
  // `navToggleRef.current` is `display: none` above 900px, and `.focus()` on a display:none
  // element is a documented no-op everywhere — safe to run unconditionally rather than
  // gating this on viewport width in JS.
  useEffect(() => {
    if (!navOpen) navToggleRef.current?.focus();
  }, [navOpen]);

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

  return (
    <>
      <button
        ref={navToggleRef}
        className="nav-toggle"
        onClick={() => setNavOpen((v) => !v)}
        aria-expanded={navOpen}
        aria-controls="sidebar"
        aria-label={navOpen ? "Close navigation" : "Open navigation"}
      >
        {navOpen ? <CloseIcon /> : <MenuIcon />}
      </button>

      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}

      <aside id="sidebar" ref={sidebarRef} className={navOpen ? "sidebar open" : "sidebar"}>
        <h1 className="brand">
          <span className="brand-mark-chip">
            <Mark />
          </span>
          FleetGuard
        </h1>

        <nav className="sidebar-nav">
          <button
            onClick={() => {
              setView({ name: "home" });
              setNavOpen(false);
            }}
            aria-current={tab === "home" ? "page" : undefined}
          >
            Home
          </button>
          <button
            onClick={() => {
              setView({ name: "queue" });
              setNavOpen(false);
            }}
            aria-current={tab === "queue" ? "page" : undefined}
          >
            Recall queue
          </button>
          <button
            onClick={() => {
              setView({ name: "signals" });
              setNavOpen(false);
            }}
            aria-current={tab === "signals" ? "page" : undefined}
          >
            Emerging
          </button>
          <button
            onClick={() => {
              setView({ name: "evidence" });
              setNavOpen(false);
            }}
            aria-current={tab === "evidence" ? "page" : undefined}
          >
            Evidence
          </button>
          <button
            onClick={() => {
              setView({ name: "launched" });
              setNavOpen(false);
            }}
            aria-current={tab === "launched" ? "page" : undefined}
          >
            Launched
          </button>
          <button
            onClick={() => {
              setView({ name: "work-orders" });
              setNavOpen(false);
            }}
            aria-current={tab === "work-orders" ? "page" : undefined}
          >
            Work orders
          </button>
          <button
            onClick={() => {
              setView({ name: "audit-log" });
              setNavOpen(false);
            }}
            aria-current={tab === "audit-log" ? "page" : undefined}
          >
            Audit log
          </button>
          <button
            onClick={() => {
              setView({ name: "depot-risk" });
              setNavOpen(false);
            }}
            aria-current={tab === "depot-risk" ? "page" : undefined}
          >
            Depots
          </button>
          <button
            onClick={() => {
              setView({ name: "trends" });
              setNavOpen(false);
            }}
            aria-current={tab === "trends" ? "page" : undefined}
          >
            Trends
          </button>
        </nav>

        <div className="sidebar-footer">
          <span className="who">
            {/* Green means "an identity is attached to what you do here". A successful /me
                call with no user_name is NOT that — it is the dev/static path, and showing
                green beside "not signed in" would contradict the words next to it. */}
            <span className={auth?.user_name || me?.user_name ? "dot" : "dot off"} />
            {auth?.user_name ?? me?.user_name ?? "not signed in"}
            {auth?.signed_in && !auth.may_approve && <span className="muted">· read-only</span>}
            {me && !auth?.signed_in && <span className="muted">· {me.token_source}</span>}
          </span>

          <ThemeToggle />
        </div>
      </aside>

      <main className={assistantOpen ? "assistant-open" : undefined}>
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

        {view.name === "queue" && (
          <Queue onOpen={(id) => setView({ name: "campaign", id })} />
        )}
        {view.name === "campaign" && (
          <Campaign id={view.id} onBack={() => setView({ name: "queue" })} />
        )}
        {view.name === "signals" && <Signals />}
        {view.name === "launched" && (
          <ServiceCampaigns
            onOpen={(serviceCampaignId) => setView({ name: "work-orders", serviceCampaignId })}
          />
        )}
        {view.name === "work-orders" && (
          <WorkOrders
            serviceCampaignId={view.serviceCampaignId}
            onClearFilter={
              view.serviceCampaignId ? () => setView({ name: "work-orders" }) : undefined
            }
          />
        )}
        {view.name === "audit-log" && <AuditLog />}
        {view.name === "depot-risk" && <DepotRisk />}
        {view.name === "trends" && <Trends />}
        {view.name === "home" && (
          <Home
            onNavigate={(v) =>
              setView(
                // Explicit per target, not a fallback — a card added later that passes an
                // id nobody maps here should be a visible bug, not a silent misroute to
                // Evidence (the previous shape of this ternary, before there were three
                // internal cards to tell apart).
                v === "queue"
                  ? { name: "queue" }
                  : v === "signals"
                    ? { name: "signals" }
                    : { name: "evidence" }, // v === "evidence"
              )
            }
          />
        )}
        {view.name === "evidence" && <Evidence />}
      </main>

      {/* Available from every tab, not just Queue — the assistant is self-contained (owns its
          own turns/error state), so lifting it here cost nothing but the wrapper. Assistant's
          own 401 handling covers the case where the caller is identified but the serving
          endpoint call itself fails. */}
      {(
        <>
          <button
            ref={assistantFabRef}
            className="assistant-fab"
            onClick={() => setAssistantOpen((v) => !v)}
            aria-expanded={assistantOpen}
            aria-label={assistantOpen ? "Close assistant" : "Open assistant"}
            title={assistantOpen ? "Close assistant" : "Ask the assistant"}
          >
            {assistantOpen ? <CloseIcon /> : <ChatIcon />}
          </button>
          {assistantOpen && (
            <div ref={assistantDockRef} className="assistant-dock" role="dialog" aria-label="Assistant">
              <Assistant />
            </div>
          )}
        </>
      )}
    </>
  );
}
