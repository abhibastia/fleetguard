import { useState } from "react";
import { api, ApiError, type AgentActionResult, type ChatTurn } from "../lib/api";
import { renderMarkdownLite } from "../lib/markdown";

// Spans three of the agent's five read tools (fleet exposure, emerging signals, complaint
// search) so the empty state demonstrates real breadth, not just the one example someone has
// to already know to type. Clicking sends immediately — this used to be inert text the
// operator had to retype or copy by hand.
const EXAMPLE_PROMPTS = [
  "Which fleet vehicles does recall 17V629000 affect?",
  "What emerging defect signals are affecting our fleet right now?",
  "Search complaints about brake failures",
];

/**
 * The agent panel — advisory, and sitting *beside* the queue rather than in front of it.
 *
 * The deterministic path (recall → exposure → approval) is what the operator acts on. This
 * panel explains and searches; it cannot launch anything. That separation is the reason a
 * fleet safety team could plausibly run this, so the UI states it rather than assuming the
 * user will infer it.
 */
export function Assistant() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gated, setGated] = useState(false);
  const [lastAction, setLastAction] = useState<AgentActionResult | null>(null);

  async function send(override?: string) {
    const question = (override ?? draft).trim();
    if (!question || busy) return;

    const next: ChatTurn[] = [...turns, { role: "user", content: question }];
    setTurns(next);
    setDraft("");
    setBusy(true);
    setError(null);

    try {
      const { reply, action_result } = await api.chat(next);
      // `reply` is already stripped of the action envelope server-side, so replaying these
      // turns as history cannot feed an envelope back into the model's context.
      setTurns([...next, { role: "assistant", content: reply }]);
      // The console's own record of what it wrote — kept out of `turns` because it is not
      // conversation, and because the model must never be able to author it.
      if (action_result) setLastAction(action_result);
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401) {
        // Expected on the public deployment: the agent is queried with the *caller's*
        // identity, and this surface has no sign-in. Explaining that is the honest answer —
        // a bare "Sign-in required" on a demo page reads as a broken build.
        setGated(true);
        setTurns(turns);
        return;
      }
      // 503 means the serving endpoint is stopped — a normal state for a project that does
      // not leave billing compute running. Say so plainly instead of showing a raw error.
      setError(
        err.status === 503
          ? "The assistant is offline. The queue and approval path are unaffected."
          : err.message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel assistant">
      <h3>Assistant</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
        Searches 1.75M complaint narratives and fleet exposure, and can open a defect signal
        or watch a campaign for tracking. It can <em>propose</em> a service campaign; only
        you can approve one.
      </p>

      {gated && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            The assistant answers under <em>your</em> Databricks identity — it never queries as the
            application — so it needs an authenticated session. This public deployment has no
            sign-in, so it is read-only. The <strong>Evidence</strong> tab needs no session and
            carries the measured result.
          </p>
        </div>
      )}

      <div className="turns">
        {turns.length === 0 && !busy && (
          <div className="prompt-chips">
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              Try one:
            </p>
            {EXAMPLE_PROMPTS.map((p) => (
              <button key={p} className="prompt-chip" onClick={() => send(p)} disabled={busy}>
                {p}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === "user" ? "turn user" : "turn agent"}>
            {t.role === "assistant" ? renderMarkdownLite(t.content) : t.content}
          </div>
        ))}
        {lastAction && lastAction.action === "open_defect_signal" && (
          /* Rendered from the console's committed row, never from the model's text. The
             agent is prompted to say it *requested* a signal; this is the only element on
             the page entitled to say one exists. */
          <div className="action-receipt">
            <strong>Defect signal opened</strong>
            <div>
              <code>{lastAction.signal_id}</code> · {lastAction.component}
              {lastAction.make ? ` · ${lastAction.make}` : ""}
              {lastAction.model ? ` ${lastAction.model}` : ""}
            </div>
            <div className="muted">
              {lastAction.fleet_vehicles.toLocaleString()} fleet vehicle
              {lastAction.fleet_vehicles === 1 ? "" : "s"} match · opened by{" "}
              {lastAction.opened_by} · visible in <strong>Emerging</strong>
            </div>
            {lastAction.match_basis === "MODEL_VARIANT" && (
              /* Only annotated for the variant tier. An exact match needs no caveat, and
                 labelling every count would train the operator to skip the label — which is
                 exactly when it would matter. NHTSA and vPIC spell models differently
                 (`F-250 SD` vs `F-250`), so silently reporting 0 here was the original bug. */
              <div className="muted">
                Matched on a model-name variant — the fleet registry spells this model
                differently from the complaint record.
              </div>
            )}
          </div>
        )}
        {lastAction && lastAction.action === "watch_campaign" && (
          /* Same "committed row, never the model's text" framing as the defect-signal
             receipt above — this is a distinct write action with its own shape, not a
             variant of it (no fleet-exposure count is computed for a watch). */
          <div className="action-receipt">
            <strong>Campaign watched</strong>
            <div>
              <code>{lastAction.campaign_id}</code>
            </div>
            <div className="muted">watched by {lastAction.watched_by}</div>
          </div>
        )}
        {busy && (
          <p className="thinking">
            <i />
            <i />
            <i />
            running tools
          </p>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="ask">
        <input
          value={draft}
          placeholder="Ask about a campaign or a symptom…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
        />
        <button onClick={() => send()} disabled={busy || !draft.trim()}>
          Ask
        </button>
      </div>
    </div>
  );
}
