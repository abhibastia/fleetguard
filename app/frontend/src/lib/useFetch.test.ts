import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { classifyFetchFailure } from "./useFetch";

describe("classifyFetchFailure", () => {
  it("treats a 401 ApiError as gated, with no error message", () => {
    expect(classifyFetchFailure(new ApiError(401, "not signed in"))).toEqual({
      gated: true,
      error: null,
    });
  });

  it("treats every other ApiError status as a real error, not gated", () => {
    expect(classifyFetchFailure(new ApiError(500, "backend exploded"))).toEqual({
      gated: false,
      error: "backend exploded",
    });
    expect(classifyFetchFailure(new ApiError(404, "not found"))).toEqual({
      gated: false,
      error: "not found",
    });
  });

  it("carries a plain Error's message through as the error, not gated", () => {
    expect(classifyFetchFailure(new TypeError("Failed to fetch"))).toEqual({
      gated: false,
      error: "Failed to fetch",
    });
  });

  it("stringifies a thrown non-Error rather than losing it", () => {
    // fetch() itself only ever rejects with an Error, but a fetcher can throw anything —
    // this is what keeps a thrown string or object from rendering as "[object Object]".
    expect(classifyFetchFailure("boom")).toEqual({ gated: false, error: "boom" });
  });
});
