"""Model providers: a model is a config value, not a code path.

Before this module the harness called `anthropic.Anthropic().messages.create` inline and had
never run against another lab. Everything provider-specific now lives here, behind one
method - `complete()` - that takes the same (system, messages, tools) every time and returns
a `Turn` in one normalised shape. `live_agent` knows nothing about wire formats.

Two adapters:

* `AnthropicProvider` - the native Messages API, exactly as before. Its cache identity is
  **deliberately unchanged**: the model id is the bare `claude-opus-5` and the params dict is
  byte-identical to the one that produced the committed Opus run, so every one of the 1,200
  paid Opus responses still hits. A refactor that silently re-keyed the cache would re-bill
  ~$60 on the next re-score; `tests/test_providers.py` asserts it does not.
* `OpenAICompatProvider` - any OpenAI-compatible chat-completions endpoint. Configured for
  OpenRouter, which reaches every lab with one key and one wire format, and reports the
  billed cost of each request in `usage.cost`. Pointing `base_url` at api.openai.com makes
  it a direct OpenAI client with no code change.

**Stop reasons are normalised and kept.** OpenAI-family models spend reasoning tokens out of
the same `max_tokens` budget as the visible answer, so a request can end at the token limit
having produced no JSON at all. Scored naively that is `no_json_found` - a model failure -
when it is really a harness budget too small for the model. `Turn.stop` records `length` so
that case is counted and visible rather than folded into the model's score.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# ------------------------------------------------------------------ normalised turn


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    """One model response, provider-neutral.

    `stop` is one of: end, tool_use, length, refusal, other. `raw_stop` keeps the provider's
    own value so nothing is lost in normalisation.
    """
    text: str
    stop: str
    raw_stop: str
    usage: dict
    tool_calls: list[ToolCall] = field(default_factory=list)
    refusal: str | None = None
    reported_cost_usd: float | None = None   # provider-reported billed cost, if any
    served_by: str | None = None             # upstream provider actually used (routers)
    assistant_message: object = None         # opaque; fed back verbatim in a tool loop


# ------------------------------------------------------------------ model registry


@dataclass(frozen=True)
class ModelConfig:
    """Everything that identifies a model run. Registered once; selected by `--model`.

    `cache_model` and `params` go into the response-cache key, so changing either misses the
    cache instead of reusing responses produced under different conditions.
    """
    key: str                 # what `--model` accepts, and what results files are named by
    provider: str            # "anthropic" | "openrouter" | "openai"
    api_model: str           # the id sent on the wire
    cache_model: str         # the id hashed into the cache key
    params: dict             # sampling / routing params, hashed into the cache key
    price_in: float          # USD per million input tokens
    price_out: float         # USD per million output tokens (reasoning billed as output)
    max_output_tokens: int   # upper bound used for worst-case budget reservation
    # Requests per minute this ACCOUNT may send for this model; None = no known limit.
    # DELIBERATELY NOT in `params`: pacing cannot change a response, and putting it in the
    # cache key would re-key paid responses over a scheduling decision. It is a property of
    # the account and its tier rather than of the model, so it is a default that --rpm
    # overrides, and the retry in ratelimit.py is what makes a wrong value recoverable.
    rpm: float | None = None


# max_tokens raised 8,000 -> 16,000 on 2026-09-20, for BOTH models, before either ran on the
# corrected corpus. At 8,000, 1 of 20 GPT-5.6 Sol probe responses stopped at the limit with no
# JSON (5%; ~60 of 1,200 tasks would be scored as model failures for a harness budget - see
# LIMITS §34). There was no parity to protect: every earlier run is superseded along with the
# corpus, and only a response that would have truncated costs more. This re-keys the response
# cache, which is correct - a different sampling configuration is a different request.
MAX_TOKENS = 16_000

_ANTHROPIC_PARAMS = {"max_tokens": MAX_TOKENS, "thinking": {"type": "adaptive"},
                     "output_config": {"effort": "high"}}

_OPENROUTER_ANTHROPIC_ROUTING = {"only": ["anthropic"], "allow_fallbacks": False,
                                 "require_parameters": True, "data_collection": "deny"}

# Pinned routing. Without `only` + `allow_fallbacks: False`, OpenRouter may serve the same
# model id from a different upstream (e.g. Azure) request to request, which would mix two
# serving stacks into one reported number. `require_parameters` refuses a provider that
# would silently drop `reasoning`. `data_collection: deny` keeps prompts out of training
# pools where the router can enforce it.
_OPENROUTER_OPENAI_ROUTING = {"only": ["openai"], "allow_fallbacks": False,
                              "require_parameters": True, "data_collection": "deny"}

MODELS: dict[str, ModelConfig] = {
    "claude-opus-5": ModelConfig(
        key="claude-opus-5", provider="anthropic", api_model="claude-opus-5",
        cache_model="claude-opus-5", params=_ANTHROPIC_PARAMS,
        price_in=5.00, price_out=25.00, max_output_tokens=MAX_TOKENS,
    ),
    # GPT-5.6 Sol: OpenRouter lists it (created 2026-07-09) as "the flagship model in
    # OpenAI's GPT-5.6 series"; $2 / $10 per M, read from /api/v1/models on 2026-09-18.
    # Same token ceiling and "high" effort as the Opus configuration, so the two differ in
    # model and not in configuration.
    "gpt-5.6-sol": ModelConfig(
        key="gpt-5.6-sol", provider="openrouter", api_model="openai/gpt-5.6-sol",
        cache_model="openrouter:openai/gpt-5.6-sol",
        params={"max_tokens": MAX_TOKENS, "reasoning": {"effort": "high"},
                "provider": _OPENROUTER_OPENAI_ROUTING},
        price_in=2.00, price_out=10.00, max_output_tokens=MAX_TOKENS,
    ),
    # Opus 5 through OpenRouter, routing pinned to Anthropic exactly as gpt-5.6-sol is pinned
    # to OpenAI. Added 2026-09-21 (LIMITS §41): the reason to use the native Messages API was
    # cache compatibility with the first Opus run, and that run is superseded - the new prompt
    # re-keys everything - so nothing is left to preserve. Routing both models through one
    # provider layer removes a variable from the deciding comparison: same request builder,
    # same retry policy (none), same reasoning-effort translation, same provider-reported
    # cost, which already matched our ledger to the cent. $5/$25 per M, read from
    # /api/v1/models on 2026-09-21 - identical to the direct API's price.
    "claude-opus-5-openrouter": ModelConfig(
        key="claude-opus-5-openrouter", provider="openrouter",
        api_model="anthropic/claude-opus-5",
        cache_model="openrouter:anthropic/claude-opus-5",
        params={"max_tokens": MAX_TOKENS, "reasoning": {"effort": "high"},
                "provider": _OPENROUTER_ANTHROPIC_ROUTING},
        price_in=5.00, price_out=25.00, max_output_tokens=MAX_TOKENS,
        # 2026-09-21: 1,014 of the first 1,050 requests at 8 workers came back 429,
        # "new accounts are limited to 20 requests per minute for this model". Set to 18,
        # under the stated 20, because the limiter's clock and the provider's do not agree
        # on where a minute starts. Nothing was mis-billed - 429 is unbilled - but the run
        # could not proceed. gpt-5.6-sol ran 8-wide against no such limit, which is why
        # this is a per-model field and not a global constant.
        rpm=18,
    ),
}


def get_model(key: str) -> ModelConfig:
    if key not in MODELS:
        raise KeyError(f"unknown model {key!r}; registered: {sorted(MODELS)}. "
                       f"Add it to eval/providers.py::MODELS with its price.")
    return MODELS[key]


# ------------------------------------------------------------------ adapters


class AnthropicProvider:
    env_keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def __init__(self, cfg: ModelConfig):
        import anthropic
        self.cfg = cfg
        self.client = anthropic.Anthropic()

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def complete(self, system: str, messages: list, tools: list) -> Turn:
        kwargs = dict(model=self.cfg.api_model, system=system, messages=messages,
                      **self.cfg.params)
        if tools:
            kwargs["tools"] = tools
        r = self.client.messages.create(**kwargs)
        usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
        stop = {"end_turn": "end", "tool_use": "tool_use", "max_tokens": "length",
                "refusal": "refusal"}.get(r.stop_reason, "other")
        refusal = None
        if r.stop_reason == "refusal":
            refusal = getattr(getattr(r, "stop_details", None), "category", "unknown")
        calls = [ToolCall(b.id, b.name, b.input) for b in r.content if b.type == "tool_use"]
        text = "\n".join(b.text for b in r.content if b.type == "text")
        return Turn(text=text, stop=stop, raw_stop=str(r.stop_reason), usage=usage,
                    tool_calls=calls, refusal=refusal,
                    assistant_message={"role": "assistant", "content": r.content})

    def tool_results_message(self, results: list[tuple[str, dict]]) -> dict:
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out)}
            for tid, out in results]}


class OpenAICompatProvider:
    """OpenAI-compatible chat completions (OpenRouter, or OpenAI direct)."""

    ENDPOINTS = {
        "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
        "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    }

    def __init__(self, cfg: ModelConfig):
        import openai
        base_url, key_env = self.ENDPOINTS[cfg.provider]
        # Overridable so a test can point at a closed port and reproduce "provider
        # unreachable" without a network call. Never used in a real run.
        base_url = os.environ.get(f"{cfg.provider.upper()}_BASE_URL", base_url)
        self.env_keys = (key_env,)
        self.cfg = cfg
        # max_retries=0: a retry inside the SDK is a second billed attempt the budget never
        # saw reserved. Transient failures surface to prewarm, which leaves the task uncached.
        self.client = openai.OpenAI(base_url=base_url, api_key=os.environ.get(key_env),
                                    max_retries=0, timeout=600)

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def complete(self, system: str, messages: list, tools: list) -> Turn:
        if tools:
            # Not implemented rather than half-implemented: the tool path has never been
            # exercised against this adapter, and an unexercised path is unvalidated.
            raise NotImplementedError(
                "tool conditions are not wired for OpenAI-compatible providers yet; "
                "only tool_less is supported")
        p = dict(self.cfg.params)
        max_tokens = p.pop("max_tokens")
        extra = {k: p.pop(k) for k in ("reasoning", "provider") if k in p}
        r = self.client.chat.completions.create(
            model=self.cfg.api_model,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens, extra_body=extra, **p,
        )
        choice = r.choices[0]
        msg = choice.message
        u = r.usage
        details = getattr(u, "completion_tokens_details", None)
        usage = {
            "input_tokens": u.prompt_tokens,
            "output_tokens": u.completion_tokens,   # includes reasoning tokens
            "reasoning_tokens": getattr(details, "reasoning_tokens", None) or 0,
        }
        # OpenRouter reports billed cost in credits (= USD) as a non-standard field.
        cost = getattr(u, "cost", None)
        if cost is None and getattr(u, "model_extra", None):
            cost = u.model_extra.get("cost")
        refusal = getattr(msg, "refusal", None)
        raw = str(choice.finish_reason)
        stop = ("refusal" if refusal else
                {"stop": "end", "length": "length", "tool_calls": "tool_use",
                 "content_filter": "refusal"}.get(raw, "other"))
        return Turn(text=msg.content or "", stop=stop, raw_stop=raw, usage=usage,
                    refusal=refusal, reported_cost_usd=float(cost) if cost is not None else None,
                    served_by=getattr(r, "provider", None)
                    or (getattr(r, "model_extra", None) or {}).get("provider"))


def make_provider(cfg: ModelConfig):
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.provider in OpenAICompatProvider.ENDPOINTS:
        return OpenAICompatProvider(cfg)
    raise ValueError(f"unknown provider {cfg.provider!r}")


def is_unbilled_error(exc: BaseException) -> bool:
    """True when the provider REFUSED the request, so nothing was generated or billed.

    Added 2026-09-20 after a real run: OpenRouter hit the key's spending limit and returned
    403 for 1,045 requests. Each was charged its worst-case reservation (`Budget.charge_failed`
    is deliberately conservative when billing is UNKNOWN), so the cap read $39.84 of $40
    while actual spend was $4.48. A cap that is exhausted by unbilled refusals stops the next
    legitimate run early, which is a different failure from overspending but still a wrong
    number driving a decision.

    Only statuses that mean "never reached generation" count: authentication, permission,
    rate limit, bad request, and transport errors raised before a response existed. A timeout
    or a 5xx mid-stream stays chargeable, because billing is genuinely unknown there.
    """
    status = getattr(exc, "status_code", None)
    if status in (400, 401, 402, 403, 404, 422, 429):
        return True
    # APITimeoutError is deliberately NOT here: a timeout can land after the provider has
    # generated and billed, so billing is unknown and the reservation stands.
    name = type(exc).__name__
    return name in ("APIConnectionError", "ConnectError", "ConnectTimeout") and status is None


def credential_env(cfg: ModelConfig) -> tuple[str, ...]:
    if cfg.provider == "anthropic":
        return AnthropicProvider.env_keys
    return (OpenAICompatProvider.ENDPOINTS[cfg.provider][1],)
