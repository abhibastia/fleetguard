import { useEffect, useState } from "react";
import { api, type Me } from "./lib/api";
import { Assistant } from "./views/Assistant";
import { Campaign } from "./views/Campaign";
import { Evidence } from "./views/Evidence";
import { Queue } from "./views/Queue";
import { Signals } from "./views/Signals";

type View =
  | { name: "queue" }
  | { name: "signals" }
  | { name: "campaign"; id: string }
  | { name: "evidence" };

export function App() {
  const [view, setView] = useState<View>({ name: "queue" });
  const [me, setMe] = useState<Me | null>(null);

  // Identity is displayed because every approval is attributed to it. An operator should be
  // able to see who they are signed in as before launching 1,801 work orders.
  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  return (
    <>
      <header>
        <h1>FleetGuard</h1>
        <button className="crumb" style={{ margin: 0 }} onClick={() => setView({ name: "queue" })}>
          Queue
        </button>
        <button className="crumb" style={{ margin: 0 }} onClick={() => setView({ name: "signals" })}>
          Emerging
        </button>
        <button className="crumb" style={{ margin: 0 }} onClick={() => setView({ name: "evidence" })}>
          Evidence
        </button>
        <span className="who">
          {me?.user_name ?? "not signed in"}
          {me && <span className="muted"> · {me.token_source}</span>}
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
