import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    statusText: response.statusText ?? "",
    json: response.json ?? (() => Promise.resolve({})),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request() error handling — every call goes through this, so a bug here is silent everywhere", () => {
  it("throws a fixed 'sign-in required' ApiError on 401, not the server's detail text", () => {
    // Deliberate: the console never distinguishes *why* a session is invalid ("expired" vs
    // "sign in to continue") — every 401 means the same thing to the UI, show the sign-in
    // state. Losing this behaviour would leak backend wording into the console.
    mockFetchOnce({ ok: false, status: 401, json: () => Promise.resolve({ detail: "session expired; sign in again" }) });
    return expect(api.me()).rejects.toMatchObject(
      new ApiError(401, "Sign-in required."),
    );
  });

  it("surfaces the server's detail message on other error statuses", async () => {
    mockFetchOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: "campaign exposes no vehicles in scope" }),
    });
    await expect(api.me()).rejects.toMatchObject(
      new ApiError(409, "campaign exposes no vehicles in scope"),
    );
  });

  it("falls back to statusText when the error body is not JSON", async () => {
    // A Render infra error (502 HTML page) or a proxy failure won't return the API's JSON
    // shape. Losing this fallback turns an already-bad error into an unhandled exception.
    mockFetchOnce({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: () => Promise.reject(new SyntaxError("Unexpected token < in JSON")),
    });
    await expect(api.me()).rejects.toMatchObject(new ApiError(502, "Bad Gateway"));
  });

  it("resolves normally on a 200", async () => {
    mockFetchOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ user_name: "ops@example.com", token_source: "databricks-apps" }),
    });
    await expect(api.me()).resolves.toEqual({
      user_name: "ops@example.com",
      token_source: "databricks-apps",
    });
  });

  it("always sends credentials — identity travels as a cookie, dropping this silently breaks every authenticated call", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      json: () => Promise.resolve({ user_name: null, token_source: "databricks-apps" }),
    });
    await api.me();
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toMatchObject({ credentials: "include" });
  });

  it("hits the expected path and base", async () => {
    const fetchMock = mockFetchOnce({
      ok: true,
      json: () => Promise.resolve([]),
    });
    await api.queue(25);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/queue?limit=25");
  });
});

describe("ApiError", () => {
  it("carries the status code as a first-class field, not just in the message", () => {
    const err = new ApiError(503, "offline");
    expect(err.status).toBe(503);
    expect(err.message).toBe("offline");
    expect(err).toBeInstanceOf(Error);
  });
});
