import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApprovalResult } from "../lib/api";
import { ApprovalConfirmation } from "./ApprovalConfirmation";

const result: ApprovalResult = {
  service_campaign_id: "SC-42",
  campaign_id: "24V001",
  work_orders_created: 12,
  approved_by: "ops@example.com",
  due_date: "2026-10-01",
};

describe("ApprovalConfirmation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the launched banner with the service campaign id immediately", () => {
    render(<ApprovalConfirmation result={result} />);
    expect(screen.getByText("SC-42", { selector: "strong" })).toBeInTheDocument();
    expect(document.querySelector(".success")).toHaveTextContent("SC-42 launched.");
  });

  it("reveals all three timeline stages once the stagger timers complete", () => {
    render(<ApprovalConfirmation result={result} />);
    act(() => {
      vi.runAllTimers();
    });
    expect(screen.getByText("Campaign record created")).toBeInTheDocument();
    expect(screen.getByText("12 work orders created")).toBeInTheDocument();
    expect(screen.getByText("Audit log entry recorded")).toBeInTheDocument();
    expect(screen.getByText(`${result.service_campaign_id}, approved by ${result.approved_by}`)).toBeInTheDocument();
  });

  it("has not revealed the later stages before their delay has elapsed", () => {
    render(<ApprovalConfirmation result={result} />);
    // Only the first stage's delay has passed — its content becomes visible via a CSS class,
    // not conditional rendering, so assert on the dot's "done" state instead.
    act(() => {
      vi.advanceTimersByTime(150);
    });
    const dots = document.querySelectorAll(".timeline-dot");
    expect(dots[0]).toHaveClass("is-done");
    expect(dots[1]).not.toHaveClass("is-done");
    expect(dots[2]).not.toHaveClass("is-done");
  });
});
