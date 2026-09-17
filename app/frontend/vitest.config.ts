import { defineConfig } from "vitest/config";

// Separate from vite.config.ts on purpose: the app build has no reason to know about a
// test environment, and keeping them apart means a change to one can't silently affect
// the other.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // jsdom scopes localStorage to an origin and leaves it undefined without one — the
    // default "about:blank" test document has none. theme.ts uses localStorage for real,
    // so the test environment needs an origin, not just a DOM.
    environmentOptions: { jsdom: { url: "http://localhost/" } },
    setupFiles: ["src/test-setup.ts"],
  },
});
