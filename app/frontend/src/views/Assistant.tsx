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
      <h3 style={{ marginTop: 0 }}>Assistant</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
        Searches 1.75M complaint narratives and fleet exposure. It can <em>propose</em> a
        service campaign; only you can approve one.
      </p>

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
        {busy && <p className="muted">Thinking — the agent runs its tools before answering…</p>}
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
