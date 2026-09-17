import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Trends } from "./Trends";

const { recallTrend } = vi.hoisted(() => ({ recallTrend: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { recallTrend } };
});

describe("Trends", () => {
  beforeEach(() => {
    recallTrend.mockReset();
  });

  it("shows the sign-in message on a 401", async () => {
    recallTrend.mockRejectedValue(new ApiError(401, "nope"));
    render(<Trends />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure with no cached data", async () => {
    recallTrend.mockRejectedValue(new Error("backend exploded"));
    render(<Trends />);
    expect(await screen.findByText("Recall trend could not be loaded.")).toBeInTheDocument();
  });

  it("shows the empty-registry message when no points are returned", async () => {
    recallTrend.mockResolvedValue({ points: [], latest_issued_at: null });
    render(<Trends />);
    expect(
      await screen.findByText(/No dated recall campaigns loaded yet/),
    ).toBeInTheDocument();
  });

  it("renders yearly stats from real data", async () => {
    recallTrend.mockResolvedValue({
      points: [
        { year: 2024, campaigns: 10, urgent_campaigns: 1, vehicles_exposed: 500 },
        { year: 2025, campaigns: 20, urgent_campaigns: 3, vehicles_exposed: 900 },
      ],
      latest_issued_at: "2025-12-31",
    });
    render(<Trends />);
    expect(await screen.findByText("2")).toBeInTheDocument(); // years with recalls
    expect(screen.getByText("30")).toBeInTheDocument(); // total campaigns
    expect(screen.getByText("4")).toBeInTheDocument(); // total urgent
  });

  it("marks the final year partial when the latest date is not year-end", async () => {
    recallTrend.mockResolvedValue({
      points: [{ year: 2026, campaigns: 5, urgent_campaigns: 0, vehicles_exposed: 100 }],
      latest_issued_at: "2026-06-15",
    });
    render(<Trends />);
    expect((await screen.findAllByText(/not year-end/)).length).toBeGreaterThan(0);
  });
});
