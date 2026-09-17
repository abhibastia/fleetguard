import { render, renderHook, screen, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Pager, usePagination } from "./pagination";

describe("usePagination", () => {
  it("splits rows into pages of the given size", () => {
    const rows = Array.from({ length: 12 }, (_, i) => i);
    const { result } = renderHook(() => usePagination(rows, 5));
    expect(result.current.pageCount).toBe(3);
    expect(result.current.pageRows).toEqual([0, 1, 2, 3, 4]);
    expect(result.current.totalRows).toBe(12);
  });

  it("defaults to a single page when rows fit within the default page size", () => {
    const rows = [1, 2, 3];
    const { result } = renderHook(() => usePagination(rows));
    expect(result.current.pageCount).toBe(1);
    expect(result.current.pageRows).toEqual(rows);
  });

  it("advances to the requested page via setPage", () => {
    const rows = Array.from({ length: 12 }, (_, i) => i);
    const { result } = renderHook(() => usePagination(rows, 5));
    act(() => result.current.setPage(2));
    expect(result.current.page).toBe(2);
    expect(result.current.pageRows).toEqual([5, 6, 7, 8, 9]);
  });

  it("clamps the current page down when a new (shorter) rows array arrives", () => {
    const rows = Array.from({ length: 12 }, (_, i) => i);
    const { result, rerender } = renderHook(({ rows }) => usePagination(rows, 5), {
      initialProps: { rows },
    });
    act(() => result.current.setPage(3));
    expect(result.current.page).toBe(3);

    // A genuinely new array (new filter/sort result) resets to page 1, per the hook's own
    // "resets on the input array's identity" contract.
    rerender({ rows: [rows[0], rows[1]] });
    expect(result.current.page).toBe(1);
    expect(result.current.pageCount).toBe(1);
  });

  it("never reports a page count below 1, even for an empty array", () => {
    const { result } = renderHook(() => usePagination([] as number[], 5));
    expect(result.current.pageCount).toBe(1);
    expect(result.current.pageRows).toEqual([]);
  });
});

describe("Pager", () => {
  it("renders nothing when there is only one page", () => {
    const { container } = render(
      <Pager page={1} pageCount={1} onChange={() => {}} totalRows={3} pageSize={50} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the current range and page, and disables Prev on page 1", () => {
    render(<Pager page={1} pageCount={3} onChange={() => {}} totalRows={120} pageSize={50} />);
    expect(screen.getByText("1–50 of 120")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
  });

  it("disables Next on the last page and clamps the range to totalRows", () => {
    render(<Pager page={3} pageCount={3} onChange={() => {}} totalRows={120} pageSize={50} />);
    expect(screen.getByText("101–120 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /prev/i })).toBeEnabled();
  });

  it("calls onChange with the adjacent page when Prev/Next is clicked", () => {
    const onChange = vi.fn();
    render(<Pager page={2} pageCount={3} onChange={onChange} totalRows={120} pageSize={50} />);
    screen.getByRole("button", { name: /prev/i }).click();
    expect(onChange).toHaveBeenCalledWith(1);
    screen.getByRole("button", { name: /next/i }).click();
    expect(onChange).toHaveBeenCalledWith(3);
  });
});
