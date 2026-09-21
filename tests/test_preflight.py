"""The provider-headroom preflight, tested for teeth.

CLAUDE.md, 2026-09-18: any control we assert exists needs a test that FAILS when the
control is absent. So the central test here does not check a message - it runs the real
`live` entry point against a stub provider and asserts that **no completion request was
ever sent**. Delete the preflight from `run_eval.main` and the run reaches the stub's
`/chat/completions`, the counter moves, and this test goes red.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from eval.preflight import Headroom, check_headroom, estimate_run_cost, provider_headroom
from eval.providers import get_model

REPO = Path(__file__).resolve().parents[1]
SPLIT = REPO / "data" / "dev" / "t1_smoke.jsonl"

MODEL = "claude-opus-5-openrouter"   # the only OpenRouter model whose provider exposes /key


# ------------------------------------------------------------------ stub provider
class _Handler(BaseHTTPRequestHandler):
    """Serves /key with whatever the test set, and counts completion attempts."""

    limit: float | None = 70.0
    usage: float = 39.14
    completions: int = 0

    def log_message(self, *a):        # keep pytest output clean
        pass

    def _json(self, code: int, body: dict):
        blob = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        if self.path.endswith("/key"):
            self._json(200, {"data": {"usage": type(self).usage, "limit": type(self).limit}})
        else:
            self._json(404, {"error": "no"})

    def do_POST(self):
        type(self).completions += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        self._json(200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0}})


@pytest.fixture
def stub():
    _Handler.completions = 0
    _Handler.limit, _Handler.usage = 70.0, 39.14
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", _Handler
    srv.shutdown()


# ------------------------------------------------------------------ reading the provider
def test_headroom_is_read_from_the_provider(stub, monkeypatch):
    url, h = stub
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    hr = provider_headroom(get_model(MODEL))
    assert hr.known and hr.limit == 70.0 and hr.usage == 39.14
    assert hr.remaining == pytest.approx(30.86)


def test_an_unreachable_provider_is_unknown_not_zero(monkeypatch):
    """Must not read as 'no money left' - that would block a run over a network blip."""
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9")  # discard port
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    hr = provider_headroom(get_model(MODEL), timeout=2.0)
    assert not hr.known and hr.remaining is None and hr.error


def test_no_per_key_limit_means_no_constraint(stub, monkeypatch):
    url, h = stub
    h.limit = None
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    hr = provider_headroom(get_model(MODEL))
    assert hr.known and hr.remaining is None
    assert check_headroom(get_model(MODEL), estimate=1e6, cap=10.0, log=lambda *_: None)


def test_a_provider_with_no_spend_endpoint_is_not_checked():
    assert provider_headroom(get_model("claude-opus-5")) is None
    assert check_headroom(get_model("claude-opus-5"), estimate=1e6, cap=1.0,
                          log=lambda *_: None)


# ------------------------------------------------------------------ the decision
def test_refuses_when_the_key_has_less_left_than_the_run_costs(stub, monkeypatch):
    """The 2026-09-21 case, exactly: $70 limit, $39.14 already used, a $52 run."""
    url, h = stub
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    lines: list[str] = []
    ok = check_headroom(get_model(MODEL), estimate=52.0, cap=70.0, log=lines.append)
    assert ok is False
    said = " ".join(lines)
    assert "REFUSING TO START" in said
    assert "$91.14" in said, f"must name the figure to raise the limit TO: {said}"


def test_allows_a_run_that_fits_but_says_the_key_is_the_real_ceiling(stub, monkeypatch):
    url, h = stub
    h.usage = 39.14
    h.limit = 95.0
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    lines: list[str] = []
    assert check_headroom(get_model(MODEL), estimate=52.0, cap=70.0, log=lines.append)
    said = " ".join(lines)
    assert "NOTE" in said and "55.86" in said, said


def test_an_unknown_headroom_does_not_block(monkeypatch):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub")
    assert check_headroom(get_model(MODEL), estimate=1e6, cap=1.0, log=lambda *_: None)


# ------------------------------------------------------------------ the estimate
def test_the_estimate_is_measured_from_cached_responses_when_there_are_any():
    est, basis = estimate_run_cost([0.04, 0.05, 0.03], 1000, worst_case=290.0)
    assert est == pytest.approx(40.0)
    assert "measured over 3" in basis


def test_the_estimate_falls_back_to_the_worst_case_and_says_so():
    est, basis = estimate_run_cost([], 1000, worst_case=290.0)
    assert est == 290.0 and "worst case" in basis


def test_nothing_uncached_costs_nothing():
    assert estimate_run_cost([0.04], 0, worst_case=290.0)[0] == 0.0


# ------------------------------------------------------------------ TEETH
@pytest.mark.skipif(not SPLIT.exists(), reason="smoke split not built")
def test_the_live_entry_point_sends_nothing_when_the_key_is_short(stub, tmp_path):
    """The control itself, through the real CLI.

    The stub would happily answer a completion - so if the preflight is removed, deleted or
    bypassed, `completions` moves above zero and this fails. That is the point: the check is
    asserted to exist, so its absence must be what breaks.
    """
    url, h = stub
    h.limit, h.usage = 40.0, 39.99          # $0.01 left; any real run exceeds it
    env = os.environ | {
        "OPENROUTER_BASE_URL": url,
        "OPENROUTER_API_KEY": "stub",
        "REDTAPE_CACHE_DIR": str(tmp_path / "cache"),   # nothing cached -> everything bills
        "PYTHONPATH": str(REPO),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "live", "--split", str(SPLIT),
         "--model", MODEL, "--max-usd", "70", "--results", str(tmp_path / "results")],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    assert h.completions == 0, (
        f"{h.completions} completion request(s) were sent although the key had $0.01 left - "
        f"the preflight did not stop the run.\n{proc.stdout}\n{proc.stderr}"
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "REFUSING TO START" in proc.stdout


@pytest.mark.skipif(not SPLIT.exists(), reason="smoke split not built")
def test_the_same_run_proceeds_when_the_key_has_room(stub, tmp_path):
    """The complement: the preflight must not be a blanket block. Same command, same stub,
    only the key's limit differs - and now requests are sent."""
    url, h = stub
    h.limit, h.usage = 10_000.0, 0.0
    env = os.environ | {
        "OPENROUTER_BASE_URL": url,
        "OPENROUTER_API_KEY": "stub",
        "REDTAPE_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(REPO),
    }
    subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "live", "--split", str(SPLIT),
         "--model", MODEL, "--max-usd", "70", "--results", str(tmp_path / "results")],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    assert h.completions > 0, "the preflight blocked a run that had room"


def test_headroom_formats_every_state_without_crashing():
    for hr in (Headroom(limit=70.0, usage=39.14), Headroom(limit=None, usage=1.0),
               Headroom(error="boom")):
        assert hr.line()
