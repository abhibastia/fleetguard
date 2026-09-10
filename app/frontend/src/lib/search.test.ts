import { describe, expect, it } from "vitest";
import { filterRows } from "./search";

type Row = { id: string; depot: string; make: string };

const ROWS: Row[] = [
  { id: "WO-1", depot: "DEP-042", make: "FORD" },
  { id: "WO-2", depot: "DEP-042", make: "RAM" },
  { id: "WO-3", depot: "DEP-007", make: "FORD" },
];

const text = (r: Row) => `${r.id} ${r.depot} ${r.make}`;
const ids = (rows: Row[]) => rows.map((r) => r.id);

describe("filterRows", () => {
  it("returns every row when the query is empty or whitespace", () => {
    expect(filterRows(ROWS, text, "")).toHaveLength(3);
    expect(filterRows(ROWS, text, "   ")).toHaveLength(3);
  });

  it("matches case-insensitively on any exposed field", () => {
    expect(ids(filterRows(ROWS, text, "ford"))).toEqual(["WO-1", "WO-3"]);
    expect(ids(filterRows(ROWS, text, "DEP-007"))).toEqual(["WO-3"]);
  });

  it("ANDs multiple terms rather than ORing them", () => {
    // The distinction that matters. An OR would return all three rows here and quietly teach
    // the user that typing more does nothing.
    expect(ids(filterRows(ROWS, text, "ford dep-042"))).toEqual(["WO-1"]);
  });

  it("ignores surrounding and repeated whitespace between terms", () => {
    expect(ids(filterRows(ROWS, text, "   ford    dep-042  "))).toEqual(["WO-1"]);
  });

  it("returns nothing on no match, rather than falling back to everything", () => {
    // A filter that silently shows all rows when nothing matches is worse than an empty
    // result: it looks like the search was applied and the term genuinely is present.
    expect(filterRows(ROWS, text, "kenworth")).toHaveLength(0);
  });

  it("preserves the order it was given, so it composes with useSort", () => {
    const reversed = [...ROWS].reverse();
    expect(ids(filterRows(reversed, text, "ford"))).toEqual(["WO-3", "WO-1"]);
  });

  it("does not mutate the input array", () => {
    const copy = [...ROWS];
    filterRows(ROWS, text, "ford");
    expect(ROWS).toEqual(copy);
  });
});
