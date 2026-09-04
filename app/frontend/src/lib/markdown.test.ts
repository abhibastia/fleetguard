import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { renderMarkdownLite } from "./markdown";

// react-dom/server ships with react-dom (already a dependency) — no new package needed just
// to assert on the rendered structure of a hand-rolled parser.
function html(text: string): string {
  return renderToStaticMarkup(renderMarkdownLite(text));
}

describe("renderMarkdownLite", () => {
  it("passes plain text through unchanged", () => {
    expect(html("just plain text")).toContain("just plain text");
  });

  it("renders a bold span", () => {
    const out = html("this has **bold** text");
    expect(out).toContain("<strong>bold</strong>");
    expect(out).not.toContain("**");
  });

  it("renders multiple bold spans on one line", () => {
    const out = html("**25 vehicles** across **22 depots**");
    expect(out).toContain("<strong>25 vehicles</strong>");
    expect(out).toContain("<strong>22 depots</strong>");
  });

  it("groups consecutive bullet lines into one list", () => {
    const out = html("- first\n- second\n- third");
    expect(out).toMatch(/<ul[^>]*>.*<li>first<\/li>.*<li>second<\/li>.*<li>third<\/li>.*<\/ul>/s);
  });

  it("supports bold text inside bullet items", () => {
    const out = html("- **25 vehicles** across **22 depots**");
    expect(out).toContain("<li><strong>25 vehicles</strong> across <strong>22 depots</strong></li>");
  });

  it("handles a mix of prose and a bullet list, matching the observed live agent reply", () => {
    const text =
      "For campaign **17V629000**, your fleet exposure is:\n\n" +
      "- **25 vehicles** across **22 depots** — all **EXACT** matches.\n\n" +
      "No further confirmation is needed.";
    const out = html(text);
    expect(out).toContain("<strong>17V629000</strong>");
    expect(out).toContain("<li><strong>25 vehicles</strong> across <strong>22 depots</strong>");
    expect(out).toContain("No further confirmation is needed.");
  });

  it("treats * bullets the same as - bullets", () => {
    const out = html("* one\n* two");
    expect(out).toMatch(/<li>one<\/li>.*<li>two<\/li>/s);
  });

  it("does not treat a lone asterisk mid-sentence as a bullet", () => {
    const out = html("5 * 3 = 15");
    expect(html("5 * 3 = 15")).not.toContain("<li>");
    expect(out).toContain("5 * 3 = 15");
  });
});
