import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Evidence } from "./Evidence";

const { evidence } = vi.hoisted(() => ({ evidence: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { evidence } };
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

describe("Evidence", () => {
  beforeEach(() => {
    evidence.mockReset();
  });

  it("shows an error message, not a blank table, on failure", async () => {
    evidence.mockRejectedValue(new Error("backend exploded"));
    render(<Evidence />);
    expect(await screen.findByText(/Evidence unavailable: backend exploded/)).toBeInTheDocument();
  });

  it("renders the real/placebo detection rates and lift from fetched data", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    render(<Evidence />);
    expect((await screen.findAllByText("16.0%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("11.1%").length).toBeGreaterThan(0);
    expect(screen.getByText("1.44×")).toBeInTheDocument();
    expect(screen.getByText("197d")).toBeInTheDocument();
  });

  it("renders the Model B golden-set stats", async () => {
    evidence.mockResolvedValue(sampleEvidence);
    render(<Evidence />);
    expect(await screen.findByText("91.0%")).toBeInTheDocument(); // precision
    expect(screen.getByText("87.0%")).toBeInTheDocument(); // recall
    expect(screen.getByText("163")).toBeInTheDocument(); // golden-set size
  });
});
