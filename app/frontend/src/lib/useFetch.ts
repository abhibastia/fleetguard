import { type DependencyList, type SetStateAction, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

interface InternalState<T> {
  data: T | null;
  error: string | null;
  gated: boolean;
}

export interface FetchState<T> extends InternalState<T> {
  /**
   * Escape hatch for the optimistic-update pattern: a few views PATCH one row and write the
   * server's response back into the already-fetched list rather than refetching the whole
   * page for one row's change. Takes the same value-or-updater-function shape as `useState`'s
   * own setter, and touches only `data` — `error`/`gated` are unaffected.
   */
  setData: (next: SetStateAction<T | null>) => void;
}

/**
 * Splits a caught fetch failure into "not signed in" (401 -> gated) vs "actually broken"
 * (everything else -> error) — the one piece of branching logic in `useFetch` worth testing
 * directly, without rendering a component. Every view in this console drew this same
 * distinction by hand before `useFetch` existed.
 */
export function classifyFetchFailure(e: unknown): { gated: boolean; error: string | null } {
  if (e instanceof ApiError && e.status === 401) return { gated: true, error: null };
  const message = e instanceof Error ? e.message : String(e);
  return { gated: false, error: message };
}

const IDLE: InternalState<never> = { data: null, error: null, gated: false };

/**
 * Fetch-on-mount-or-dependency-change, collapsed out of the ~11 views that each reimplemented
 * it: reset state, guard against a response that resolves after `deps` has already changed
 * again (the standard cheap alternative to plumbing an AbortSignal through `lib/api.ts`), and
 * split the caught error into gated vs error. `onSuccess` covers the rare per-view side effect
 * a bare data setter can't express (deriving a page title from the response, for example).
 *
 * Resets `data` to `null` on every dependency change, not just on mount — some call sites
 * relied on this already (a fresh fetch shows loading again rather than stale data sitting
 * under a new filter); this makes that the guaranteed behaviour everywhere instead of
 * something only some views happened to get right.
 */
export function useFetch<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList,
  onSuccess?: (data: T) => void,
): FetchState<T> {
  const [state, setState] = useState<InternalState<T>>(IDLE);
  // A ref, not a dependency: `onSuccess` is typically a fresh closure every render, and this
  // hook's own re-fetch trigger is `deps` alone — the caller decides what causes a re-fetch.
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  useEffect(() => {
    let stale = false;
    setState(IDLE);
    fetcher()
      .then((data) => {
        if (stale) return;
        setState({ data, error: null, gated: false });
        onSuccessRef.current?.(data);
      })
      .catch((e: unknown) => {
        if (stale) return;
        setState({ data: null, ...classifyFetchFailure(e) });
      });
    return () => {
      stale = true;
    };
    // `deps` is forwarded from the caller by design — this hook's contract *is* "re-fetch
    // when these change", so exhaustive-deps has nothing to check here that the caller isn't
    // already responsible for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  function setData(next: SetStateAction<T | null>) {
    setState((s) => ({
      ...s,
      data: typeof next === "function" ? (next as (prev: T | null) => T | null)(s.data) : next,
    }));
  }

  return { ...state, setData };
}
