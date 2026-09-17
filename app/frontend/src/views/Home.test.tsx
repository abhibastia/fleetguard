import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Home } from "./Home";

const { evidence, queue, signals, depotRisk } = vi.hoisted(() => ({
  evidence: vi.fn(),
  queue: vi.fn(),
  signals: vi.fn(),
  depotRisk: vi.fn(),
}));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { evidence, queue, signals, depotRisk } };
});

const sampleEvidence = {
  real: { n: 777, detected: 124, rate_pct: 16.0, median_lead_days: 197 },
  placebo: { n: 606, detected: 67, rate_pct: 11.1, median_lead_days: 343 },
  model_b: {
    model_version: 2,
    golden_set_size: 163,
    golden_set_positive: 91,
    threshold: 0.62,
    precision: 0.91,
    recall: 0.87,
    roc_auc: 0.93,
    test_set_size: 33,
  },
  lift: 1.44,
  z: 2.9,
  p_value: 0.0087,
  source_table: "gold_lead_time_summary",
  statement: "measured",
  generated_at: "2026-09-01",
};

describe("Home", () => {
  beforeEach(() => {
    evidence.mockReset();
    queue.mockReset();
    signals.mockReset();
    depotRisk.mockReset();
  });

  it("renders the measured claim once evidence resolves, even for a signed-out visitor", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockRejectedValue(new Error("401"));
    signals.mockRejectedValue(new Error("401"));
    depotRisk.mockRejectedValue(new Error("401"));
    render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("16.0%")).toBeInTheDocument();
    expect(screen.getByText("1.44×", { exact: false })).toBeInTheDocument();
  });

  it("shows the fetch-failed message for evidence, not a false zero, when it fails", async () => {
    evidence.mockRejectedValue(new Error("backend exploded"));
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    render(<Home onNavigate={() => {}} />);
    expect(
      await screen.findByText(/The measured result could not be loaded right now/),
    ).toBeInTheDocument();
  });

  it("shows fleet-unavailable when every fleet read fails, without misreporting it as signed-out", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockRejectedValue(new Error("401"));
    signals.mockRejectedValue(new Error("401"));
    depotRisk.mockRejectedValue(new Error("401"));
    render(<Home onNavigate={() => {}} />);
    expect(
      await screen.findByText("Live fleet data is not available right now."),
    ).toBeInTheDocument();
  });

  it("renders live fleet stats from real queue/signals/depot data", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([
      { campaign_id: "24V001", park_it: true, do_not_drive: false, vehicles_exposed: 10, depots_affected: 2, component: null, consequence: null, service_campaign_id: null },
    ]);
    signals.mockResolvedValue({ total: 5, live: 2, fleet_relevant: 3, as_of_month: "2026-08", signals: [] });
    depotRisk.mockResolvedValue([
      { depot_id: "DEP-1", depot_name: "D1", region: "WEST", city: "X", state: "CA", fleet_size: 100, urgent_vehicles_exposed: 0, total_vehicles_exposed: 10, distinct_campaigns: 1, outstanding_work_orders: 5, overdue_work_orders: 2 },
    ]);
    render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("1")).toBeInTheDocument(); // Park It campaigns open
    expect(screen.getByText("3")).toBeInTheDocument(); // fleet-relevant signals
    expect(screen.getByText(/work orders still outstanding across 1 depot/)).toBeInTheDocument();
  });

  it("navigates via onNavigate when a home-path card is clicked", async () => {
    const onNavigate = vi.fn();
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    render(<Home onNavigate={onNavigate} />);
    await userEvent.click(await screen.findByRole("button", { name: /Recall queue/ }));
    expect(onNavigate).toHaveBeenCalledWith("queue");
  });
});
