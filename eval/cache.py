"""Content-addressed response cache. Every model response is stored, once.

**Why this is not optional.** A run of the dev split is 1,200 paid requests. Re-running one
because a scoring bug was found downstream, or because a process died at task 900, would pay
for the same tokens twice and — worse — could return *different* answers, silently mixing two
samples of the model into one reported number. The cache makes a run resumable and makes the
scored artifact stable: score the same stored responses as many times as the scorers change.

**The key is everything that could change the response.** Model, system prompt, the task
prompt, the tool schema, and every sampling parameter are hashed together. If any of them
moves, the key moves and the old entry is simply not found — there is no staleness window and
no way to serve a response that was produced under different conditions. That is deliberate:
a cache that can return an answer generated under a *different* prompt is worse than no cache,
because the mismatch is invisible in the results file.

Usage is stored alongside the response, so cost is recomputed from the cache without
re-billing, and a cached run reports the cost it *would* have had.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "responses"

# The cache is PARTITIONED BY SPLIT, and the partition is part of the path rather than
# something a caller opts into.
#
# Entries are named by a content hash, so a held-out response and a dev response are
# indistinguishable by filename. Without this split a held-out run would drop its responses
# into the same committed directory, and each response restates the household it was given -
# so committing the cache would publish held-out case files in everything but name. The dev
# subtree is committed (it is generated from the PUBLIC seed and is worth preserving: it is
# paid for); the held-out subtree is gitignored and must stay that way.
PUBLIC_DEV_SEED = 20260828


def partition(seed: int | None) -> str:
    """`dev` only for the public seed. Anything else - including an unknown seed - is
    treated as private, because guessing wrong in that direction is the expensive mistake."""
    return "dev" if seed == PUBLIC_DEV_SEED else "heldout"


def cache_key(*, model: str, system: str, prompt: str, tools, params: dict) -> str:
    """SHA-256 over every input that can affect the response.

    `sort_keys=True` throughout: an unsorted dict would produce a different digest for an
    identical request depending on insertion order, which would look like a cache miss and
    quietly re-bill.
    """
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "tools": tools or [],
        "params": params,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _path(key: str, split: str = "dev") -> Path:
    # Shard by the first two hex chars: 1,200+ files in one directory is slow to list on
    # every filesystem this project touches, WSL over /mnt/c especially.
    return CACHE_DIR / split / key[:2] / f"{key}.json"


def get(key: str, split: str = "dev") -> dict | None:
    p = _path(key, split)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A truncated entry is a miss, not a crash. It will be rewritten.
        return None


def put(key: str, record: dict, split: str = "dev") -> None:
    """Write atomically.

    A run interrupted mid-write would otherwise leave a half-JSON file that reads as a
    permanent miss for that task, so the same task would be re-billed on every later run.
    """
    p = _path(key, split)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {**record, "cached_at": time.time()}
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def stats() -> dict:
    if not CACHE_DIR.is_dir():
        return {"entries": 0, "bytes": 0}
    files = list(CACHE_DIR.rglob("*.json"))
    return {"entries": len(files), "bytes": sum(f.stat().st_size for f in files)}


# ------------------------------------------------------------------ pricing
#
# USD per million tokens, from the Anthropic pricing table. Kept here rather than inlined at
# the call site so a price change is one edit, and so an unknown model is an explicit error
# rather than a silent $0.00 that would make a run look free.

PRICING = {
    "claude-opus-5":            {"input": 5.00, "output": 25.00},
    "claude-fable-5":           {"input": 10.00, "output": 50.00},
    "claude-sonnet-5":          {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5":         {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}


def cost_usd(model: str, usage: dict) -> float:
    """Cost of one response. Thinking tokens are billed as output and are included in
    `output_tokens` by the API, so no separate term is needed."""
    if model not in PRICING:
        raise KeyError(
            f"no pricing for {model!r}. Add it to eval/cache.py::PRICING rather than "
            f"letting the run report a cost of zero."
        )
    p = PRICING[model]
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    # Cache reads are billed at ~0.1x and writes at ~1.25x. Both are zero for this workload
    # (the system prompt is ~247 tokens, below the minimum cacheable prefix), but they are
    # counted rather than ignored so the number stays right if that ever changes.
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    return (
        inp * p["input"]
        + cache_read * p["input"] * 0.1
        + cache_write * p["input"] * 1.25
        + out * p["output"]
    ) / 1_000_000
