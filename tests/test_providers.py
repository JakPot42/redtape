"""Provider abstraction and the hard budget cap.

Inputs here come from the far side of each interface, never from our own constructors:

* OpenAI-compatible responses are raw JSON bodies served over an httpx MockTransport and
  parsed by the real `openai` SDK - so what is tested is wire -> SDK -> adapter -> Turn,
  the path a real OpenRouter response takes. A test that built `Turn(...)` by hand would be
  the LIMITS §25 tautology again.
* The Opus cache-compatibility test reads the COMMITTED cache on disk. It is the only thing
  that proves the refactor did not silently re-key ~$60 of paid responses.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from eval.budget import Budget, BudgetExhausted, NoBudget
from eval.cache import cache_key, get as cache_get, partition
from eval.providers import MAX_TOKENS, MODELS, OpenAICompatProvider, get_model

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data" / "dev" / "t1.jsonl"


# ------------------------------------------------------------------ fake wire


def _openrouter_body(content, finish="stop", cost=0.0123, reasoning=900, refusal=None):
    return {
        "id": "gen-test", "object": "chat.completion", "created": 1, "model": "openai/gpt-5.6-sol",
        "provider": "OpenAI",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content, "refusal": refusal}}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 1200, "total_tokens": 2700,
                  "completion_tokens_details": {"reasoning_tokens": reasoning},
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "cost": cost},
    }


def _provider_with(body, seen: list):
    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    import openai
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.cfg = get_model("gpt-5.6-sol")
    p.env_keys = ("OPENROUTER_API_KEY",)
    p.client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key="test",
                             max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return p


def test_openrouter_request_carries_pinned_routing_and_effort():
    seen = []
    p = _provider_with(_openrouter_body('{"a": 1}'), seen)
    p.complete("SYS", [p.user_message("case file")], [])
    body = seen[0]
    assert body["model"] == "openai/gpt-5.6-sol"
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "case file"}
    assert body["max_tokens"] == MAX_TOKENS == 16_000
    assert body["reasoning"] == {"effort": "high"}
    assert body["provider"]["only"] == ["openai"]
    assert body["provider"]["allow_fallbacks"] is False
    assert "temperature" not in body


def test_openrouter_response_normalised_including_reported_cost():
    p = _provider_with(_openrouter_body('{"a": 1}'), [])
    t = p.complete("S", [p.user_message("x")], [])
    assert t.text == '{"a": 1}'
    assert t.stop == "end" and t.raw_stop == "stop"
    assert t.usage == {"input_tokens": 1500, "output_tokens": 1200, "reasoning_tokens": 900}
    assert t.reported_cost_usd == pytest.approx(0.0123)
    assert t.served_by == "OpenAI"


def test_token_limit_is_recorded_as_length_not_as_an_empty_answer():
    """Reasoning spends the same max_tokens as the answer. A response cut off there has no
    JSON; it must be visible as `length`, not scored silently as the model's failure."""
    p = _provider_with(_openrouter_body(None, finish="length"), [])
    t = p.complete("S", [p.user_message("x")], [])
    assert t.stop == "length"
    assert t.text == ""


def test_refusal_is_normalised():
    p = _provider_with(_openrouter_body(None, refusal="I can't help with that."), [])
    t = p.complete("S", [p.user_message("x")], [])
    assert t.stop == "refusal" and t.refusal


def test_tool_conditions_are_refused_not_half_supported():
    p = _provider_with(_openrouter_body("{}"), [])
    with pytest.raises(NotImplementedError):
        p.complete("S", [p.user_message("x")], [{"name": "calculate"}])


# ------------------------------------------------------------------ cache compatibility


def test_no_committed_response_is_served_for_the_changed_prompt():
    """The committed Opus 5 responses answer the PRE-2026-09-19 prompt. That prompt changed
    (closed fact vocabulary, no real fact in the worked example), so every one of them must
    now MISS. A cache that served an answer written for a different prompt would put a
    withdrawn result back into a new one without any error (LIMITS §36).

    This replaced a test asserting 1200/1200 hits, which was right until the prompt
    deliberately changed."""
    from eval.run_eval import load_tasks, request_identity, task_cache_key

    tasks = load_tasks(str(DEV))
    cfg, system, tools = request_identity("tool_less", "claude-opus-5")
    hits = sum(cache_get(task_cache_key(t, cfg, system, tools), partition(t.data.seed))
               is not None for t in tasks)
    assert hits == 0


def _plant(tmp_path, tasks, model="claude-opus-5"):
    """Write fake cached responses for `tasks` into an isolated cache dir. The reply is the
    task's own answer key, so it parses and scores; usage is small and fixed."""
    import eval.cache as cache_mod
    from eval.run_eval import request_identity, task_cache_key

    cfg, system, tools = request_identity("tool_less", model)
    old = cache_mod.CACHE_DIR
    cache_mod.CACHE_DIR = tmp_path
    try:
        for t in tasks:
            cache_mod.put(task_cache_key(t, cfg, system, tools),
                          {"reply": t.data.answer_key.model_dump_json(),
                           "usage": {"input_tokens": 100, "output_tokens": 100}},
                          partition(t.data.seed))
    finally:
        cache_mod.CACHE_DIR = old


def test_changing_a_sampling_parameter_rekeys_the_cache():
    """A different sampling configuration is a different request.

    This replaced a test asserting the Opus key still matched the PRE-PROVIDER params dict
    (max_tokens 8,000). That identity was deliberately broken on 2026-09-20 when max_tokens
    went to 16,000 for both models, so the property worth pinning is the one that keeps a
    response produced under one configuration from being served for another."""
    cfg = get_model("claude-opus-5")
    key = cache_key(model=cfg.cache_model, system="s", prompt="p", tools=[],
                    params=cfg.params)
    for changed in ({**cfg.params, "max_tokens": 8_000},
                    {**cfg.params, "output_config": {"effort": "low"}}):
        assert cache_key(model=cfg.cache_model, system="s", prompt="p", tools=[],
                         params=changed) != key


def test_different_providers_never_share_a_cache_key():
    keys = {cache_key(model=c.cache_model, system="s", prompt="p", tools=[], params=c.params)
            for c in MODELS.values()}
    assert len(keys) == len(MODELS)


# ------------------------------------------------------------------ budget


def _budget(cap):
    return Budget(cap, price_in=2.0, price_out=10.0, max_output_tokens=8000)


def test_budget_has_no_default():
    for bad in (None, 0, -1):
        with pytest.raises(ValueError):
            Budget(bad, price_in=1, price_out=1, max_output_tokens=1)


def test_budget_refuses_before_sending():
    b = _budget(0.05)            # worst case per request is ~0.08
    with pytest.raises(BudgetExhausted):
        b.reserve("x" * 1000)
    assert b.spent == 0 and b.in_flight == 0 and b.exhausted


def test_budget_never_exceeded_under_concurrency():
    """16 threads hammering a $1 cap; every request actually costs its full worst case.
    Total spend must stay <= cap - the property a post-call check cannot give."""
    b = _budget(1.00)
    sent = []
    lock = threading.Lock()

    def worker():
        for _ in range(2_000):      # bounded: a missing cap must FAIL this test, not hang it
            try:
                r = b.reserve("y" * 500)
            except BudgetExhausted:
                return
            with lock:
                sent.append(r)
            b.settle(r, r)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert b.spent <= b.cap
    assert sum(sent) == pytest.approx(b.spent)
    assert len(sent) >= 10       # it did run, and stopped near the cap rather than at zero


def test_in_flight_reservations_count_against_the_cap():
    """The property that makes the cap hard under concurrency, tested deterministically.

    The threaded test above passed even with in-flight reservations IGNORED - each thread
    settled immediately, so they rarely overlapped and it could not see the difference.
    Here two requests are open at once with room for only one: the second must be refused
    before it is sent, or both settle and the total lands over the cap."""
    b = _budget(1.0)
    w = b.worst_case("q")
    b.cap = 1.5 * w
    first = b.reserve("q")
    with pytest.raises(BudgetExhausted):
        b.reserve("q")                  # first is still in flight
    b.settle(first, w)
    assert b.spent <= b.cap


def test_failed_request_is_charged_not_refunded():
    b = _budget(1.00)
    r = b.reserve("z")
    b.charge_failed(r)
    assert b.spent == pytest.approx(r) and b.in_flight == 0


def test_live_agent_without_budget_serves_hits_but_refuses_misses(monkeypatch, tmp_path):
    """No budget: a cache hit is served, a miss raises NoBudget and nothing is built.
    Both are made certain with an isolated cache, independent of what this machine paid for."""
    import eval.cache as cache_mod
    from eval.run_eval import live_agent, load_tasks

    task = load_tasks(str(DEV), limit=1)[0]
    _plant(tmp_path, [task])
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    assert live_agent("tool_less", "claude-opus-5")(task)          # planted: a hit
    with pytest.raises(NoBudget):
        live_agent("tool_less", "gpt-5.6-sol")(task)               # never planted: a miss


def test_cli_refuses_paid_run_without_cap_before_any_request(tmp_path):
    """Through the real entry point: no --max-usd, uncached model -> exit 2, nothing sent.
    The credential is deliberately absent too; the cap check must fire first."""
    # An empty cache makes every request a miss, whatever this machine has paid for.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
           "REDTAPE_CACHE_DIR": str(tmp_path / "empty_cache")}
    out_dir = tmp_path / "results"
    r = subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "live", "--model", "gpt-5.6-sol",
         "--split", str(DEV), "--sample", "10", "--results", str(out_dir)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "10 uncached" in r.stdout
    assert "--max-usd" in r.stderr
    assert not out_dir.exists()


def test_cached_rescore_counts_each_hit_once_through_the_cli(tmp_path):
    """Through `python -m eval.run_eval live`: 10 cached Opus tasks -> 10 hits, not 20.

    Pre-warm counted every hit and the scoring pass counted it again; a committed results
    file (t1_live300.live.tool_less.json) records 600 hits for 300 tasks because of it."""
    from eval.run_eval import load_tasks

    cache_dir, out_dir = tmp_path / "cache", tmp_path / "results"
    _plant(cache_dir, load_tasks(str(DEV), sample=10))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
           "REDTAPE_CACHE_DIR": str(cache_dir)}
    r = subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "live", "--model", "claude-opus-5",
         "--split", str(DEV), "--sample", "10", "--results", str(out_dir)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads((out_dir / "t1.live.claude-opus-5.tool_less.json").read_text())
    usage = out["run"]["usage"]
    assert usage["cached"]["n"] == 10
    assert usage["billed"]["n"] == 0


# ------------------------------------------------------------------ unbilled refusals


class _Refused(Exception):
    """Shaped like an SDK status error."""
    def __init__(self, status): self.status_code = status


class _Conn(Exception):
    pass


_Conn.__name__ = "APIConnectionError"


def test_provider_refusals_are_recognised_as_unbilled():
    from eval.providers import is_unbilled_error

    for status in (401, 403, 429, 400):
        assert is_unbilled_error(_Refused(status)), status
    assert is_unbilled_error(_Conn())
    # Billing is UNKNOWN for these, so the conservative charge stands.
    for status in (500, 502, 529):
        assert not is_unbilled_error(_Refused(status)), status

    class APITimeoutError(Exception):
        pass

    assert not is_unbilled_error(APITimeoutError())


def test_a_refused_request_does_not_consume_the_cap(monkeypatch, tmp_path):
    """The real failure this came from: OpenRouter refused 1,045 requests with 403 after the
    key hit its spending limit, every one was charged its worst case, and the cap read
    $39.84 of $40 against $4.48 actually spent. A cap exhausted by unbilled refusals stops
    the NEXT run early."""
    import eval.cache as cache_mod
    import eval.run_eval as run_eval
    from eval.budget import Budget
    from eval.providers import get_model

    class RefusingProvider:
        def user_message(self, text):
            return {"role": "user", "content": text}

        def complete(self, system, messages, tools):
            raise _Refused(403)

    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(run_eval, "make_provider", lambda cfg: RefusingProvider())
    cfg = get_model("gpt-5.6-sol")
    budget = Budget(40.0, price_in=cfg.price_in, price_out=cfg.price_out,
                    max_output_tokens=cfg.max_output_tokens)
    agent = run_eval.live_agent("tool_less", "gpt-5.6-sol", budget=budget)
    task = run_eval.load_tasks(str(DEV), limit=1)[0]
    for _ in range(50):
        with pytest.raises(_Refused):
            agent(task)
    assert budget.spent == 0.0
    assert budget.in_flight == 0.0
    assert budget.refused_unbilled == 50


def test_a_run_that_cannot_fetch_still_scores_what_it_has(tmp_path):
    """A partial run must score the fetched tasks and say how many were missed.

    This path has broken twice. It first survived 1,045 provider 403s only BY ACCIDENT:
    every refusal was charged its worst case, the cap "exhausted", and that triggered the
    cached-only fallback. Once refusals correctly stopped consuming the cap, the same run
    died with an unhandled provider error and wrote no results at all. The condition is now
    "is anything still uncached", which does not depend on the cap.

    The provider is made unreachable by pointing it at a closed port, so no network call and
    no billing can occur."""
    from eval.run_eval import load_tasks

    cache_dir, out_dir = tmp_path / "cache", tmp_path / "results"
    planted = load_tasks(str(DEV), limit=6)
    _plant(cache_dir, planted, model="gpt-5.6-sol")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
           "REDTAPE_CACHE_DIR": str(cache_dir),
           "OPENROUTER_API_KEY": "test-not-used",
           "OPENROUTER_BASE_URL": "http://127.0.0.1:9/v1"}
    r = subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "live", "--model", "gpt-5.6-sol",
         "--split", str(DEV), "--limit", "12", "--max-usd", "5.00",
         "--results", str(out_dir)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "were never fetched" in r.stdout, r.stdout[-2000:]
    out = json.loads((out_dir / "t1.live.gpt-5.6-sol.tool_less.json").read_text())
    assert out["diagnostics"]["n_tasks"] == 6
    # Unreachable is unbilled: the cap must be untouched.
    assert out["run"]["budget"]["spent_usd"] == 0.0
    assert out["run"]["budget"]["provider_refusals_unbilled"] == 6
