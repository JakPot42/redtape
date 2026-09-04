"""Re-score every committed results file under the current scorer, and diff.

Assuming a file is unaffected because "that agent never emits null" is exactly the reasoning
that produced LIMITS 25 and 27. So every agent is actually re-run: the live one from cache
(free), the scripted ones through the real engine (CPU only). Nothing is inferred.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "/home/jak/redtape")

from redtape.config import load_dotenv  # noqa: E402

load_dotenv()

from eval.baselines import BASELINES, PAIR_DIAGNOSTICS  # noqa: E402
from eval.run_eval import (  # noqa: E402
    baseline_agent,
    live_agent,
    load_tasks,
    perfect_agent,
    run,
    scripted_tool_agent,
)

R = pathlib.Path("/home/jak/redtape/results")
DEV = "/home/jak/redtape/data/dev/t1.jsonl"
TMP = pathlib.Path("/tmp/rescore")
TMP.mkdir(exist_ok=True)

HEADS = ("t1_exact_match_determinate", "t1b_abstention_accuracy", "pair_consistency")


def headline(d):
    out = {}
    for h in HEADS:
        blk = d.get(h, {})
        out[h] = (blk.get("value"), blk.get("n", blk.get("n_pairs")))
    g = d.get("diagnostics", {})
    out["gate"] = g.get("gate_pass_rate")
    out["scorer_error"] = g.get("scorer_error_count")
    out["parse_failures"] = g.get("parse_failures", {})
    return out


jobs = []
full = load_tasks(DEV)
for name in BASELINES:
    jobs.append((f"t1.baseline.{name}", full, baseline_agent(name), f"baseline:{name}",
                 "tool_less"))
for name in PAIR_DIAGNOSTICS:
    jobs.append((f"t1.pairdiag.{name}", full, baseline_agent(name, PAIR_DIAGNOSTICS),
                 f"pairdiag:{name}", "tool_less"))
jobs.append(("t1.perfect", full, perfect_agent, "perfect", "tool_less"))
jobs.append(("t1.live.claude-opus-5.tool_less", full,
             live_agent("tool_less", "claude-opus-5"), "claude-opus-5", "tool_less"))

sampled = load_tasks(DEV, sample=60)
for cond in ("tool_less", "tool_equipped", "tool_equipped_unknowns"):
    agent = (baseline_agent("rules_only") if cond == "tool_less"
             else scripted_tool_agent(cond))
    jobs.append((f"t1.scripted.{cond}", sampled, agent, "scripted", cond))

changed, same, missing = [], [], []

for stem, tasks, agent, model, condition in jobs:
    old_path = R / f"{stem}.json"
    if not old_path.is_file():
        missing.append(stem)
        continue
    old = headline(json.loads(old_path.read_text(encoding="utf-8")))
    print(f"re-scoring {stem} ({len(tasks)} tasks)...", flush=True)
    res = run(tasks, agent, model=model, split="rescore", condition=condition,
              out=TMP / f"{stem}.json")
    new = headline(res)
    (changed if new != old else same).append((stem, old, new))

print()
print("=" * 96)
print("RE-SCORE SWEEP")
print("=" * 96)
print(f"unchanged : {len(same)}")
for stem, _, _ in same:
    print(f"    {stem}")
print(f"CHANGED   : {len(changed)}")
for stem, old, new in changed:
    print(f"\n  *** {stem}")
    for k in list(HEADS) + ["gate", "scorer_error", "parse_failures"]:
        if old.get(k) != new.get(k):
            print(f"        {k}: {old.get(k)}  ->  {new.get(k)}")
if missing:
    print(f"\nno committed file (skipped): {missing}")
