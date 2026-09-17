import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { AuditLog } from "./AuditLog";

const { auditLog } = vi.hoisted(() => ({ auditLog: vi.fn() }));
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, api: { auditLog } };
});

function entry(overrides: Record<string, unknown> = {}) {
  return {
    audit_id: 1,
    entity_type: "service_campaign",
    entity_id: "SC-1",
    action: "LAUNCHED",
    actor_principal: "ops@example.com",
    before_state: null,
    after_state: { status: "LAUNCHED" },
    created_at: "2026-01-01T12:00:00Z",
    ...overrides,
  };
}

describe("AuditLog", () => {
  beforeEach(() => {
    auditLog.mockReset();
  });

  it("shows the sign-in message on a 401", async () => {
    auditLog.mockRejectedValue(new ApiError(401, "nope"));
    render(<AuditLog />);
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
  });

  it("shows a PageError on a non-401 failure", async () => {
    auditLog.mockRejectedValue(new Error("backend exploded"));
    render(<AuditLog />);
    expect(await screen.findByText("Audit log could not be loaded.")).toBeInTheDocument();
  });

  it("shows the empty-state message when there are no entries", async () => {
    auditLog.mockResolvedValue([]);
    render(<AuditLog />);
    expect(await screen.findByText(/No audit entries yet/)).toBeInTheDocument();
  });

  it("renders an entry's title-cased entity/action and its actor", async () => {
    auditLog.mockResolvedValue([entry()]);
    render(<AuditLog />);
    expect(await screen.findByText("Service campaign", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("Launched")).toBeInTheDocument();
    expect(screen.getByText("ops@example.com")).toBeInTheDocument();
  });

  it("hides LATENCY_PROBE rows from the count and the table", async () => {
    auditLog.mockResolvedValue([
      entry({ audit_id: 1, entity_type: "service_campaign" }),
      entry({ audit_id: 2, entity_type: "LATENCY_PROBE" }),
    ]);
    render(<AuditLog />);
    await screen.findByText("Service campaign", { selector: "td" });
    expect(screen.getByText("Entries (most recent 1)")).toBeInTheDocument();
    expect(screen.queryByText("Latency probe")).not.toBeInTheDocument();
  });

  it("renders a changed field as before -> after", async () => {
    auditLog.mockResolvedValue([
      entry({ before_state: { status: "OPEN" }, after_state: { status: "COMPLETED" } }),
    ]);
    render(<AuditLog />);
    expect(await screen.findByText("status: OPEN → COMPLETED")).toBeInTheDocument();
  });

  it("shows a dash when a row has no changed fields", async () => {
    auditLog.mockResolvedValue([entry({ before_state: null, after_state: null })]);
    render(<AuditLog />);
    const row = (await screen.findByText("SC-1")).closest("tr")!;
    expect(row).toHaveTextContent("—");
  });
});
