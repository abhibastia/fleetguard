/**
 * The overdue comparison, pinned.
 *
 * This logic shipped wrong: it compared `new Date(due_date)` (ISO date-only → **UTC** midnight)
 * against `new Date(new Date().toDateString())` (→ **local** midnight). For any viewer behind
 * UTC the two differ by the offset, so a work order due *today* sorted as overdue. It was
 * correct in the author's timezone (Europe/Berlin, UTC+2) and wrong in the workspace's own
 * region (America/Los_Angeles, UTC-7) — the kind of defect that cannot be found by looking at
 * your own screen.
 *
 * `today` is injected throughout so these assertions depend on neither the clock nor the
 * runner's timezone. A test that passed only in CI's timezone would repeat the original
 * mistake in a new place.
 */

import { describe, expect, it, vi } from "vitest";
import type { WorkOrder } from "./api";
import { isOverdue, todayLocalISO } from "./dates";

const wo = (over: Partial<WorkOrder> = {}): WorkOrder =>
  ({ wo_id: "WO-1", due_date: "2026-09-08", status: "OPEN", ...over }) as WorkOrder;

describe("isOverdue", () => {
  it("does not flag a work order due today", () => {
    // The regression. Under the old implementation this returned true for every viewer
    // west of Greenwich, inflating the overdue count on both the list and its stat tile.
    expect(isOverdue(wo({ due_date: "2026-09-08" }), "2026-09-08")).toBe(false);
  });

  it("flags a work order due yesterday", () => {
    expect(isOverdue(wo({ due_date: "2026-09-07" }), "2026-09-08")).toBe(true);
  });

  it("does not flag a work order due tomorrow", () => {
    expect(isOverdue(wo({ due_date: "2026-09-09" }), "2026-09-08")).toBe(false);
  });

  it("orders correctly across month and year boundaries", () => {
    // Guards the choice of string comparison: ISO YYYY-MM-DD sorts lexicographically in date
    // order, but only because every field is zero-padded. A format change that dropped the
    // padding would break here rather than silently mis-sorting December against February.
    expect(isOverdue(wo({ due_date: "2025-12-31" }), "2026-01-01")).toBe(true);
    expect(isOverdue(wo({ due_date: "2026-02-01" }), "2026-01-31")).toBe(false);
    expect(isOverdue(wo({ due_date: "2026-01-02" }), "2026-01-10")).toBe(true);
  });

  it("never flags completed or cancelled work, however old", () => {
    // Closed work is not outstanding work. Without this the overdue tile would climb forever
    // as a campaign was worked through, which is the opposite of the intended signal.
    expect(isOverdue(wo({ due_date: "2020-01-01", status: "COMPLETED" }), "2026-09-08")).toBe(
      false,
    );
    expect(isOverdue(wo({ due_date: "2020-01-01", status: "CANCELLED" }), "2026-09-08")).toBe(
      false,
    );
  });

  it("treats a missing due date as not overdue", () => {
    // `due_date` is nullable in Postgres. "No deadline" must not read as "past deadline".
    expect(isOverdue(wo({ due_date: null }), "2026-09-08")).toBe(false);
  });

  it("defaults to today when no date is supplied", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date(2026, 8, 8, 12, 0, 0)); // local noon, 8 Sep 2026
      expect(isOverdue(wo({ due_date: "2026-09-07" }))).toBe(true);
      expect(isOverdue(wo({ due_date: "2026-09-08" }))).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("todayLocalISO", () => {
  it("uses the local calendar date, not the UTC one", () => {
    vi.useFakeTimers();
    try {
      // Local 23:30 on 8 Sep. Anywhere east of Greenwich this is already 9 Sep in UTC, so
      // `toISOString().slice(0, 10)` — the tempting one-liner — would return tomorrow.
      vi.setSystemTime(new Date(2026, 8, 8, 23, 30, 0));
      expect(todayLocalISO()).toBe("2026-09-08");
    } finally {
      vi.useRealTimers();
    }
  });

  it("zero-pads month and day", () => {
    // Unpadded output would still look plausible ("2026-1-5") while breaking every string
    // comparison above.
    expect(todayLocalISO(new Date(2026, 0, 5, 9, 0, 0))).toBe("2026-01-05");
  });
});
