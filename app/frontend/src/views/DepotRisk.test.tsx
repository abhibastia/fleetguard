import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { DepotRisk } from "./DepotRisk";

const { depotRisk } = vi.hoisted(() => ({ depotRisk: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { depotRisk } };
});

function depot(overrides: Record<string, unknown> = {}) {
  return {
    depot_id: "DEP-001",
    depot_name: "Denver South Depot",
    region: "WEST",
    city: "Denver",
    state: "CO",
    fleet_size: 100,
    urgent_vehicles_exposed: 0,
    total_vehicles_exposed: 10,
    distinct_campaigns: 3,
    outstanding_work_orders: 2,
    overdue_work_orders: 0,
    ...overrides,
  };
}

describe("DepotRisk", () => {
  beforeEach(() => {
    depotRisk.mockReset();
  });

  it("shows the sign-in message on a 401", async () => {
    depotRisk.mockRejectedValue(new ApiError(401, "nope"));
    render(<DepotRisk />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    depotRisk.mockRejectedValue(new Error("backend exploded"));
    render(<DepotRisk />);
    expect(await screen.findByText("Depot risk could not be loaded.")).toBeInTheDocument();
  });

  it("shows the empty-registry message when no depots are returned", async () => {
    depotRisk.mockResolvedValue([]);
    render(<DepotRisk />);
    expect(await screen.findByText(/No depot data loaded yet/)).toBeInTheDocument();
  });

  it("renders depot rows and marks a high-risk depot by its urgent-exposure ratio", async () => {
    depotRisk.mockResolvedValue([
      depot({ depot_id: "DEP-001", fleet_size: 100, urgent_vehicles_exposed: 10 }), // 10% -> high
      depot({ depot_id: "DEP-002", depot_name: "Austin North Depot", urgent_vehicles_exposed: 0 }),
    ]);
    render(<DepotRisk />);
    expect(await screen.findByText("Denver South Depot")).toBeInTheDocument();
    expect(screen.getByText("Austin North Depot")).toBeInTheDocument();
    // High-risk stat counts exactly the one depot at >=5% urgent share.
    const highRiskStat = screen.getByText("High risk (≥5% of fleet urgent)").closest(".stat");
    expect(highRiskStat).toHaveTextContent("1");
  });

  it("shows the no-match message when a search excludes every depot", async () => {
    depotRisk.mockResolvedValue([depot({ depot_id: "DEP-001" })]);
    render(<DepotRisk />);
    await screen.findByText("Denver South Depot");
    await userEvent.type(screen.getByLabelText("Search depots"), "nomatch");
    expect(
      await screen.findByText("No depots match the current search or filters."),
    ).toBeInTheDocument();
  });
});
