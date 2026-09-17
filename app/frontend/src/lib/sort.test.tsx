import { act, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SortIndicator, useSort } from "./sort";

interface Row {
  name: string;
  age: number | null;
}

const getValue = (row: Row, key: "name" | "age") => row[key];

describe("useSort", () => {
  it("preserves the input order until a sort key is chosen", () => {
    const rows: Row[] = [
      { name: "b", age: 2 },
      { name: "a", age: 1 },
    ];
    const { result } = renderHook(() => useSort(rows, getValue));
    expect(result.current.sorted).toBe(rows);
    expect(result.current.sortKey).toBeNull();
  });

  it("sorts ascending on the first toggle of a column", () => {
    const rows: Row[] = [
      { name: "b", age: 2 },
      { name: "a", age: 1 },
      { name: "c", age: 3 },
    ];
    const { result } = renderHook(() => useSort(rows, getValue));
    act(() => result.current.toggleSort("name"));
    expect(result.current.sorted.map((r) => r.name)).toEqual(["a", "b", "c"]);
    expect(result.current.sortDir).toBe("asc");
  });

  it("flips to descending on a second toggle of the same column", () => {
    const rows: Row[] = [
      { name: "b", age: 2 },
      { name: "a", age: 1 },
    ];
    const { result } = renderHook(() => useSort(rows, getValue));
    act(() => result.current.toggleSort("name"));
    act(() => result.current.toggleSort("name"));
    expect(result.current.sorted.map((r) => r.name)).toEqual(["b", "a"]);
    expect(result.current.sortDir).toBe("desc");
  });

  it("switching to a different column resets direction to ascending", () => {
    const rows: Row[] = [
      { name: "b", age: 2 },
      { name: "a", age: 1 },
    ];
    const { result } = renderHook(() => useSort(rows, getValue));
    act(() => result.current.toggleSort("name"));
    act(() => result.current.toggleSort("name")); // now desc on name
    act(() => result.current.toggleSort("age")); // switch column
    expect(result.current.sortKey).toBe("age");
    expect(result.current.sortDir).toBe("asc");
    expect(result.current.sorted.map((r) => r.age)).toEqual([1, 2]);
  });

  it("sorts null values to the end regardless of direction", () => {
    const rows: Row[] = [
      { name: "a", age: null },
      { name: "b", age: 5 },
      { name: "c", age: 1 },
    ];
    const { result } = renderHook(() => useSort(rows, getValue));
    act(() => result.current.toggleSort("age"));
    expect(result.current.sorted.map((r) => r.name)).toEqual(["c", "b", "a"]);
  });
});

describe("SortIndicator", () => {
  it("renders a dimmed double-arrow when the column is not the active sort", () => {
    render(<SortIndicator columnKey="name" sortKey={null} sortDir="asc" />);
    expect(screen.getByText("⇅")).not.toHaveClass("is-active");
  });

  it("renders an up arrow, marked active, when ascending on this column", () => {
    render(<SortIndicator columnKey="name" sortKey="name" sortDir="asc" />);
    expect(screen.getByText("▲")).toHaveClass("is-active");
  });

  it("renders a down arrow when descending on this column", () => {
    render(<SortIndicator columnKey="name" sortKey="name" sortDir="desc" />);
    expect(screen.getByText("▼")).toBeInTheDocument();
  });
});
