"""Will the provider let this run finish?

Our cap answers "how much am I willing to spend". It says nothing about how much the
provider will still *let* us spend, and those are different numbers.

**The failure this exists to catch.** OpenRouter's per-key limit is CUMULATIVE USAGE on the
key, not a per-run allowance. On 2026-09-21 the key limit was raised to $70 for a ~$52 Opus
run, but $39.14 of that had already been spent by the GPT run, leaving $30.86. The run was
fully funded by its own $70 cap and would still have died about 59% of the way through. It
was caught by hand, twice - the first time (LIMITS, 1,045 x 403) only after $35 of phantom
cap had been charged - which is exactly the kind of check a human should not be the one
performing.

**What it does NOT do.** It is not a safety control: the hard cap in `budget.py` is what
bounds spending, and that is enforced before every request regardless. This only prevents a
run that cannot complete, so its failure mode is a wasted partial run, not an overspend.
That asymmetry sets how it treats uncertainty:

* limit is known and smaller than the estimate  -> REFUSE. Cheap to fix, expensive to skip.
* the provider exposes no spend endpoint        -> proceed, saying so. Nothing to check.
* the endpoint is unreachable                   -> proceed, saying so. A transient network
  blip must not block a run whose real protection - the cap - is unaffected.

**The estimate is measured, not assumed.** The worst case (every request emitting all 16,000
output tokens) is roughly $290 for 1,200 Opus tasks - six times what a run actually costs -
so checking against it would refuse every honest run. Instead the estimate is the mean
OBSERVED cost of responses already in the cache for this exact request identity, times the
number of uncached tasks. With a probe cached, that is a real measurement of this model on
this corpus; with nothing cached it falls back to the worst case and says which it used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from eval.providers import ModelConfig, OpenAICompatProvider


@dataclass(frozen=True)
class Headroom:
    """What the provider says is left on this key. Every field may be unknown."""

    limit: float | None = None      # None = no per-key limit is set
    usage: float | None = None
    error: str = ""                 # non-empty = we could not find out

    @property
    def known(self) -> bool:
        return not self.error and self.usage is not None

    @property
    def remaining(self) -> float | None:
        """None when unlimited or unknown - deliberately NOT 0.0 and not infinity, so a
        caller has to handle 'we do not know' rather than accidentally compare against it."""
        if not self.known or self.limit is None:
            return None
        return self.limit - self.usage

    def line(self) -> str:
        if self.error:
            return f"provider headroom UNKNOWN ({self.error})"
        if self.limit is None:
            return f"provider usage ${self.usage:.4f}, no per-key limit set"
        return (f"provider usage ${self.usage:.4f} of ${self.limit:.2f} limit, "
                f"${self.remaining:.4f} left")


def provider_headroom(cfg: ModelConfig, *, timeout: float = 30.0) -> Headroom | None:
    """Ask the provider what is left. `None` when it exposes no such endpoint.

    Only OpenRouter does, among the providers configured here. The Anthropic and OpenAI
    APIs have no per-key spend endpoint, so for those there is genuinely nothing to check
    and this returns None rather than inventing reassurance.
    """
    if cfg.provider != "openrouter":
        return None

    import httpx

    base_url, key_env = OpenAICompatProvider.ENDPOINTS[cfg.provider]
    base_url = os.environ.get(f"{cfg.provider.upper()}_BASE_URL", base_url)
    key = os.environ.get(key_env)
    if not key:
        return Headroom(error=f"no {key_env} in the environment")
    try:
        r = httpx.get(f"{base_url}/key", headers={"Authorization": f"Bearer {key}"},
                      timeout=timeout)
        r.raise_for_status()
        d = r.json()["data"]
    except Exception as exc:                      # noqa: BLE001 - any failure is "unknown"
        return Headroom(error=f"{type(exc).__name__}: {exc}")
    usage = d.get("usage")
    if usage is None:
        return Headroom(error="response carried no usage figure")
    return Headroom(limit=d.get("limit"), usage=float(usage))


def estimate_run_cost(observed: list[float], n_uncached: int,
                      worst_case: float) -> tuple[float, str]:
    """Estimated cost of the uncached requests, and one line saying where it came from.

    `observed` is the actual billed cost of responses already cached for this same request
    identity - same model, same prompt, same sampling params, so a directly comparable
    sample of the same workload.
    """
    if not n_uncached:
        return 0.0, "nothing uncached"
    if observed:
        mean = sum(observed) / len(observed)
        return mean * n_uncached, (f"${mean:.4f}/task measured over {len(observed)} cached "
                                   f"response(s) x {n_uncached} uncached")
    return worst_case, "worst case (no cached response for this model to measure)"


def check_headroom(cfg: ModelConfig, *, estimate: float, cap: float,
                   basis: str = "", log=print) -> bool:
    """False means DO NOT START. Prints what it found either way.

    Also warns - without refusing - when the provider's remaining balance is below our own
    cap. That run can complete, but its real ceiling is the provider's number, not the one
    on the command line, and a reader of the log should not have to work that out.
    """
    hr = provider_headroom(cfg)
    if hr is None:
        log(f"  headroom: {cfg.provider} exposes no spend endpoint; not checked")
        return True
    log(f"  headroom: {hr.line()}")
    if not hr.known:
        log("  headroom could not be confirmed; starting anyway - the hard cap is "
            "unaffected and a partial run is recoverable from the cache")
        return True

    log(f"  estimated cost of this run: ${estimate:.2f} ({basis})")
    left = hr.remaining
    if left is None:
        return True
    if left < estimate:
        log(f"  REFUSING TO START: ${left:.2f} left on the key is less than the ${estimate:.2f} "
            f"this run is estimated to cost. The provider limit is cumulative usage on the "
            f"key, not a per-run allowance - raise it to at least "
            f"${hr.usage + estimate:.2f} (usage so far + this run), or run with --limit.")
        return False
    if left < cap:
        log(f"  NOTE: ${left:.2f} left on the key is below the ${cap:.2f} cap, so the "
            f"provider's limit - not the cap - is what would actually stop this run.")
    return True
