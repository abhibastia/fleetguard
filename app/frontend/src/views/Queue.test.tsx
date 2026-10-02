import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Queue } from "./Queue";

const { queue, queueSummary } = vi.hoisted(() => ({ queue: vi.fn(), queueSummary: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { queue, queueSummary } };
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
    // Default to rejecting: the stat cards must survive the summary being unavailable, and a
    // test that silently got a resolved value would not prove that.
    queueSummary.mockReset();
    queueSummary.mockRejectedValue(new Error("no summary"));
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

  // ------------------------------------------------------------------ I-130 regression pins
  // The cards used to be derived from `items`, which is ONE PAGE capped at the API's default
  // limit of 50. Live that meant "50 campaigns" against 393, and a vehicle total of 51,615
  // against 11,323 distinct VINs, because the sum counted a VIN once per campaign it matched.
  // These two tests fail against that implementation.

  it("shows the server's campaign total, not the number of rows it fetched", async () => {
    queue.mockResolvedValue([item(), item({ campaign_id: "24V002" })]);
    queueSummary.mockResolvedValue({
      campaigns: 393,
      vehicles_exposed: 11323,
      urgent_campaigns: 6,
    });
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("393")).toBeInTheDocument();
    // The page length must not appear as a stat. Scoped to the stat block so a "2" elsewhere
    // in the table cannot make this pass or fail by accident.
    const stats = document.querySelector(".stats")!;
    expect(stats.textContent).not.toContain("2 ");
  });

  it("shows distinct exposed vehicles, not the sum over campaigns", async () => {
    // Two campaigns, 10 vehicles each: the old code rendered 20. The server says 11,323
    // because a VIN in both campaigns is one vehicle.
    queue.mockResolvedValue([item(), item({ campaign_id: "24V002" })]);
    queueSummary.mockResolvedValue({
      campaigns: 393,
      vehicles_exposed: 11323,
      urgent_campaigns: 6,
    });
    render(<Queue onOpen={() => {}} />);
    expect(await screen.findByText("11,323")).toBeInTheDocument();
    expect(screen.queryByText("20")).not.toBeInTheDocument();
  });

  it("falls back to page-derived figures when the summary is unavailable", async () => {
    // Degradation, not correctness: the fallback is the OLD wrong arithmetic, kept only so the
    // cards render at all. Pinned so nobody "simplifies" it into a crash or a blank card.
    queue.mockResolvedValue([item(), item({ campaign_id: "24V002" })]);
    queueSummary.mockRejectedValue(new Error("down"));
    render(<Queue onOpen={() => {}} />);
    // Anchor on the stat itself, not on row text — both fixtures share a component name.
    expect(await screen.findByText("20")).toBeInTheDocument();
    const stats = document.querySelector(".stats")!;
    expect(stats.textContent).toContain("20");
  });
});
