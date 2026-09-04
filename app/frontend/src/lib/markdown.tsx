import type { ReactNode } from "react";

/**
 * A minimal Markdown renderer for the chat panel — bold spans and bullet lists, because that
 * is everything the deployed agent has ever actually been observed to emit (verified against
 * a live response: "- **25 vehicles** across **22 depots**"). Anything else passes through as
 * plain text.
 *
 * Hand-rolled rather than a dependency: this project has zero non-React runtime dependencies
 * today (matching the hand-rolled SVG icons in App.tsx), and a ~40-line parser covering the
 * observed cases is proportionate to the actual need. Reach for `react-markdown` instead if
 * the agent's output grows real Markdown (tables, links, code fences) beyond this.
 */

const BOLD = /\*\*(.+?)\*\*/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  BOLD.lastIndex = 0;
  while ((match = BOLD.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push(<strong key={`${keyPrefix}-b${i++}`}>{match[1]}</strong>);
    last = BOLD.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function isBulletLine(line: string): boolean {
  return /^[-*]\s+/.test(line.trim());
}

function stripBullet(line: string): string {
  return line.trim().replace(/^[-*]\s+/, "");
}

export function renderMarkdownLite(text: string): ReactNode {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    if (isBulletLine(lines[i])) {
      const items: string[] = [];
      while (i < lines.length && isBulletLine(lines[i])) {
        items.push(stripBullet(lines[i]));
        i++;
      }
      blocks.push(
        <ul key={`ul-${key++}`} style={{ margin: "6px 0", paddingLeft: 18 }}>
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `ul-${key}-${idx}`)}</li>
          ))}
        </ul>,
      );
    } else {
      const start = i;
      const textLines: string[] = [];
      while (i < lines.length && !isBulletLine(lines[i])) {
        textLines.push(lines[i]);
        i++;
      }
      const content: ReactNode[] = [];
      textLines.forEach((line, idx) => {
        if (idx > 0) content.push(<br key={`br-${start}-${idx}`} />);
        content.push(...renderInline(line, `p-${start}-${idx}`));
      });
      blocks.push(<span key={`p-${key++}`}>{content}</span>);
    }
  }

  return blocks;
}
