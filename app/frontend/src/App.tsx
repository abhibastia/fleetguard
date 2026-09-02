import { useEffect, useState } from "react";
import { api, type Me } from "./lib/api";
import { Assistant } from "./views/Assistant";
import { Campaign } from "./views/Campaign";
import { Evidence } from "./views/Evidence";
import { Queue } from "./views/Queue";
import { Signals } from "./views/Signals";

type View =
  { name: "queue" } | { name: "signals" } | { name: "campaign"; id: string } | { name: "evidence" };

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
  return { name: "queue" };
}

function hashForView(v: View): string {
  if (v.name === "campaign") return `#/campaign/${encodeURIComponent(v.id)}`;
  if (v.name === "signals") return "#/emerging";
  if (v.name === "evidence") return "#/evidence";
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

export function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [me, setMe] = useState<Me | null>(null);

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
  }, []);

  // Campaign detail is reached *from* the queue, so it keeps the queue tab marked current
  // rather than leaving no tab active — which reads as "you are nowhere".
  const tab = view.name === "campaign" ? "queue" : view.name;

  return (
    <>
      <header>
        <h1 className="brand">
          <Mark />
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
        </nav>

        <span className="who">
          {/* Green means "an identity is attached to what you do here". A successful /me
              call with no user_name is NOT that — it is the dev/static path, and showing
              green beside "not signed in" would contradict the words next to it. */}
          <span className={me?.user_name ? "dot" : "dot off"} />
          {me?.user_name ?? "not signed in"}
          {me && <span className="muted">· {me.token_source}</span>}
        </span>
      </header>

      <main>
        {view.name === "queue" && (
          <div className="split">
            <div>
              <Queue onOpen={(id) => setView({ name: "campaign", id })} />
            </div>
            <Assistant />
          </div>
        )}
        {view.name === "campaign" && (
          <Campaign id={view.id} onBack={() => setView({ name: "queue" })} />
        )}
        {view.name === "signals" && <Signals />}
        {view.name === "evidence" && <Evidence />}
      </main>
    </>
  );
}
