import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { WorkOrders } from "./WorkOrders";

const { workOrders, technicians, serviceCampaigns, updateWorkOrder } = vi.hoisted(() => ({
  workOrders: vi.fn(),
  technicians: vi.fn(),
  serviceCampaigns: vi.fn(),
  updateWorkOrder: vi.fn(),
}));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { workOrders, technicians, serviceCampaigns, updateWorkOrder } };
});

function order(overrides: Record<string, unknown> = {}) {
  return {
    wo_id: "WO-1",
    service_campaign_id: "SC-1",
    vin: "1FTFW1ET",
    depot_id: "DEP-001",
    assigned_to: null,
    assigned_to_name: null,
    due_date: "2099-01-01",
    status: "OPEN",
    created_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    actual_cost: null,
    ...overrides,
  };
}

describe("WorkOrders", () => {
  beforeEach(() => {
    workOrders.mockReset();
    technicians.mockReset().mockResolvedValue([]);
    serviceCampaigns.mockReset().mockResolvedValue([]);
    updateWorkOrder.mockReset();
  });

  it("shows the sign-in message on a 401", async () => {
    workOrders.mockRejectedValue(new ApiError(401, "nope"));
    render(<WorkOrders />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    workOrders.mockRejectedValue(new Error("backend exploded"));
    render(<WorkOrders />);
    expect(await screen.findByText("Work orders could not be loaded.")).toBeInTheDocument();
  });

  it("shows the empty-state message when there are no work orders", async () => {
    workOrders.mockResolvedValue([]);
    render(<WorkOrders />);
    expect(await screen.findByText(/No work orders yet/)).toBeInTheDocument();
  });

  it("renders order rows and the OPEN/overdue KPI counts from real data", async () => {
    workOrders.mockResolvedValue([
      order({ wo_id: "WO-1", status: "OPEN", due_date: "2000-01-01" }), // overdue
      order({ wo_id: "WO-2", status: "COMPLETED", due_date: "2099-01-01" }),
    ]);
    render(<WorkOrders />);
    expect(await screen.findByText("WO-1")).toBeInTheDocument();
    expect(screen.getByText("WO-2")).toBeInTheDocument();
    expect(screen.getByText("OVERDUE")).toBeInTheDocument();
  });

  it("shows a filter banner and clears it via the Clear filter button", async () => {
    const onClearFilter = vi.fn();
    workOrders.mockResolvedValue([order()]);
    render(<WorkOrders serviceCampaignId="SC-1" onClearFilter={onClearFilter} />);
    expect(await screen.findByText("SC-1", { selector: "strong" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Clear filter/ }));
    expect(onClearFilter).toHaveBeenCalled();
  });

  it("changes a work order's status via the select and calls api.updateWorkOrder", async () => {
    workOrders.mockResolvedValue([order({ wo_id: "WO-1", status: "OPEN" })]);
    updateWorkOrder.mockResolvedValue(order({ wo_id: "WO-1", status: "IN_PROGRESS" }));
    render(<WorkOrders />);
    await screen.findByText("WO-1");
    const statusSelect = screen.getAllByRole("combobox").find(
      (el) => (el as HTMLSelectElement).value === "OPEN",
    ) as HTMLSelectElement;
    await userEvent.selectOptions(statusSelect, "IN_PROGRESS");
    expect(updateWorkOrder).toHaveBeenCalledWith("WO-1", { status: "IN_PROGRESS" });
  });

  it("shows the update error inline when a status change fails", async () => {
    workOrders.mockResolvedValue([order({ wo_id: "WO-1", status: "OPEN" })]);
    updateWorkOrder.mockRejectedValue(new ApiError(403, "not an approver"));
    render(<WorkOrders />);
    await screen.findByText("WO-1");
    const statusSelect = screen.getAllByRole("combobox").find(
      (el) => (el as HTMLSelectElement).value === "OPEN",
    ) as HTMLSelectElement;
    await userEvent.selectOptions(statusSelect, "COMPLETED");
    expect(await screen.findByText("not an approver")).toBeInTheDocument();
  });

  it("commits a cost edit on blur, sending the parsed number", async () => {
    workOrders.mockResolvedValue([order({ wo_id: "WO-1", actual_cost: null })]);
    updateWorkOrder.mockResolvedValue(order({ wo_id: "WO-1", actual_cost: 120.5 }));
    render(<WorkOrders />);
    const costInput = await screen.findByPlaceholderText("not logged");
    await userEvent.type(costInput, "120.50");
    await userEvent.tab(); // triggers onBlur -> commitCost
    expect(updateWorkOrder).toHaveBeenCalledWith("WO-1", { actual_cost: 120.5 });
  });

  it("rejects a non-numeric cost without calling the API", async () => {
    workOrders.mockResolvedValue([order({ wo_id: "WO-1", actual_cost: null })]);
    render(<WorkOrders />);
    const costInput = await screen.findByPlaceholderText("not logged");
    await userEvent.type(costInput, "not-a-number");
    await userEvent.tab();
    expect(await screen.findByText(/is not a valid cost/)).toBeInTheDocument();
    expect(updateWorkOrder).not.toHaveBeenCalled();
  });

  it("shows the truncation notice when the fetch returns exactly the request limit", async () => {
    workOrders.mockResolvedValue(
      Array.from({ length: 500 }, (_, i) => order({ wo_id: `WO-${i}` })),
    );
    render(<WorkOrders />);
    expect(
      await screen.findByText(/Showing the 500 most recently created work orders/),
    ).toBeInTheDocument();
  });
});
