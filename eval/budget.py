"""A hard spending cap, enforced inside the request loop.

CLAUDE.md has required since 2026-09-04 that every paid run take a cap on the command line,
"checked after every API call". **That rule was committed as prose and never implemented:**
until this module existed `run_eval.py` had no cap of any kind. A documented control that
does not exist is the same failure as the determinism "CI" that did not exist - work gets
approved on the strength of it.

Checking *after* each call is not enough to make a cap hard. With N concurrent workers, up
to N requests are in flight when the total crosses the line, so the run overshoots by N
responses. This cap is enforced **before** each request instead:

1. Reserve the request's worst-case cost: every input byte counted as a token (BPE never
   produces more tokens than bytes), plus `max_output_tokens` at the output price.
2. Refuse to send if spent + all in-flight reservations + this reservation would exceed the
   cap. No request is sent that could carry the total past it.
3. After the response, release the reservation and add the actual cost - the larger of our
   own computation and the provider-reported figure, so a pricing-table error can only make
   the cap more conservative, never less.

So the total billed is <= the cap by construction, whatever the concurrency, provided the
provider honours `max_tokens` - which is the one assumption, and is stated here rather than
hidden. There is deliberately no default cap: `Budget` cannot be built without a figure.
"""

from __future__ import annotations

import threading


class BudgetExhausted(RuntimeError):
    """Raised INSTEAD of sending a request that could cross the cap."""


class NoBudget(RuntimeError):
    """A billed request was attempted with no cap configured. Cache hits never raise this."""


class Budget:
    def __init__(self, cap_usd: float, *, price_in: float, price_out: float,
                 max_output_tokens: int):
        if cap_usd is None or not cap_usd > 0:
            raise ValueError("a paid run needs an explicit positive --max-usd; there is no default")
        self.cap = float(cap_usd)
        self.price_in = price_in
        self.price_out = price_out
        self.max_output_tokens = max_output_tokens
        self.spent = 0.0
        self.in_flight = 0.0
        self.refused = 0
        self._lock = threading.Lock()

    def worst_case(self, request_text: str) -> float:
        in_tokens_bound = len(request_text.encode("utf-8")) + 64
        return (in_tokens_bound * self.price_in
                + self.max_output_tokens * self.price_out) / 1_000_000

    def reserve(self, request_text: str) -> float:
        amount = self.worst_case(request_text)
        with self._lock:
            if self.spent + self.in_flight + amount > self.cap:
                self.refused += 1
                raise BudgetExhausted(
                    f"cap ${self.cap:.2f}: spent ${self.spent:.4f} + in flight "
                    f"${self.in_flight:.4f} + worst case ${amount:.4f} would exceed it")
            self.in_flight += amount
        return amount

    def settle(self, reserved: float, actual_usd: float) -> None:
        with self._lock:
            self.in_flight -= reserved
            self.spent += actual_usd

    def charge_failed(self, reserved: float) -> None:
        """A request that raised. It is charged at its full worst case, NOT refunded.

        We cannot tell from an exception whether the provider billed - a timeout can land
        after the generation completed. Refunding would under-count exactly when we do not
        know; charging the reservation over-counts by a bounded amount. Over-counting only
        stops a run early, which is recoverable from the cache. The SDK's own retries are
        disabled so no attempt is invisible to the budget.
        """
        with self._lock:
            self.in_flight -= reserved
            self.spent += reserved

    @property
    def exhausted(self) -> bool:
        return self.refused > 0

    def line(self) -> str:
        return f"budget ${self.spent:.4f} / ${self.cap:.2f} cap"
