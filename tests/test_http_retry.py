"""Unit tests for the retry/validation helper — the whole point of which is that the
interesting cases never have to happen live.

Everything here runs with **no network and no clock**: `fetch_json` takes its `opener` and
`sleeper` as arguments, so a 429 with a `Retry-After`, a truncated body and a shape change
are all ordinary test inputs rather than things you wait for NHTSA to do to you.

The sleeper is asserted on, not just stubbed. A retry test that only checks the final result
passes just as happily against code that retries instantly in a tight loop, which is the
version that gets you rate-limited.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from fleetguard.http_retry import (
    FetchOutcome,
    backoff_delay,
    failure_pct,
    fetch_json,
    parse_retry_after,
    should_retry,
    summarise_statuses,
    validate_recalls_payload,
)


class FakeResponse:
    """Minimal file-like stand-in for what `urlopen` yields."""

    def __init__(self, payload: str):
        self._payload = payload

    def read(self, *_args) -> bytes:
        return self._payload.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeOpener:
    """Replays a scripted sequence of responses/exceptions, one per attempt."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, _url):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class FakeSleeper:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("http://x", code, "boom", headers, None)


# --------------------------------------------------------------------------------------
# should_retry — the policy table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, False),
        (400, False),  # NHTSA's "unknown combo" — deterministic, never retry (I-031)
        (401, False),
        (403, False),
        (404, False),
        (408, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
def test_should_retry_by_status(status, expected):
    assert should_retry(status, None) is expected


def test_400_is_never_retried_even_though_it_is_a_failure():
    """The rule this module exists to encode, asserted on its own so it cannot be
    'simplified' into a generic 4xx-vs-5xx split later."""
    assert should_retry(400, None) is False
    assert should_retry(429, None) is True


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (urllib.error.URLError("dns"), True),
        (TimeoutError("slow"), True),
        (json.JSONDecodeError("trunc", "", 0), True),
        (ValueError("nope"), False),
        (KeyError("Results"), False),
    ],
)
def test_should_retry_by_exception(exc, expected):
    assert should_retry(None, exc) is expected


def test_should_retry_with_neither_is_false():
    assert should_retry(None, None) is False


# --------------------------------------------------------------------------------------
# Retry-After parsing — both documented forms
# --------------------------------------------------------------------------------------


def test_retry_after_delta_seconds():
    assert parse_retry_after("120") == 120.0


def test_retry_after_http_date_is_seconds_from_now():
    # A date far in the future should parse to a large positive delta, not raise.
    delta = parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    assert delta is not None and delta > 0


def test_retry_after_in_the_past_clamps_to_zero():
    assert parse_retry_after("Wed, 21 Oct 1999 07:28:00 GMT") == 0.0


@pytest.mark.parametrize("value", [None, "", "   ", "soon", "not-a-date"])
def test_retry_after_unparseable_degrades_to_none(value):
    """A malformed header must fall back to our own backoff, never raise — a broken header
    is not a reason to fail a request we were about to retry anyway."""
    assert parse_retry_after(value) is None


# --------------------------------------------------------------------------------------
# backoff_delay — bounded, exponential, jittered
# --------------------------------------------------------------------------------------


def test_backoff_is_exponential_at_the_ceiling():
    at_max = lambda: 1.0  # noqa: E731 - full-jitter draw of 1.0 gives the ceiling
    assert backoff_delay(1, base=0.5, cap=100, rand=at_max) == 0.5
    assert backoff_delay(2, base=0.5, cap=100, rand=at_max) == 1.0
    assert backoff_delay(3, base=0.5, cap=100, rand=at_max) == 2.0
    assert backoff_delay(4, base=0.5, cap=100, rand=at_max) == 4.0


def test_backoff_respects_the_cap():
    assert backoff_delay(20, base=0.5, cap=8.0, rand=lambda: 1.0) == 8.0


def test_backoff_jitter_stays_within_zero_and_the_ceiling():
    for attempt in range(1, 6):
        for draw in (0.0, 0.25, 0.5, 0.99, 1.0):
            d = backoff_delay(attempt, base=0.5, cap=8.0, rand=lambda: draw)  # noqa: B023
            assert 0.0 <= d <= min(8.0, 0.5 * 2 ** (attempt - 1))


def test_backoff_default_rand_is_used_when_none_supplied():
    """Guards against a refactor that drops the default and silently returns 0 forever."""
    draws = {backoff_delay(3, base=1.0, cap=100) for _ in range(50)}
    assert len(draws) > 1


def test_retry_after_overrides_computed_backoff_and_is_not_jittered_down():
    assert backoff_delay(1, retry_after=5.0, base=0.5, cap=100, rand=lambda: 0.0) == 5.0


def test_retry_after_is_still_capped():
    """An absurd or hostile Retry-After must not stall a 200-combo sweep."""
    assert backoff_delay(1, retry_after=86400.0, cap=8.0) == 8.0


# --------------------------------------------------------------------------------------
# fetch_json — the loop
# --------------------------------------------------------------------------------------


def test_success_on_first_attempt_does_not_sleep():
    opener, sleeper = FakeOpener('{"results": []}'), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper)
    assert out.ok and out.status == "ok" and out.attempts == 1
    assert out.retried is False
    assert sleeper.delays == []


def test_transient_failure_then_success_is_reported_as_retried_ok():
    opener = FakeOpener(http_error(503), '{"results": [{"a": 1}]}')
    sleeper = FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, rand=lambda: 1.0)
    assert out.ok and out.status == "retried_ok" and out.attempts == 2
    assert out.retried is True
    assert out.body == {"results": [{"a": 1}]}
    assert len(sleeper.delays) == 1, "must have waited before retrying"


def test_a_400_is_returned_immediately_without_retrying():
    opener, sleeper = FakeOpener(http_error(400)), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper)
    assert out.status == "http_400"
    assert opener.calls == 1, "a 400 must cost exactly one request"
    assert sleeper.delays == []


def test_attempts_are_capped_and_the_last_status_is_reported():
    opener, sleeper = FakeOpener(http_error(500)), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, attempts=3, rand=lambda: 1.0)
    assert out.status == "http_500" and out.attempts == 3
    assert opener.calls == 3
    assert len(sleeper.delays) == 2, "n attempts means n-1 sleeps"


def test_429_uses_the_servers_retry_after():
    opener = FakeOpener(http_error(429, retry_after="4"), '{"results": []}')
    sleeper = FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, cap=60)
    assert out.ok
    assert sleeper.delays == [4.0], "Retry-After is an instruction, not a hint"


def test_truncated_body_is_retried_then_reported():
    opener, sleeper = FakeOpener("{not json"), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, attempts=2, rand=lambda: 1.0)
    assert out.status.startswith("error_JSONDecodeError")
    assert opener.calls == 2


def test_non_retryable_exception_stops_immediately():
    opener, sleeper = FakeOpener(ValueError("bad")), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, attempts=3)
    assert out.status == "error_ValueError"
    assert opener.calls == 1
    assert sleeper.delays == []


def test_fetch_json_never_raises_for_a_dead_endpoint():
    """The sweep-level contract: a dead combo costs a combo, not the run."""
    opener, sleeper = FakeOpener(urllib.error.URLError("no route")), FakeSleeper()
    out = fetch_json("u", opener=opener, sleeper=sleeper, attempts=2, rand=lambda: 0.0)
    assert out.ok is False and out.body is None and out.error


def test_attempts_below_one_still_makes_one_call():
    opener, sleeper = FakeOpener('{"results": []}'), FakeSleeper()
    assert fetch_json("u", opener=opener, sleeper=sleeper, attempts=0).ok
    assert opener.calls == 1


# --------------------------------------------------------------------------------------
# validate_recalls_payload — the check whose absence made a shape change invisible
# --------------------------------------------------------------------------------------


def test_valid_payload_passes():
    results, reason = validate_recalls_payload({"Count": 2, "results": [{"a": 1}, {"b": 2}]})
    assert reason is None and len(results) == 2


def test_count_absent_is_fine():
    results, reason = validate_recalls_payload({"results": [{"a": 1}]})
    assert reason is None and len(results) == 1


def test_empty_results_is_valid_not_malformed():
    """Zero recalls for a combo is a real answer and must stay distinguishable from a
    broken response — that distinction is the whole reason this function exists."""
    results, reason = validate_recalls_payload({"Count": 0, "results": []})
    assert reason is None and results == []


def test_count_mismatch_is_rejected():
    _, reason = validate_recalls_payload({"Count": 10, "results": [{"a": 1}]})
    assert reason is not None and "Count=10" in reason


def test_missing_results_key_is_rejected():
    _, reason = validate_recalls_payload({"Message": "Results returned successfully"})
    assert reason == "no 'results' key"


@pytest.mark.parametrize(
    "body",
    [None, [], "a string", 42, {"results": "not a list"}, {"results": [1, 2, 3]}],
)
def test_wrong_shapes_are_rejected(body):
    results, reason = validate_recalls_payload(body)
    assert reason is not None and results == []


# --------------------------------------------------------------------------------------
# Run-level accounting
# --------------------------------------------------------------------------------------


def test_summarise_buckets_every_status_it_can_be_given():
    s = summarise_statuses(
        [
            "ok",
            "ok",
            "retried_ok",
            "http_400",
            "http_429",
            "http_500",
            "malformed",
            "error_URLError",
        ]
    )
    assert s == {
        "ok": 3,
        "retried": 1,
        "http_4xx": 2,
        "http_5xx": 1,
        "malformed": 1,
        "errors": 1,
    }


def test_summarise_handles_an_unparseable_status_string():
    assert summarise_statuses(["http_banana"])["errors"] == 1


def test_failure_pct_is_zero_for_a_clean_sweep():
    assert failure_pct(summarise_statuses(["ok"] * 200), 200) == 0.0


def test_failure_pct_counts_everything_that_is_not_ok():
    s = summarise_statuses(["ok"] * 180 + ["http_400"] * 20)
    assert failure_pct(s, 200) == pytest.approx(10.0)


def test_failure_pct_of_an_empty_sweep_is_zero_not_a_zero_division():
    assert failure_pct({}, 0) == 0.0


def test_fetch_outcome_ok_covers_both_success_labels():
    assert FetchOutcome(status="ok", attempts=1).ok
    assert FetchOutcome(status="retried_ok", attempts=2).ok
    assert not FetchOutcome(status="http_500", attempts=3).ok


def test_httperror_is_a_urlerror_and_must_not_be_treated_as_transient():
    """Regression for a bug this suite caught during development.

    `urllib.error.HTTPError` subclasses `URLError`. The first version of `should_retry`
    tested the exception before the status, so every HTTP status matched the URLError arm
    and answered "retry" — including 400, the one status the policy exists to exclude. The
    ordering in `should_retry` is therefore load-bearing, not cosmetic.
    """
    err = http_error(400)
    assert isinstance(err, urllib.error.URLError), "premise of this regression test"
    assert should_retry(400, err) is False
    assert should_retry(503, err) is True
