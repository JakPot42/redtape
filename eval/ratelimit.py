"""Client-side request pacing, and bounded retry on a rate-limit refusal.

**Why this is not the SDK's retry.** `max_retries=0` is set on every client deliberately:
an SDK retry is a second billed attempt that the budget never saw reserved, so the cap
could be crossed by requests it never counted. That reasoning does not apply to a 429. A
rate-limit refusal generated nothing and billed nothing (`providers.is_unbilled_error`), and
the retry here goes through `Budget.reserve` like any other request, so every attempt is
counted exactly once and the cap still binds.

**The run this was written for.** 2026-09-21, Opus 5 via OpenRouter, 8 workers: 1,014 of the
first 1,050 requests came back

    Rate limit exceeded: new-account-rpm/anthropic/claude-opus-5-20260723.
    Rate limit reached: new accounts are limited to 20 requests per minute for this model.

Nothing was mis-billed - the unbilled classification held, and $0.46 of real responses were
cached - but the run could not proceed and would have reported 1,014 "errors" that have
nothing to do with the model. Concurrency was tuned for a provider that had no such limit.

Two mechanisms, because they fail differently:

* `RateLimiter` paces requests so the limit is not hit in the first place. It is the cheap
  path and it keeps the log readable.
* Retry-on-429 is what makes the run correct anyway when the pace is wrong - the published
  limit is not always the enforced one, and an account's tier can change under us. Pacing
  alone would be a guess asserted as fact.
"""

from __future__ import annotations

import random
import threading
import time


class RateLimiter:
    """Allow at most `rpm` acquisitions per rolling minute, across all threads.

    Spacing, not a bucket: a bucket of size N lets N requests leave together the moment it
    refills, which is exactly the burst a per-minute limit rejects. Each caller instead
    waits until its own slot, `60/rpm` after the previous one.
    """

    def __init__(self, rpm: float | None):
        self.rpm = rpm
        self._interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until this request may be sent. Returns how long it waited."""
        if not self._interval:
            return 0.0
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self._interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)
        return max(0.0, delay)


def retry_delay(exc: BaseException, attempt: int, *, base: float = 2.0,
                cap: float = 60.0) -> float:
    """Seconds to wait before retrying a rate-limited request.

    The provider's own `Retry-After` wins when it sends one - it knows when the window
    reopens and we are guessing. Otherwise exponential backoff with full jitter, because
    N workers that all back off by the same amount re-collide on the same slot.
    """
    for attr in ("response", "http_response"):
        resp = getattr(exc, attr, None)
        headers = getattr(resp, "headers", None)
        if not headers:
            continue
        for name in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset"):
            raw = headers.get(name)
            if raw is None:
                continue
            try:
                secs = float(str(raw).rstrip("s"))
            except ValueError:
                continue
            # Some providers report the reset as a millisecond epoch rather than a delay.
            if secs > 10_000:
                secs = max(0.0, secs / 1000.0 - time.time())
            if 0 <= secs <= cap:
                return secs
    return min(cap, base * (2 ** attempt)) * random.random()


def is_rate_limit(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError"
