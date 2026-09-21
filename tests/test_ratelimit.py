"""Pacing and rate-limit retry.

The property that matters is not "it retries" - it is that **a retry is still counted by
the budget**. The SDK's own retries are disabled precisely because they are not, so a retry
added here has to carry that guarantee or it reintroduces the hole it was written around.
"""

from __future__ import annotations

import threading
import time

import pytest

from eval.budget import Budget, BudgetExhausted
from eval.ratelimit import RateLimiter, is_rate_limit, retry_delay


class _Resp:
    def __init__(self, headers):
        self.headers = headers


class _RateLimited(Exception):
    status_code = 429

    def __init__(self, headers=None):
        super().__init__("rate limited")
        self.response = _Resp(headers or {})


# ------------------------------------------------------------------ pacing
def test_no_rpm_means_no_delay():
    lim = RateLimiter(None)
    t0 = time.monotonic()
    for _ in range(50):
        lim.acquire()
    assert time.monotonic() - t0 < 0.1


def test_requests_are_spaced_not_bursted():
    """A token bucket would let all 5 leave at once, which is the burst a per-minute limit
    rejects. 600 rpm = 0.1s apart, so 5 acquisitions take at least 0.4s."""
    lim = RateLimiter(600)
    t0 = time.monotonic()
    for _ in range(5):
        lim.acquire()
    assert time.monotonic() - t0 >= 0.39


def test_the_limit_is_shared_across_threads():
    """The provider's limit is per account. One limiter, 8 threads, 16 requests at 600rpm
    must still take ~1.5s - if each thread paced itself it would take a fifth of that."""
    lim = RateLimiter(600)
    t0 = time.monotonic()
    threads = [threading.Thread(target=lambda: [lim.acquire() for _ in range(2)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - t0 >= 1.4


# ------------------------------------------------------------------ backoff
def test_the_providers_retry_after_wins_over_our_guess():
    assert retry_delay(_RateLimited({"retry-after": "7"}), attempt=0) == 7.0
    assert retry_delay(_RateLimited({"retry-after": "3s"}), attempt=5) == 3.0


def test_an_absurd_retry_after_is_ignored_rather_than_obeyed():
    """A header that would park the run for an hour is not honoured; backoff is capped."""
    d = retry_delay(_RateLimited({"retry-after": "9999"}), attempt=0, cap=60.0)
    assert 0 <= d <= 60.0


def test_a_millisecond_epoch_reset_is_read_as_a_delay():
    soon = (time.time() + 5) * 1000
    assert retry_delay(_RateLimited({"x-ratelimit-reset": str(soon)}), 0) == pytest.approx(5, abs=1)


def test_backoff_grows_and_is_jittered():
    """Full jitter: N workers backing off by the same amount re-collide on one slot."""
    samples = [retry_delay(_RateLimited(), attempt=3) for _ in range(40)]
    assert len(set(samples)) > 1, "identical delays would resynchronise every worker"
    assert all(0 <= s <= 16.0 for s in samples)


def test_is_rate_limit_recognises_the_sdk_error_by_status_and_by_name():
    assert is_rate_limit(_RateLimited())

    class RateLimitError(Exception):
        pass

    assert is_rate_limit(RateLimitError("no status attribute"))
    assert not is_rate_limit(ValueError("something else"))


# ------------------------------------------------------------------ the guarantee
def test_every_retry_goes_through_the_cap():
    """The reason this retry lives here and not in the SDK.

    An SDK retry is invisible to the budget, so the cap can be crossed by requests it never
    counted. Here every attempt reserves first, so as soon as attempts carry real cost the
    cap refuses - and it refuses BEFORE sending, not after.
    """
    budget = Budget(1.00, price_in=5.0, price_out=25.0, max_output_tokens=16_000)
    assert budget.worst_case("x" * 100) == pytest.approx(0.4005, abs=1e-3)

    sent = 0
    with pytest.raises(BudgetExhausted):
        for _ in range(10):
            reserved = budget.reserve("x" * 100)
            sent += 1
            budget.charge_failed(reserved)      # billing unknown: charged conservatively
    assert sent == 2, f"a $1.00 cap affords two $0.40 attempts, not {sent}"
    assert budget.refused == 1


def test_a_rate_limited_task_recovers_through_the_real_agent(tmp_path, monkeypatch):
    """End to end: the provider 429s twice, then answers, and the task lands in the cache.

    This is the control the 2026-09-21 run needed and did not have. It runs through
    `live_agent` - the real request path, real budget, real cache - against a stub that
    behaves exactly as OpenRouter did. Without the retry the first 429 propagates, the task
    stays uncached, and this fails.
    """
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    pytest.importorskip("verifiers.v1", reason="v1 requires Linux")
    from pathlib import Path

    from eval.cache import get as cache_get, partition
    from eval.run_eval import live_agent, load_tasks, task_cache_key
    from eval.providers import get_model

    split = Path(__file__).resolve().parents[1] / "data" / "dev" / "t1_smoke.jsonl"
    if not split.exists():
        pytest.skip("smoke split not built")

    class H(BaseHTTPRequestHandler):
        attempts = 0

        def log_message(self, *a):
            pass

        def do_POST(self):
            H.attempts += 1
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            if H.attempts <= 2:
                body = {"error": {"message": "Rate limit exceeded", "code": 429}}
                blob, code = json.dumps(body).encode(), 429
            else:
                body = {"choices": [{"message": {"content": '{"ok": true}'},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}}
                blob, code = json.dumps(body).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "0")     # keep the test fast, exercise the header
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("OPENROUTER_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
        monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
        monkeypatch.setenv("REDTAPE_CACHE_DIR", str(tmp_path))
        import eval.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)

        model = "claude-opus-5-openrouter"
        cfg = get_model(model)
        budget = Budget(5.00, price_in=cfg.price_in, price_out=cfg.price_out,
                        max_output_tokens=cfg.max_output_tokens)
        task = load_tasks(str(split), limit=1)[0]
        agent = live_agent("tool_less", model, budget=budget, rpm=0)
        reply = agent(task)

        assert reply == '{"ok": true}'
        assert H.attempts == 3, f"expected two refusals then a success, got {H.attempts}"
        assert budget.refused_unbilled == 2, "both 429s must be released, not charged"
        assert budget.spent > 0, "the successful attempt must still be billed"
        from eval.run_eval import request_identity
        _, system, tools = request_identity("tool_less", model)
        assert cache_get(task_cache_key(task, cfg, system, tools),
                         partition(task.data.seed)) is not None
    finally:
        srv.shutdown()


def test_an_unbilled_retry_does_not_consume_the_cap():
    """The complement, and why 1,045 refusals in a real run no longer end it: released
    reservations leave `spent` untouched, so the run can still proceed once the window
    reopens."""
    budget = Budget(1.00, price_in=5.0, price_out=25.0, max_output_tokens=16_000)
    for _ in range(50):
        budget.release_unbilled(budget.reserve("x" * 10))
    assert budget.spent == 0.0
    assert budget.in_flight == pytest.approx(0.0)
    assert budget.refused_unbilled == 50
