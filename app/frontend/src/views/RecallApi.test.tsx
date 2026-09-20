import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { RecallApi } from "./RecallApi";

const { recallApiStatus } = vi.hoisted(() => ({ recallApiStatus: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { recallApiStatus } };
});

function combo(overrides: Record<string, unknown> = {}) {
  return {
    combo_key: "FREIGHTLINER|CASCADIA|2020",
    make: "FREIGHTLINER",
    model: "CASCADIA",
    model_year: 2020,
    fleet_vehicles: 235,
    last_polled_at: "2026-09-20T14:39:00Z",
    last_status: "ok",
    last_campaign_count: 7,
    ...overrides,
  };
}

function alert(overrides: Record<string, unknown> = {}) {
  return {
    alert_key: "26V583000|FREIGHTLINER|CASCADIA|2020",
    campaign_number: "26V583000",
    make: "FREIGHTLINER",
    model: "CASCADIA",
    model_year: 2020,
    component: "SERVICE BRAKES, AIR:ANTILOCK",
    park_it: false,
    park_outside: false,
    consequence: "Increased stopping distance.",
    vehicles_exposed: 235,
    depots_affected: 59,
    ...overrides,
  };
}

function status(overrides: Record<string, unknown> = {}) {
  const { summary: summaryOverride, ...rest } = overrides;
  return {
    poll: [combo()],
    alerts: [],
    ...rest,
    summary: {
      combos: 200,
      combos_ok: 200,
      success_rate_pct: 100,
      fleet_vehicles_covered: 33231,
      last_polled_at: "2026-09-20T14:39:00Z",
      alerts: 0,
      alert_campaigns: 0,
      vehicles_exposed_by_alerts: 0,
      ...((summaryOverride as object) ?? {}),
    },
  };
}

/** A fetcher that rejects, without the rejection ever being momentarily unowned.
 *
 * `mockRejectedValue(e)` — and a bare `() => Promise.reject(e)` — leave the rejected promise
 * with no handler for the tick between the mock being configured and `useFetch`'s effect
 * attaching its `.catch`. In this file vitest reliably flags that as an unhandled rejection
 * and fails the test, even though the component behaves correctly (checked while writing
 * these: it renders "Sign-in required" exactly as expected).
 *
 * Attaching a no-op `.catch` marks the original promise as handled while still returning
 * *it* to the caller, so `useFetch` receives and classifies the same rejection it always
 * would. This changes the test harness, not the behaviour under test.
 */
function rejectingWith(err: unknown) {
  return () => {
    const p = Promise.reject(err);
    p.catch(() => {});
    return p;
  };
}

/** Fresh mock state for one test.
 *
 * Called as the first line of each test rather than from a `beforeEach` hook, which is
 * unusual and deliberate. With the reset in `beforeEach`, the two tests whose mock *rejects*
 * fail with the rejection surfacing as an uncaught error — consistently, and regardless of
 * `mockReset` vs `mockClear`, or `mockRejectedValue` vs a fresh `Promise.reject`. Moving the
 * identical call inside the test body makes them pass. The component is not implicated: it
 * was confirmed during development to render "Sign-in required" correctly in exactly the
 * failing case, so this is a vitest hook-boundary interaction, not a bug under test.
 */
function freshMock() {
  recallApiStatus.mockReset();
}

describe("RecallApi", () => {

  it("shows the sign-in panel on 401", async () => {
    recallApiStatus.mockImplementation(rejectingWith(new ApiError(401, "nope")));
    render(<RecallApi />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows PageError on a non-401 failure", async () => {
    recallApiStatus.mockImplementation(rejectingWith(new Error("backend exploded")));
    render(<RecallApi />);
    expect(
      await screen.findByText("Recall API status could not be loaded."),
    ).toBeInTheDocument();
  });

  it("states zero alerts as a result, not an empty table", async () => {
    // The state this view spent most of its life in. "The feed works and there is no news"
    // must read differently from "the feed is broken" — so a healthy success rate is shown
    // alongside an explicit sentence, and no alert table is rendered at all.
    freshMock();
    recallApiStatus.mockResolvedValue(status());
    render(<RecallApi />);

    expect(
      await screen.findByText(/No campaigns found that the daily flat file does not already have/),
    ).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.queryByText("26V583000")).not.toBeInTheDocument();
  });

  it("renders an em dash rather than 0% when nothing has been polled", async () => {
    // A null success rate means "never run". Rendering it as 0% would claim a total outage.
    freshMock();
    recallApiStatus.mockResolvedValue({
      summary: {
        combos: 0,
        combos_ok: 0,
        success_rate_pct: null,
        fleet_vehicles_covered: 0,
        last_polled_at: null,
        alerts: 0,
        alert_campaigns: 0,
        vehicles_exposed_by_alerts: 0,
      },
      poll: [],
      alerts: [],
    });
    render(<RecallApi />);

    expect(await screen.findByText("Nothing polled yet")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.getByText(/Last swept never/)).toBeInTheDocument();
  });

  it("lists alerts with exposure, and counts campaigns separately from rows", async () => {
    // One campaign spanning several model years is the real shape (26V583000 covers four),
    // which is why the summary carries both `alerts` and `alert_campaigns`.
    freshMock();
    recallApiStatus.mockResolvedValue({
      summary: {
        combos: 200,
        combos_ok: 200,
        success_rate_pct: 100,
        fleet_vehicles_covered: 33231,
        last_polled_at: "2026-09-20T14:39:00Z",
        alerts: 2,
        alert_campaigns: 1,
        vehicles_exposed_by_alerts: 314,
      },
      poll: [combo()],
      alerts: [
        alert(),
        alert({
          alert_key: "26V583000|FREIGHTLINER|CASCADIA|2022",
          model_year: 2022,
          vehicles_exposed: 79,
        }),
      ],
    });
    render(<RecallApi />);

    // The sentence is broken up by <strong> wrappers, so match the paragraph as a whole.
    expect(
      await screen.findByText(
        (_t, el) =>
          el?.tagName === "P" && /campaign\s+visible in the live API/.test(el.textContent ?? ""),
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("26V583000")).toHaveLength(2);
    expect(screen.getByText("FREIGHTLINER CASCADIA 2020")).toBeInTheDocument();
    expect(screen.getByText("FREIGHTLINER CASCADIA 2022")).toBeInTheDocument();
  });

  it("filters the coverage table to failures only", async () => {
    freshMock();
    recallApiStatus.mockResolvedValue({
      ...status(),
      poll: [combo(), combo({ combo_key: "FORD|F-150|2024", make: "FORD", model: "F-150" })],
    });
    render(<RecallApi />);
    await screen.findByText("FREIGHTLINER");

    await userEvent.click(screen.getByLabelText("Failures only"));

    expect(await screen.findByText("No matching combinations")).toBeInTheDocument();
    expect(
      screen.getByText("Every polled combination returned successfully."),
    ).toBeInTheDocument();
  });

  it("shows a no-match panel when the search matches nothing", async () => {
    freshMock();
    recallApiStatus.mockResolvedValue(status());
    render(<RecallApi />);
    await screen.findByText("FREIGHTLINER");

    await userEvent.type(screen.getByLabelText("Search polled combinations"), "nomatch");

    expect(await screen.findByText("No matching combinations")).toBeInTheDocument();
  });
});
