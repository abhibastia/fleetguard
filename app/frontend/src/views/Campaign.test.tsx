import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Campaign } from "./Campaign";

const { campaign, approve } = vi.hoisted(() => ({ campaign: vi.fn(), approve: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { campaign, approve } };
});

function detail(overrides: Record<string, unknown> = {}) {
  return {
    campaign_id: "24V001",
    component: "BRAKES",
    park_it: false,
    consequence: null,
    remedy: null,
    vehicles_exposed: 25,
    by_depot: { "DEP-001": 15, "DEP-002": 10 },
    sample_vehicles: [],
    service_campaign_id: null,
    ...overrides,
  };
}

describe("Campaign", () => {
  beforeEach(() => {
    campaign.mockReset();
    approve.mockReset();
  });

  it("shows the sign-in message on a 401 with no cached data", async () => {
    campaign.mockRejectedValue(new ApiError(401, "nope"));
    render(<Campaign id="24V001" onBack={() => {}} />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    campaign.mockRejectedValue(new Error("backend exploded"));
    render(<Campaign id="24V001" onBack={() => {}} />);
    expect(await screen.findByText("Campaign detail could not be loaded.")).toBeInTheDocument();
  });

  it("renders exposure and depot rows from fetched data", async () => {
    campaign.mockResolvedValue(detail());
    render(<Campaign id="24V001" onBack={() => {}} />);
    expect(await screen.findByText("25 vehicles across 2 depots", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("DEP-001")).toBeInTheDocument();
  });

  it("shows the already-launched panel instead of the approval form when a service campaign exists", async () => {
    campaign.mockResolvedValue(detail({ service_campaign_id: "SC-9" }));
    render(<Campaign id="24V001" onBack={() => {}} />);
    expect(await screen.findByText("Already launched", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("SC-9")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("disables Approve until both title and rationale are filled", async () => {
    campaign.mockResolvedValue(detail());
    render(<Campaign id="24V001" onBack={() => {}} />);
    const approveButton = await screen.findByRole("button", { name: /Approve/ });
    expect(approveButton).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Rationale/), "Because it must be done");
    expect(approveButton).toBeEnabled(); // title is pre-filled by useFetch's onSuccess
  });

  it("calls api.approve with the campaign id and form values, then shows the confirmation", async () => {
    campaign.mockResolvedValue(detail());
    approve.mockResolvedValue({
      service_campaign_id: "SC-99",
      campaign_id: "24V001",
      work_orders_created: 25,
      approved_by: "ops@example.com",
      due_date: "2026-10-01",
    });
    render(<Campaign id="24V001" onBack={() => {}} />);
    await userEvent.type(await screen.findByLabelText(/Rationale/), "Because it must be done");
    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(approve).toHaveBeenCalledWith(
      "24V001",
      expect.objectContaining({ rationale: "Because it must be done", due_in_days: 30 }),
    );
    expect(await screen.findByText("SC-99", { selector: "strong" })).toBeInTheDocument();
  });

  it("uses a 7-day due date instead of 30 when the campaign is Park It", async () => {
    campaign.mockResolvedValue(detail({ park_it: true }));
    approve.mockResolvedValue({
      service_campaign_id: "SC-1",
      campaign_id: "24V001",
      work_orders_created: 25,
      approved_by: "ops@example.com",
      due_date: "2026-09-24",
    });
    render(<Campaign id="24V001" onBack={() => {}} />);
    await userEvent.type(await screen.findByLabelText(/Rationale/), "Urgent brake defect");
    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(approve).toHaveBeenCalledWith("24V001", expect.objectContaining({ due_in_days: 7 }));
  });

  it("shows the approval error inline and keeps the form usable on failure", async () => {
    campaign.mockResolvedValue(detail());
    approve.mockRejectedValue(new ApiError(409, "campaign already launched"));
    render(<Campaign id="24V001" onBack={() => {}} />);
    await userEvent.type(await screen.findByLabelText(/Rationale/), "Because it must be done");
    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(await screen.findByText("campaign already launched")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeInTheDocument();
  });
});
