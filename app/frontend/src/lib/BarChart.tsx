import { useId, useRef, useState } from "react";

/**
 * A minimal hand-rolled bar chart — this app's first chart, and deliberately not a new
 * dependency. Matches the precedent set by `markdown.tsx` (a small hand-rolled parser instead
 * of pulling in `react-markdown`) and the hand-rolled SVG icons in `App.tsx`: the actual need
 * here is a handful of bars with an optional highlighted sub-segment, not a general-purpose
 * charting library.
 *
 * Everything is laid out in one fixed logical coordinate space (`VIEW_W` × `VIEW_H`) and left
 * to scale via CSS (`width: 100%`), rather than mixing percentage and pixel units in the same
 * SVG — that mismatch is an easy way to end up with bars that don't actually line up with
 * their labels at different container widths.
 *
 * The tooltip is a real positioned `<div>` driven by mouse events, not a native SVG `<title>`
 * child — the latter looks correct in markup but is unreliable in practice (inconsistent
 * hover-delay and support across browsers, and it can't be captured in a screenshot even when
 * it does work, which is how the first version of this file was found to be broken).
 */

// 5:1, chosen to match this app's actual panel width (~1200-1460px content area, see
// `main { max-width: 1520px }`) against a taller 240px chart — the previous 600×200 (3:1) was
// far narrower than any real container, so the default `preserveAspectRatio="xMidYMid meet"`
// letterboxed the chart down to a ~500px-wide island in the middle of a much wider panel.
// `preserveAspectRatio="none"` was tried first and reverted: it fixes the letterboxing but
// non-uniformly scales the SVG <text> labels along with the bars, visibly warping the year
// and value labels. Matching the logical aspect ratio to the real container is the fix that
// doesn't distort text.
const VIEW_W = 1200;
const VIEW_H = 240;
const PADDING_BOTTOM = 22;
const PADDING_TOP = 16; // room for the value label above the tallest bar
// A highlight segment proportional to its true ratio can round to under a pixel for small
// counts (e.g. 1 urgent campaign out of 44) — invisible at exactly the moment it matters most.
// Floored to a minimum, capped at the bar's own height so it never overflows above the bar.
const MIN_HIGHLIGHT_H = 4;

export interface BarChartDatum {
  label: string;
  value: number;
  /** A sub-portion of `value` to render in the highlight color, e.g. "of which N were urgent." */
  highlightValue?: number;
  /** Rendered with a dashed outline and muted fill — data that isn't a complete period yet. */
  partial?: boolean;
  title: string;
}

export function BarChart({ data }: { data: BarChartDatum[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ title: string; x: number; y: number } | null>(null);
  // Trends.tsx renders two charts on one page, and SVG `url(#id)` refs are document-global —
  // without a unique id per instance, the second chart's gradient would silently resolve to
  // the first's (or vice versa) depending on paint order.
  const gradientId = `bar-fill-${useId().replace(/:/g, "")}`;

  const max = Math.max(1, ...data.map((d) => d.value));
  const plotHeight = VIEW_H - PADDING_BOTTOM - PADDING_TOP;
  const slot = VIEW_W / Math.max(1, data.length);
  const barWidth = slot * 0.62;

  function showTooltip(e: React.MouseEvent, title: string) {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHover({ title, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }

  return (
    <div ref={wrapRef} className="bar-chart-wrap">
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="bar-chart"
        role="img"
        aria-label={data.map((d) => d.title).join("; ")}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.75" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.3" />
          </linearGradient>
        </defs>
        {data.map((d, i) => {
          const barH = (d.value / max) * plotHeight;
          const rawHighlightH = d.highlightValue ? (d.highlightValue / max) * plotHeight : 0;
          const highlightH =
            rawHighlightH > 0 ? Math.min(Math.max(rawHighlightH, MIN_HIGHLIGHT_H), barH) : 0;
          const x = i * slot + (slot - barWidth) / 2;
          const y = PADDING_TOP + plotHeight - barH;
          return (
            <g
              key={d.label}
              onMouseMove={(e) => showTooltip(e, d.title)}
              onMouseLeave={() => setHover(null)}
            >
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={Math.max(barH, 1.5)}
                className={`bar-base${d.partial ? " bar-partial" : ""}`}
                // Complete bars get the gradient; a partial (incomplete) period keeps its own
                // flat, dashed, muted look instead — that distinction is a real signal ("this
                // year isn't finished yet"), not just decoration, so it isn't overridden here.
                fill={d.partial ? undefined : `url(#${gradientId})`}
              />
              {highlightH > 0 && (
                <rect
                  x={x}
                  y={PADDING_TOP + plotHeight - highlightH}
                  width={barWidth}
                  height={highlightH}
                  className="bar-highlight"
                />
              )}
              {/* Invisible full-column hit area, kept separate from the bar itself, so hovering
                  the empty space above a short bar still shows its tooltip - a real usability
                  problem for the smallest bars in this data (some years have very few campaigns). */}
              <rect
                x={x}
                y={0}
                width={barWidth}
                height={VIEW_H - PADDING_BOTTOM}
                fill="transparent"
                style={{ pointerEvents: "all" }}
              />
              <text x={x + barWidth / 2} y={y - 5} textAnchor="middle" className="bar-value">
                {d.value.toLocaleString()}
                {d.partial ? "*" : ""}
              </text>
              <text
                x={x + barWidth / 2}
                y={VIEW_H - 6}
                textAnchor="middle"
                className="bar-tick"
              >
                {d.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="bar-chart-tooltip" style={{ left: hover.x, top: hover.y }}>
          {hover.title}
        </div>
      )}
    </div>
  );
}
