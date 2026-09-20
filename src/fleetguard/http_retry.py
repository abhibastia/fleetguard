"""Retry, backoff and response validation for this project's outbound HTTP calls.

**Why this exists.** Until now the repo had *no retry logic anywhere* — a grep for
`retry|backoff|429|tenacity` across `src/` and `app/backend/` returned one unrelated print
string. Both external callers failed differently and badly:

- `src/ingest/05_poll_recalls_api.py` caught everything and recorded a status string, so one
  transient blip silently dropped a combo's campaigns for the whole sweep — and the sweep then
  went on to `CREATE OR REPLACE` the alert table from that partial view.
- `src/fleet/04_build_fleet_registry.py::vpic_batch()` had no `try`/`except` at all and a bare
  `json.load(r)["Results"]` subscript, so any single failed batch aborted the entire
  fleet-registry build.

**Why it lives here rather than in the notebooks.** This package is the project's convention
for "logic that has already been wrong once, extracted so it can be unit-tested off platform"
(see `chunking.py`, `vin.py`, `naming.py`). Retry policy is exactly that kind of logic: the
interesting cases are the ones that are painful to reproduce live — a 429 with a `Retry-After`
header, a truncated body, a 200 whose shape changed. Every function here is pure or takes its
side effects (`opener`, `sleeper`) as arguments, so `tests/test_http_retry.py` exercises the
whole matrix with no network and no clock.

**The one non-obvious policy: never retry a 400.** NHTSA's `recallsByVehicle` answers an
unrecognised make/model/year with **HTTP 400 and a body reading "Results returned
successfully"** — status and body disagree (I-031). That is a deterministic "I don't know this
combo", not a transient fault: retrying it three times just triples the request count and
changes nothing. The real mitigation is upstream and already in place — poll the NHTSA
vocabulary (`gold_fleet_exposure.recall_model`) rather than vPIC's, which took coverage from
60% to 200/200.
"""

from __future__ import annotations

import email.utils
import json
import random
import urllib.error
from collections.abc import Callable, Iterable
from typing import Any

#: Attempts include the first try, so 3 means "one call, then at most two retries".
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 8.0

#: Transient by nature: the server is asking us to slow down, or it broke in a way that may
#: not recur. 408 is included because a server-side request timeout is the same class of
#: event as a client-side one.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def should_retry(status: int | None, exc: BaseException | None) -> bool:
    """True when another attempt could plausibly succeed.

    Exactly one of `status` / `exc` is normally set. A 400 is deliberately **not** retryable
    here — see this module's docstring for the NHTSA case that motivated the rule.
    """
    # STATUS WINS WHEN WE HAVE ONE, and the ordering is load-bearing rather than stylistic:
    # `urllib.error.HTTPError` is a *subclass* of `URLError`, so a version of this function
    # that tested the exception first would answer "retry" for every HTTP status — including
    # the 400 the docstring above says must never be retried. Caught by
    # `test_a_400_is_returned_immediately_without_retrying`, which is why it asserts on the
    # opener's call count rather than only on the returned status.
    if status is not None:
        return status in RETRYABLE_STATUSES
    if exc is not None:
        # URLError covers DNS failure, connection refused and socket timeouts (the latter
        # arriving as URLError(reason=TimeoutError) from urlopen). A JSONDecodeError is a
        # truncated or mangled body, which a re-request can genuinely fix.
        return isinstance(exc, urllib.error.URLError | TimeoutError | json.JSONDecodeError)
    return False


def parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` in either documented form: delta-seconds, or an HTTP-date.

    Returns None for absent or unparseable values rather than raising — a malformed header
    should degrade to our own backoff, not fail the request.
    """
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC) if when.tzinfo else _dt.datetime.now()
    return max(0.0, (when - now).total_seconds())


def backoff_delay(
    attempt: int,
    *,
    retry_after: float | None = None,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
    rand: Callable[[], float] | None = None,
) -> float:
    """Seconds to wait before `attempt` (1-based: the wait *after* the first failure is 1).

    Exponential with **full jitter** — `random.uniform(0, base * 2**(attempt-1))`, capped.
    Full jitter rather than fixed backoff because this sweep fires ~200 requests in a row at
    a public government API; if a rate limit trips, an unjittered schedule marches every
    subsequent combo into the same retry instant.

    A server-supplied `Retry-After` always wins, and is **not** jittered down — it is an
    instruction, not a hint. It is still capped, so a hostile or mistaken header cannot
    stall a sweep indefinitely.
    """
    if retry_after is not None:
        return min(retry_after, cap)
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    draw = rand() if rand is not None else random.random()  # noqa: S311 - backoff, not crypto
    return ceiling * draw


class FetchOutcome:
    """What one `fetch_json` call did, including the attempts that failed.

    Carries `status` so the caller can keep NHTSA's status vocabulary
    (`ok` / `http_400` / `malformed` / ...) rather than collapsing everything to a boolean —
    `ops_recall_poll_state.last_status` is read by a dashboard and has to stay legible.
    """

    __slots__ = ("attempts", "body", "error", "retried", "status")

    def __init__(
        self,
        *,
        body: Any = None,
        status: str,
        attempts: int,
        error: str | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.attempts = attempts
        self.retried = attempts > 1
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "retried_ok")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FetchOutcome(status={self.status!r}, attempts={self.attempts})"


def fetch_json(
    url: str,
    *,
    opener: Callable[[str], Any],
    sleeper: Callable[[float], None],
    attempts: int = DEFAULT_ATTEMPTS,
    rand: Callable[[], float] | None = None,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
) -> FetchOutcome:
    """Fetch and JSON-decode `url`, retrying only what is worth retrying.

    `opener(url)` must return a file-like response exposing `.read()`/`.headers`, or raise —
    it is injected so tests need no network, and so the two callers can supply their own
    `Request` objects (headers, method, POST body) without this module growing an HTTP DSL.
    `sleeper(seconds)` is injected for the same reason: the retry schedule is asserted in
    tests rather than waited out.

    Never raises for a network or protocol fault. The caller gets a `FetchOutcome` and
    decides — which is what lets a sweep record a dead combo and carry on, while still
    letting the *run* fail later on an aggregate failure rate.
    """
    last_error: str | None = None
    status_label = "error_Unknown"

    for attempt in range(1, max(1, attempts) + 1):
        http_status: int | None = None
        exc: BaseException | None = None
        retry_after: float | None = None
        try:
            with opener(url) as response:
                body = json.load(response)
            return FetchOutcome(
                body=body,
                status="retried_ok" if attempt > 1 else "ok",
                attempts=attempt,
            )
        except urllib.error.HTTPError as e:
            http_status = e.code
            exc = e
            status_label = f"http_{e.code}"
            last_error = f"HTTP {e.code}"
            # `headers` is absent on hand-rolled HTTPError instances in tests; tolerate it.
            retry_after = parse_retry_after(
                getattr(getattr(e, "headers", None), "get", lambda _: None)("Retry-After")
            )
        except Exception as e:  # noqa: BLE001 - deciding on the exception IS this function's job
            exc = e
            status_label = f"error_{type(e).__name__}"
            last_error = str(e) or type(e).__name__

        if attempt >= attempts or not should_retry(http_status, exc):
            return FetchOutcome(status=status_label, attempts=attempt, error=last_error)

        sleeper(backoff_delay(attempt, retry_after=retry_after, base=base, cap=cap, rand=rand))

    # Unreachable: the loop always returns. Kept so the type checker sees a total function.
    return FetchOutcome(status=status_label, attempts=attempts, error=last_error)


def validate_recalls_payload(body: Any) -> tuple[list[dict], str | None]:
    """Check a `recallsByVehicle` body actually looks like one, before anything trusts it.

    Returns `(results, reason)` — `reason` is None when the payload is usable.

    **This is the check whose absence made a shape change invisible.** Previously the code did
    `body.get("results") or []`, so a 200 carrying a completely different JSON document
    produced zero records and the status `ok` — indistinguishable from "this combo genuinely
    has no open recalls". Given NHTSA already ships one endpoint whose status and body
    disagree (I-031), "the body is the shape we think it is" is not a safe assumption.

    The API reports its own row count in `Count`. Cross-checking it against `len(results)`
    costs nothing and catches truncation, pagination appearing, and the key being renamed.
    """
    if not isinstance(body, dict):
        return [], f"body is {type(body).__name__}, expected object"

    results = body.get("results")
    if results is None:
        return [], "no 'results' key"
    if not isinstance(results, list):
        return [], f"'results' is {type(results).__name__}, expected list"
    if not all(isinstance(r, dict) for r in results):
        return [], "'results' contains non-object entries"

    count = body.get("Count")
    if isinstance(count, int) and count != len(results):
        return [], f"Count={count} but got {len(results)} results"

    return results, None


def summarise_statuses(statuses: Iterable[str]) -> dict[str, int]:
    """Bucket per-combo status strings into the run-level counters the sweep records.

    Kept here, beside the vocabulary that produces those strings, so the accounting cannot
    drift from the statuses `fetch_json` actually emits.
    """
    out = {
        "ok": 0,
        "retried": 0,
        "http_4xx": 0,
        "http_5xx": 0,
        "malformed": 0,
        "errors": 0,
    }
    for s in statuses:
        if s in ("ok", "retried_ok"):
            out["ok"] += 1
            if s == "retried_ok":
                out["retried"] += 1
        elif s == "malformed":
            out["malformed"] += 1
        elif s.startswith("http_"):
            try:
                code = int(s.removeprefix("http_"))
            except ValueError:
                out["errors"] += 1
                continue
            if 400 <= code < 500:
                out["http_4xx"] += 1
            elif code >= 500:
                out["http_5xx"] += 1
            else:
                out["errors"] += 1
        else:
            out["errors"] += 1
    return out


def failure_pct(summary: dict[str, int], combos: int) -> float:
    """Share of combos that did NOT yield a usable payload, as a percentage.

    `http_4xx` counts as a failure here even though a 400 is NHTSA's ordinary "unknown combo"
    answer. That is deliberate: coverage is currently **200/200 ok**, so a 4xx appearing at
    all means the fleet's model vocabulary has drifted away from NHTSA's again — which is
    I-030, a bug that has now surfaced three times and whose signature is exactly a rising
    400 count nobody was alerted on.
    """
    if combos <= 0:
        return 0.0
    return 100.0 * (combos - summary.get("ok", 0)) / combos
