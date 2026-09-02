import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, currentTheme } from "./theme";

// Node 22+ ships its own native `localStorage` global, which shadows jsdom's in this test
// environment and is left uninitialized without a CLI flag this project has no reason to
// carry. Stubbed explicitly here rather than depended on implicitly — this also makes the
// fake swappable per test (see the "throws" case below), which the real Storage object
// would not allow without the same Storage.prototype.setItem spy trick either way.
function fakeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      data.set(k, v);
    },
    removeItem: (k: string) => {
      data.delete(k);
    },
    clear: () => data.clear(),
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
});
afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  vi.unstubAllGlobals();
});

describe("currentTheme", () => {
  it("reads light when the DOM attribute says light", () => {
    document.documentElement.setAttribute("data-theme", "light");
    expect(currentTheme()).toBe("light");
  });

  it("defaults to dark for any other value, including absence", () => {
    // No attribute at all — the un-set state a fresh <html> starts in.
    expect(currentTheme()).toBe("dark");
  });

  it("defaults to dark on a garbage attribute value, not just absence", () => {
    // A stale or hand-edited attribute must not read as anything other than dark — the
    // module's own contract is "dark unless the attribute is exactly 'light'".
    document.documentElement.setAttribute("data-theme", "sepia");
    expect(currentTheme()).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("sets the DOM attribute and persists the choice", () => {
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem("fleetguard.theme")).toBe("light");
  });

  it("round-trips through currentTheme", () => {
    applyTheme("light");
    expect(currentTheme()).toBe("light");
    applyTheme("dark");
    expect(currentTheme()).toBe("dark");
  });

  it("still applies the DOM attribute even when localStorage throws", () => {
    // Private browsing / storage disabled: the module's own comment says the theme must
    // still apply for the session even if persistence fails. That claim was never checked.
    vi.stubGlobal("localStorage", {
      ...fakeStorage(),
      setItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    expect(() => applyTheme("light")).not.toThrow();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
