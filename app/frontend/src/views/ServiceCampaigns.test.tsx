import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { ServiceCampaigns } from "./ServiceCampaigns";

const { serviceCampaigns, evidence, costBreakdown } = vi.hoisted(() => ({
  serviceCampaigns: vi.fn(),
  evidence: vi.fn(),
  costBreakdown: vi.fn(),
}));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { serviceCampaigns, evidence, costBreakdown } };
});

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    service_campaign_id: "SC-1",
    campaign_id: "24V001",
    title: "Brake remediation",
    vehicle_count: 10,
    status: "LAUNCHED",
    approved_by: "ops@example.com",
    approved_at: "2026-01-01T00:00:00Z",
    open_count: 4,
    in_progress_count: 1,
    completed_count: 5,
    cancelled_count: 0,
    total_actual_cost: 500,
    costed_count: 5,
    ...overrides,
  };
}

describe("ServiceCampaigns", () => {
  beforeEach(() => {
    serviceCampaigns.mockReset();
    evidence.mockReset().mockResolvedValue(undefined);
    costBreakdown.mockReset().mockResolvedValue({ by_component: [], by_depot: [] });
  });

  it("shows the sign-in message on a 401", async () => {
    serviceCampaigns.mockRejectedValue(new ApiError(401, "nope"));
    render(<ServiceCampaigns onOpen={() => {}} />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    serviceCampaigns.mockRejectedValue(new Error("backend exploded"));
    render(<ServiceCampaigns onOpen={() => {}} />);
    expect(
      await screen.findByText("Launched campaigns could not be loaded."),
    ).toBeInTheDocument();
  });

  it("shows the empty-state message when nothing has been launched", async () => {
    serviceCampaigns.mockResolvedValue([]);
    render(<ServiceCampaigns onOpen={() => {}} />);
    expect(await screen.findByText(/No service campaigns launched yet/)).toBeInTheDocument();
  });

  it("renders campaign rows and calls onOpen when a row is clicked", async () => {
    const onOpen = vi.fn();
    serviceCampaigns.mockResolvedValue([campaign()]);
    render(<ServiceCampaigns onOpen={onOpen} />);
    const row = await screen.findByText("Brake remediation");
    row.closest("tr")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(onOpen).toHaveBeenCalledWith("SC-1");
  });

  it("shows a PageError for the cost breakdown without hiding the campaign table", async () => {
    serviceCampaigns.mockResolvedValue([campaign()]);
    costBreakdown.mockRejectedValue(new ApiError(500, "breakdown exploded"));
    render(<ServiceCampaigns onOpen={() => {}} />);
    expect(await screen.findByText("Brake remediation")).toBeInTheDocument();
    expect(
      await screen.findByText("Cost breakdown could not be loaded."),
    ).toBeInTheDocument();
  });

  it("reveals more than 10 depots via the show-all toggle", async () => {
    serviceCampaigns.mockResolvedValue([campaign()]);
    costBreakdown.mockResolvedValue({
      by_component: [],
      by_depot: Array.from({ length: 12 }, (_, i) => ({
        key: `DEP-${i}`,
        total_actual_cost: 100,
        costed_count: 1,
        total_work_orders: 1,
      })),
    });
    render(<ServiceCampaigns onOpen={() => {}} />);
    await screen.findByText("Brake remediation");
    expect(screen.queryByText("DEP-11")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /further depots/ }));
    expect(screen.getByText("DEP-11")).toBeInTheDocument();
  });
});
