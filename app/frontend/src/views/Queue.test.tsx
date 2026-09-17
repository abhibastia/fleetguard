import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Queue } from "./Queue";

const { queue } = vi.hoisted(() => ({ queue: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { queue } };
});

function item(overrides: Record<string, unknown> = {}) {
  return {
    campaign_id: "24V001",
    component: "BRAKES",
    park_it: false,
    do_not_drive: false,
    vehicles_exposed: 10,
    depots_affected: 2,
    consequence: "Loss of braking",
    service_campaign_id: null,
    ...overrides,
  };
}

describe("Queue", () => {
  beforeEach(() => {
    queue.mockReset();
  });

  it("shows the sign-in message on a 401, not a generic error", async () => {
    queue.mockRejectedValue(new ApiError(401, "nope"));
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows the error state on a non-401 failure", async () => {
    queue.mockRejectedValue(new Error("backend exploded"));
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("backend exploded")).toBeInTheDocument();
  });

  it("shows the empty state when the fleet has no exposure", async () => {
    queue.mockResolvedValue([]);
    render(<Queue onOpen={() => {}} />);
    expect(
      await screen.findByText("No campaigns currently match any vehicle in the fleet."),
    ).toBeInTheDocument();
  });

  it("renders campaign rows and the urgent-count stat from real data", async () => {
    queue.mockResolvedValue([
      item({ campaign_id: "24V001", park_it: true, vehicles_exposed: 10 }),
      item({ campaign_id: "24V002", vehicles_exposed: 5 }),
    ]);
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("24V001")).toBeInTheDocument();
    expect(screen.getByText("24V002")).toBeInTheDocument();
    // Urgent stat: 1 of the 2 campaigns is park_it.
    expect(screen.getByText("PARK IT")).toBeInTheDocument();
  });

  it("marks a campaign as LAUNCHED when it already has a service_campaign_id", async () => {
    queue.mockResolvedValue([item({ service_campaign_id: "SC-1" })]);
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("LAUNCHED")).toBeInTheDocument();
  });

  it("calls onOpen with the campaign id when a row is clicked", async () => {
    const onOpen = vi.fn();
    queue.mockResolvedValue([item({ campaign_id: "24V009" })]);
    render(<Queue onOpen={onOpen} />);
    (await screen.findByText("24V009")).closest("tr")?.dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );
    expect(onOpen).toHaveBeenCalledWith("24V009");
  });
});
