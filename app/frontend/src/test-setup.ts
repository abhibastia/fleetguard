import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// vitest.config.ts does not set `test.globals: true` (existing tests import `afterEach`
// explicitly from vitest rather than relying on a global), so @testing-library/react's
// automatic per-test cleanup — which detects globalThis.afterEach — never registers. Without
// this, a render() in one test leaks its DOM into the next test in the same file, and a
// query like getByRole can suddenly match more than one element.
afterEach(() => {
  cleanup();
});
