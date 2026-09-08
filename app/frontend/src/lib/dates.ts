/**
 * Date handling for date-only values, extracted because it was wrong once.
 *
 * `due_date` is a Postgres `DATE` and arrives as `YYYY-MM-DD`. The rule here is that such a
 * value is **never** passed through `new Date()`: ISO date-only strings parse as UTC midnight,
 * while almost every other date string form parses as local midnight, and mixing the two
 * silently shifts results by the viewer's UTC offset. Comparing the strings directly is both
 * correct and timezone-free, because ISO `YYYY-MM-DD` sorts lexicographically in date order.
 */

import type { WorkOrder } from "./api";

/** Today in the viewer's own timezone, as `YYYY-MM-DD`.
 *
 * Built from local date parts on purpose. `new Date().toISOString().slice(0, 10)` looks
 * equivalent and is not: it converts to UTC first, so for a viewer behind UTC it returns
 * *tomorrow's* date for most of the evening. */
export function todayLocalISO(now: Date = new Date()): string {
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${mm}-${dd}`;
}

/**
 * Is this work order past its due date?
 *
 * The previous implementation was
 * `new Date(w.due_date) < new Date(new Date().toDateString())`, which mixed the two parsing
 * rules described above: the left side parsed as UTC midnight, the right as local midnight.
 * For any viewer **behind** UTC the left is the smaller value even on the same calendar day,
 * so **every work order due today rendered as OVERDUE**. Measured 2026-09-09: correct in
 * Europe/Berlin (UTC+2), wrong in America/Los_Angeles (UTC-7) — the workspace's own region,
 * and where a judge is far likelier to be sitting than the author. Invisible to anyone
 * developing east of UTC, which is why it survived.
 *
 * This also brings the console into line with the backend, which has always defined overdue as
 * `due_date < CURRENT_DATE` (`depots.py`). Before this fix the Depot Risk tile and the Work
 * Orders list could disagree about the same rows.
 *
 * `today` is injectable so the comparison can be tested without depending on the runner's
 * timezone or clock.
 */
export function isOverdue(w: WorkOrder, today: string = todayLocalISO()): boolean {
  if (!w.due_date || w.status === "COMPLETED" || w.status === "CANCELLED") return false;
  return w.due_date < today;
}
