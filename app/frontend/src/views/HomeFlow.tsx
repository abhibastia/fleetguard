/**
 * The six-step flow, drawn once on Home.
 *
 * **Why a diagram at all.** FleetGuard is a pipeline, and the landing page described it only
 * in prose — a visitor could read the whole hero and still not know that a recall becomes
 * dispatched work orders, or that a human sits in the middle of that. One picture carries the
 * shape faster than the paragraph did.
 *
 * **Why inline SVG rather than an image or a library.** The console makes zero external
 * requests and that is a property worth keeping (it is why the generated stylesheet reviewed
 * on 2026-09-23 was rejected — it pulled a Google font). Inline also means the drawing
 * inherits the theme: every colour here is an existing custom property or `currentColor`, so
 * there is no second palette to keep in sync with `styles.css`, and light/dark both work with
 * no extra rules.
 *
 * **Accessibility.** The drawing is `aria-hidden`; the ordered list underneath is the real
 * content. It is visually hidden on screen and restored by `@media print`, where the SVG is
 * hidden instead — a diagram drawn in themed strokes does not survive a white page, so the
 * list is not a fallback there, it is the version print actually gets.
 *
 * The first version gave the SVG `role="img"` with a `<title>`/`<desc>` pair *and* kept the
 * list, which meant a screen reader announced the same six steps twice. One representation,
 * not two. Forced-colors keeps the SVG (checked against a real render — it stays legible) and
 * leaves the list hidden, for the same no-duplication reason.
 *
 * Static by design — nothing animates, so there is nothing for `prefers-reduced-motion` to
 * have to suppress.
 */

const STEPS: { label: string; sub: string }[] = [
  { label: "NHTSA", sub: "complaints · recalls" },
  { label: "Detect", sub: "signal or campaign" },
  { label: "Scope", sub: "against VIN roster" },
  { label: "Approve", sub: "human gate" },
  { label: "Dispatch", sub: "work orders" },
  { label: "Audit", sub: "append-only" },
];

const BOX_W = 132;
const BOX_H = 52;
const GAP = 26;
const H = 78;
const W = STEPS.length * BOX_W + (STEPS.length - 1) * GAP;

export function FlowDiagram() {
  return (
    <div className="home-flow">
      <svg
        className="home-flow-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        // The list below is the accessible representation, so the drawing is hidden rather
        // than described. It previously carried `role="img"` with a title/desc AND sat beside
        // that list, which meant a screen reader announced the same six steps twice.
        aria-hidden="true"
        focusable="false"
      >
        {STEPS.map((step, i) => {
          const x = i * (BOX_W + GAP);
          const my = (H - BOX_H) / 2;
          // The human gate is the one step a reader should notice — it is the claim that an
          // LLM cannot dispatch work against a fleet. Accented rather than annotated.
          const isGate = step.label === "Approve";
          return (
            <g key={step.label}>
              <rect
                x={x}
                y={my}
                width={BOX_W}
                height={BOX_H}
                rx="8"
                className={isGate ? "flow-box flow-box-gate" : "flow-box"}
              />
              <text x={x + BOX_W / 2} y={my + 21} className="flow-label">
                {step.label}
              </text>
              <text x={x + BOX_W / 2} y={my + 37} className="flow-sub">
                {step.sub}
              </text>
              {i < STEPS.length - 1 && (
                <path
                  d={`M${x + BOX_W + 5} ${H / 2} H${x + BOX_W + GAP - 8}`}
                  className="flow-arrow"
                  markerEnd="url(#flow-head)"
                />
              )}
            </g>
          );
        })}

        <defs>
          <marker
            id="flow-head"
            viewBox="0 0 8 8"
            refX="6"
            refY="4"
            markerWidth="5"
            markerHeight="5"
            orient="auto"
          >
            <path d="M0 1 L7 4 L0 7 z" className="flow-arrow-head" />
          </marker>
        </defs>
      </svg>

      {/* The real content for screen readers, print and forced-colors — see the module note. */}
      <ol className="home-flow-text">
        {STEPS.map((s) => (
          <li key={s.label}>
            <b>{s.label}</b> — {s.sub}
          </li>
        ))}
      </ol>
    </div>
  );
}
