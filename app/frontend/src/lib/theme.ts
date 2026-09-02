/**
 * Theme selection.
 *
 * **Dark is the default**, deliberately and unconditionally — this is a monitoring surface,
 * frequently on a wall display, and the severity palette is tuned against a dark ground. The
 * system preference is *not* consulted: a light-mode laptop should not silently change what
 * an operator console looks like the first time it is opened. Light is a choice the user
 * makes, and it is then remembered.
 *
 * The attribute is written to `<html>` by an inline script in index.html before first paint,
 * so there is no flash of the wrong theme. This module keeps that in sync afterwards.
 */

export type Theme = "dark" | "light";

const KEY = "fleetguard.theme";

/** Read what the pre-paint script already applied, so React never disagrees with the DOM. */
export function currentTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private browsing, or storage disabled. The theme still applies for this session —
    // failing to persist a preference is not worth breaking the page over.
  }
}
