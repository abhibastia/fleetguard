import { useEffect, useState } from "react";
import type { ApprovalResult } from "../lib/api";

/**
 * What actually happened when a campaign was approved — a vertical timeline, not a flat
 * instant reveal.
 *
 * All three stages are real, already-committed facts by the time `ApprovalResult` exists (one
 * atomic transaction: service campaign row, N work order rows, one audit row, committed
 * together) — there is no real elapsed time between them to show genuine per-stage backend
 * progress for. The staggered reveal below is a fixed, ~1.1s-total cosmetic transition, not a
 * progress bar: deliberate enough to feel substantial, but short enough that it still reads as
 * a reveal rather than a multi-second "still working" wait, which would misrepresent what's
 * actually happening. Stretching this into several seconds per stage to "look like more work
 * happened" would be exactly the "simulated success" this project's own history (I-050) treats
 * as a hard rule to avoid — raised and deliberately kept short for that reason (2026-09-04).
 *
 * Unity Catalog propagation via Change Data Feed is real and measured (7-16s), but this console
 * has no way to confirm it landed today, and "Unity Catalog propagation" is Databricks-internal
 * vocabulary the operator persona (a fleet safety team, per Assistant.tsx) has no reason to
 * know. It stays out of the stage list entirely and appears as one small, plainly-worded,
 * honestly-hedged footnote instead.
 */
const FIRST_DELAY_MS = 100;
const STAGE_DELAY_MS = 350;

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" width="13" height="13">
      <path
        d="M5 12.5 9.5 17 19 7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TimelineStage({
  done,
  isLast,
  label,
  detail,
}: {
  done: boolean;
  isLast: boolean;
  label: string;
  detail: string;
}) {
  return (
    <div className="timeline-item">
      <div className="timeline-dot-col">
        <span className={done ? "timeline-dot is-done" : "timeline-dot"}>
          {done && <CheckIcon />}
        </span>
        {!isLast && <span className={done ? "timeline-line is-done" : "timeline-line"} />}
      </div>
      <div className={done ? "timeline-content is-visible" : "timeline-content"}>
        <div className="step-label">{label}</div>
        <div className="step-detail">{detail}</div>
      </div>
    </div>
  );
}

export function ApprovalConfirmation({ result }: { result: ApprovalResult }) {
  const [revealed, setRevealed] = useState(0);

  useEffect(() => {
    // Fixed, fast client-side stagger — not tied to any backend wait. The transaction that
    // produced `result` already fully completed before this component ever mounts; this is
    // purely a reveal cadence for legibility.
    const timers = [0, 1, 2].map((i) =>
      setTimeout(() => setRevealed((r) => Math.max(r, i + 1)), FIRST_DELAY_MS + i * STAGE_DELAY_MS),
    );
    return () => timers.forEach(clearTimeout);
  }, []);

  const stages = [
    {
      label: "Campaign record created",
      detail: `${result.service_campaign_id}, approved by ${result.approved_by}`,
    },
    {
      label: `${result.work_orders_created.toLocaleString()} work orders created`,
      detail: `Due ${result.due_date}`,
    },
    {
      label: "Audit log entry recorded",
      detail: `Attributed to ${result.approved_by}`,
    },
  ];

  return (
    <div>
      <div className="success" style={{ marginBottom: 12 }}>
        <strong>{result.service_campaign_id}</strong> launched.
      </div>
      <div className="timeline">
        {stages.map((s, i) => (
          <TimelineStage key={i} done={i < revealed} isLast={i === stages.length - 1} {...s} />
        ))}
      </div>
      <p className="footnote" style={{ marginTop: 10 }}>
        Also syncs to company-wide reporting within about 15 seconds.
      </p>
    </div>
  );
}
