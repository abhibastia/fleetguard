import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PageError } from "./PageError";

describe("PageError", () => {
  it("renders the title and the detail message", () => {
    render(<PageError title="Queue" detail="backend exploded" />);
    expect(screen.getByRole("heading", { name: "Queue" })).toBeInTheDocument();
    expect(screen.getByText("backend exploded")).toBeInTheDocument();
  });

  describe("Retry button", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("reloads the page when clicked", () => {
      const reload = vi.fn();
      vi.stubGlobal("location", { ...window.location, reload });
      render(<PageError title="Queue" detail="backend exploded" />);
      screen.getByRole("button", { name: /retry/i }).click();
      expect(reload).toHaveBeenCalled();
      vi.unstubAllGlobals();
    });
  });
});
