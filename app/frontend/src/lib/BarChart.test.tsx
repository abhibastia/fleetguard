import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart, type BarChartDatum } from "./BarChart";

describe("BarChart", () => {
  it("renders one bar group per datum, labelled via the accessible name", () => {
    const data: BarChartDatum[] = [
      { label: "2024", value: 10, title: "2024: 10 campaigns" },
      { label: "2025", value: 20, title: "2025: 20 campaigns" },
    ];
    render(<BarChart data={data} />);
    const svg = screen.getByRole("img");
    expect(svg).toHaveAccessibleName("2024: 10 campaigns; 2025: 20 campaigns");
  });

  it("renders each bar's value and label as text", () => {
    const data: BarChartDatum[] = [{ label: "2024", value: 42, title: "2024: 42" }];
    render(<BarChart data={data} />);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("2024")).toBeInTheDocument();
  });

  it("marks a partial-period bar and suffixes its value with an asterisk", () => {
    const data: BarChartDatum[] = [
      { label: "2026", value: 5, title: "2026 (partial)", partial: true },
    ];
    const { container } = render(<BarChart data={data} />);
    expect(screen.getByText("5*")).toBeInTheDocument();
    expect(container.querySelector(".bar-partial")).not.toBeNull();
  });

  it("renders a highlight sub-segment when highlightValue is given", () => {
    const data: BarChartDatum[] = [
      { label: "2026", value: 10, highlightValue: 3, title: "10, 3 urgent" },
    ];
    const { container } = render(<BarChart data={data} />);
    expect(container.querySelector(".bar-highlight")).not.toBeNull();
  });

  it("renders no highlight segment when highlightValue is absent", () => {
    const data: BarChartDatum[] = [{ label: "2026", value: 10, title: "10" }];
    const { container } = render(<BarChart data={data} />);
    expect(container.querySelector(".bar-highlight")).toBeNull();
  });

  it("renders an empty chart without crashing when data is empty", () => {
    const { container } = render(<BarChart data={[]} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
