import { useState } from "react";
import { api, ApiError, type ChatTurn } from "../lib/api";

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

  async function send() {
    const question = draft.trim();
    if (!question || busy) return;

    const next: ChatTurn[] = [...turns, { role: "user", content: question }];
    setTurns(next);
    setDraft("");
    setBusy(true);
    setError(null);

    try {
      const { reply } = await api.chat(next);
      setTurns([...next, { role: "assistant", content: reply }]);
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
        Searches 1.75M complaint narratives and fleet exposure. It can <em>propose</em> a service
        campaign; only you can approve one.
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
          <p className="muted" style={{ fontSize: 13 }}>
            Try: <em>“Which fleet vehicles does recall 17V629000 affect?”</em>
          </p>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === "user" ? "turn user" : "turn agent"}>
            {t.content}
          </div>
        ))}
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
        <button onClick={send} disabled={busy || !draft.trim()}>
          Ask
        </button>
      </div>
    </div>
  );
}
