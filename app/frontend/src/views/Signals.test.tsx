import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Signals } from "./Signals";

const { signals } = vi.hoisted(() => ({ signals: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { signals } };
});

function signal(overrides: Record<string, unknown> = {}) {
  return {
    signal_id: "SIG-1",
    series_key: "RAM|2500|BRAKES",
    make: "RAM",
    model: "2500",
    component: "SERVICE BRAKES",
    run_start: "2026-01-01",
    run_end: "2026-06-01",
    run_len: 5,
    max_z: 3.4,
    complaint_count: 40,
    harm_share: 0.1,
    fleet_vehicles: 0,
    match_basis: null,
    is_live: true,
    status: "OPEN",
    source: "DETECTOR",
    opened_by: null,
    ...overrides,
  };
}

function summary(rows: ReturnType<typeof signal>[], overrides: Record<string, unknown> = {}) {
  return {
    total: rows.length,
    live: rows.filter((r) => r.is_live).length,
    fleet_relevant: rows.filter((r) => r.fleet_vehicles > 0).length,
    as_of_month: "2026-08",
    signals: rows,
    ...overrides,
  };
}

describe("Signals", () => {
  beforeEach(() => {
    signals.mockReset();
  });

  it("shows the sign-in message on a 401", async () => {
    signals.mockRejectedValue(new ApiError(401, "nope"));
    render(<Signals />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    signals.mockRejectedValue(new Error("backend exploded"));
    render(<Signals />);
    expect(await screen.findByText("Emerging defects could not be loaded.")).toBeInTheDocument();
  });

  it("shows the no-signals result message, not an error, on an empty page", async () => {
    signals.mockResolvedValue(summary([]));
    render(<Signals />);
    expect(
      await screen.findByText(/No signals match the current search or filter/),
    ).toBeInTheDocument();
  });

  it("renders a DETECTOR signal's LIVE badge and fleet count", async () => {
    signals.mockResolvedValue(summary([signal({ fleet_vehicles: 12, is_live: true })]));
    render(<Signals />);
    await screen.findByText("RAM 2500");
    // The legend row above the table also carries a LIVE/AGENT/VARIANT badge each, so scope
    // to the one with a `title` — only the per-row badge has one.
    expect(document.querySelector(".tag.live[title]")).toHaveTextContent("LIVE");
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders an AGENT-opened signal's AGENT badge and opener, not LIVE/QUIET", async () => {
    signals.mockResolvedValue(
      summary([signal({ source: "AGENT", opened_by: "ops@example.com", max_z: null })]),
    );
    render(<Signals />);
    await screen.findByText("RAM 2500");
    expect(document.querySelector(".tag.agent[title]")).toHaveTextContent("AGENT");
    expect(screen.getByText(/opened by ops@example.com/)).toBeInTheDocument();
    expect(document.querySelector("td .tag.live")).toBeNull();
    expect(document.querySelector("td .tag.quiet")).toBeNull();
  });

  it("tags a MODEL_VARIANT fleet match as VARIANT", async () => {
    signals.mockResolvedValue(
      summary([signal({ fleet_vehicles: 5, match_basis: "MODEL_VARIANT" })]),
    );
    render(<Signals />);
    await screen.findByText("RAM 2500");
    expect(document.querySelector(".tag.quiet[title]")).toHaveTextContent("VARIANT");
  });

  it("re-fetches when the fleet-only checkbox is toggled", async () => {
    signals.mockResolvedValue(summary([signal()]));
    render(<Signals />);
    await screen.findByText("RAM 2500");
    expect(signals).toHaveBeenCalledWith(false);
    screen.getByRole("checkbox").click();
    expect(await vi.waitUntil(() => signals.mock.calls.some((c) => c[0] === true))).toBe(true);
  });
});
