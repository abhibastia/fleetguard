import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Home } from "./Home";

const { evidence, queue, signals, depotRisk, corpus, me } = vi.hoisted(() => ({
  evidence: vi.fn(),
  queue: vi.fn(),
  signals: vi.fn(),
  depotRisk: vi.fn(),
  corpus: vi.fn(),
  me: vi.fn(),
}));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { evidence, queue, signals, depotRisk, corpus, me } };
});

const sampleCorpus = {
  complaints: 2240289,
  tsbs: 5801279,
  recalls: 244925,
  investigations: 154367,
  fleet_vehicles: 20000,
  fleet_depots: 60,
  rag_chunks: 115499,
  bronze_total: 8440860,
  source_schema: "bootcamp_students.fleetguard",
  statement: "SELECT COUNT(*) ...",
  generated_at: "2026-09-23T19:40:41+00:00",
};

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
    corpus.mockReset();
    me.mockReset();
    // Default for the existing cases, which are about evidence and fleet state. The scale
    // strip has its own tests below.
    corpus.mockResolvedValue(sampleCorpus);
    me.mockResolvedValue({
      user_name: "test@example.com",
      token_source: "databricks-apps",
      dashboard_url: "https://example.databricks.com/dashboardsv3/test/published",
    });
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

  // ── corpus scale strip (I-115 follow-on) ──────────────────────────────────────────────
  //
  // The point of these is the SOURCE, not the layout. Home's own rule is that it invents no
  // numbers, and the scale figures are the ones most likely to be quietly hardcoded — that is
  // exactly what I-115 removed from README.md and Assistant.tsx after I-111's rescope left
  // `1.75M` asserted on three user-facing surfaces. If someone ever replaces `/api/corpus`
  // with a literal, the third test fails.

  it("renders corpus scale from the endpoint", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("2.24M")).toBeInTheDocument();
    expect(screen.getByText("5.8M")).toBeInTheDocument();
    expect(screen.getByText("20,000")).toBeInTheDocument();
  });

  it("omits the scale strip rather than rendering zeros when corpus fails", async () => {
    // `/api/corpus` 503s when its snapshot is missing (routers/corpus.py) — the page must
    // lose the strip, never show "0 NHTSA complaints", which reads as "this system has no
    // data" rather than "the numbers failed to load". I-050's rule.
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    corpus.mockRejectedValue(new Error("503"));
    render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("16.0%")).toBeInTheDocument();
    expect(screen.queryByLabelText("Corpus scale")).not.toBeInTheDocument();
    expect(screen.queryByText("NHTSA complaints")).not.toBeInTheDocument();
  });

  it("takes the scale numbers from the endpoint, not from literals in the JSX", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    corpus.mockResolvedValue({ ...sampleCorpus, complaints: 3000000, fleet_depots: 7 });
    render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("3.00M")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.queryByText("2.24M")).not.toBeInTheDocument();
  });

  it("shows the flow steps as text for screen readers, with the drawing hidden", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    queue.mockResolvedValue([]);
    signals.mockResolvedValue({ total: 0, live: 0, fleet_relevant: 0, as_of_month: null, signals: [] });
    depotRisk.mockResolvedValue([]);
    const { container } = render(<Home onNavigate={() => {}} />);
    expect(await screen.findByText("16.0%")).toBeInTheDocument();

    // "Approve" is present TWICE in the DOM — once drawn in the SVG, once in the list — which
    // is exactly why the SVG must be aria-hidden: otherwise a screen reader announces all six
    // steps twice. So assert against the list specifically rather than by text.
    const list = container.querySelector("ol.home-flow-text");
    expect(list).toBeInTheDocument();
    expect(list).toHaveTextContent("Approve");
    expect(list).toHaveTextContent("human gate");
    expect(list?.querySelectorAll("li")).toHaveLength(6);
    expect(container.querySelector("svg.home-flow-svg")).toHaveAttribute("aria-hidden", "true");
  });
});
