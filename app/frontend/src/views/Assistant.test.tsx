import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { Assistant } from "./Assistant";

const { chat } = vi.hoisted(() => ({ chat: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { chat } };
});

describe("Assistant", () => {
  beforeEach(() => {
    chat.mockReset();
  });

  it("shows the example prompt chips before any turn is sent", () => {
    render(<Assistant />);
    expect(
      screen.getByRole("button", { name: "Search complaints about brake failures" }),
    ).toBeInTheDocument();
  });

  it("sends a chip's text immediately when clicked, and renders the reply", async () => {
    chat.mockResolvedValue({
      reply: "Here is what I found.",
      endpoint: "agent",
      action_result: null,
    });
    render(<Assistant />);
    await userEvent.click(
      screen.getByRole("button", { name: "Search complaints about brake failures" }),
    );
    expect(chat).toHaveBeenCalledWith([
      { role: "user", content: "Search complaints about brake failures" },
    ]);
    expect(await screen.findByText("Here is what I found.")).toBeInTheDocument();
  });

  it("sends the typed draft on Enter and clears the input", async () => {
    chat.mockResolvedValue({ reply: "Answer.", endpoint: "agent", action_result: null });
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "What is open?{Enter}");
    expect(chat).toHaveBeenCalledWith([{ role: "user", content: "What is open?" }]);
    expect(input).toHaveValue("");
  });

  it("shows the offline message on a 503, not a raw error", async () => {
    chat.mockRejectedValue(new ApiError(503, "endpoint stopped"));
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "hello{Enter}");
    expect(
      await screen.findByText("The assistant is offline. The queue and approval path are unaffected."),
    ).toBeInTheDocument();
  });

  it("shows the sign-in explanation on a 401, not the raw message", async () => {
    chat.mockRejectedValue(new ApiError(401, "nope"));
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "hello{Enter}");
    expect(
      await screen.findByText(/it needs an authenticated session/),
    ).toBeInTheDocument();
  });

  it("shows a real error message on a non-401/503 failure", async () => {
    chat.mockRejectedValue(new ApiError(500, "backend exploded"));
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "hello{Enter}");
    expect(await screen.findByText("backend exploded")).toBeInTheDocument();
  });

  it("renders the open_defect_signal action receipt from the committed row, not the reply text", async () => {
    chat.mockResolvedValue({
      reply: "I opened a signal for this.",
      endpoint: "agent",
      action_result: {
        action: "open_defect_signal",
        signal_id: "SIG-9",
        component: "BRAKES",
        make: "RAM",
        model: "2500",
        fleet_vehicles: 12,
        match_basis: "MODEL_VARIANT",
        opened_by: "ops@example.com",
      },
    });
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "open a signal{Enter}");
    expect(await screen.findByText("Defect signal opened")).toBeInTheDocument();
    expect(screen.getByText("SIG-9")).toBeInTheDocument();
    expect(screen.getByText(/12 fleet vehicles match/)).toBeInTheDocument();
    expect(screen.getByText(/Matched on a model-name variant/)).toBeInTheDocument();
  });

  it("renders the watch_campaign action receipt", async () => {
    chat.mockResolvedValue({
      reply: "Watching it.",
      endpoint: "agent",
      action_result: {
        action: "watch_campaign",
        watchlist_id: "W-1",
        campaign_id: "24V001",
        watched_by: "ops@example.com",
        watched_at: "2026-01-01T00:00:00Z",
      },
    });
    render(<Assistant />);
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "watch this{Enter}");
    expect(await screen.findByText("Campaign watched")).toBeInTheDocument();
    expect(screen.getByText("24V001")).toBeInTheDocument();
  });

  it("disables the Ask button while a draft is empty or a request is in flight", async () => {
    let resolveChat: (v: unknown) => void = () => {};
    chat.mockReturnValue(new Promise((resolve) => (resolveChat = resolve)));
    render(<Assistant />);
    const askButton = screen.getByRole("button", { name: "Ask" });
    expect(askButton).toBeDisabled(); // empty draft
    const input = screen.getByPlaceholderText("Ask about a campaign or a symptom…");
    await userEvent.type(input, "hello");
    expect(askButton).toBeEnabled();
    await userEvent.click(askButton);
    expect(askButton).toBeDisabled(); // now busy
    resolveChat({ reply: "done", endpoint: "agent", action_result: null });
  });
});
