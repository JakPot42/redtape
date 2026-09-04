"""Claude Opus 5 through the three tool conditions, on the same n=300 sample.

The scripted ablation answers "what does the tool offer a PERFECT extractor". This answers
the question that matters for the finding: given the determinability signal, can a model use
it? If `tool_equipped_unknowns` lifts Opus 5's abstention the way it lifts the scripted
agent's, the model can act on the signal when handed it and simply does not generate it - a
reporting failure. If it does not lift, the failure is upstream, in recognition.

Same sample as scripts/ablation300.py so the two tables are directly comparable.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, "/home/jak/redtape")

from redtape.config import load_dotenv  # noqa: E402

load_dotenv()

from eval.run_eval import LEDGER, live_agent, prewarm, run  # noqa: E402
from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig  # noqa: E402

SPLIT = pathlib.Path("/home/jak/redtape/data/dev/t1.jsonl")
RESULTS = pathlib.Path("/home/jak/redtape/results")
CONDITIONS = ("tool_less", "tool_equipped", "tool_equipped_unknowns")
QUOTA = {"determinate": 150, "indeterminate": 60,
         "incomplete_determinate": 50, "eligibility_flip": 40}


def cls(r):
    return "eligibility_flip" if r.get("is_eligibility_flip") else r["determinability"]


def sample(limit=None):
    rows = [json.loads(ln) for ln in SPLIT.open(encoding="utf-8") if ln.strip()]
    by = collections.defaultdict(list)
    for r in rows:
        if not r.get("pair_id"):
            by[cls(r)].append(r)
    picked = []
    for c, want in QUOTA.items():
        group = sorted(by[c], key=lambda r: r["index"])
        stride = max(1, len(group) // want)
        picked.extend(group[::stride][:want])
    if limit:
        # Keep the class mix when probing: take a stride through the whole sample.
        step = max(1, len(picked) // limit)
        picked = picked[::step][:limit]
    return picked


ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=None, help="probe with a subset first")
ap.add_argument("--workers", type=int, default=6)
ap.add_argument("--model", default="claude-opus-5")
args = ap.parse_args()

rows = sample(args.limit)
cfg = T1TaskConfig()
tasks = []
for r in rows:
    payload = {k: v for k, v in r.items()
               if k not in ("task_hash", "task_key", "unscored_deciding_programs",
                            "pair_truth_differs")}
    tasks.append(T1Task(T1Data.model_validate(payload), cfg))

print(f"{len(tasks)} tasks  " +
      "  ".join(f"{c}={sum(1 for r in rows if cls(r) == c)}" for c in QUOTA))

tag = f"t1_live300{'_probe' + str(args.limit) if args.limit else ''}"
grand = {"usd": 0.0, "in": 0, "out": 0}

for condition in CONDITIONS:
    print(f"\n=== {condition} ===", flush=True)
    agent = live_agent(condition, args.model)
    LEDGER.reset()
    prewarm(tasks, agent, workers=args.workers, log_every=50)
    run(tasks, agent, model=args.model, split=tag, condition=condition,
        out=RESULTS / f"{tag}.live.{condition}.json", progress_every=150)
    b = LEDGER.billed
    grand["usd"] += b["usd"]
    grand["in"] += b["input_tokens"]
    grand["out"] += b["output_tokens"]
    print(f"  {condition}: {LEDGER.line()}", flush=True)

print()
print("=" * 78)
print(f"OPUS 5 TOOL CONDITIONS - n={len(tasks)}")
print("=" * 78)
print(f"{'condition':<30}{'exact-match':>20}{'abstention':>20}")
print("-" * 78)
for condition in CONDITIONS:
    d = json.loads((RESULTS / f"{tag}.live.{condition}.json").read_text(encoding="utf-8"))
    e = d["t1_exact_match_determinate"]
    a = d["t1b_abstention_accuracy"]
    ev, en = e["value"], e["n"]
    av, an = a["value"], a["n"]
    ecell = f"{ev:.3f} (n={en})"
    acell = f"{av:.3f} (n={an})"
    print(f"{condition:<30}{ecell:>20}{acell:>20}")
print("-" * 78)
usd, tin, tout = grand["usd"], grand["in"], grand["out"]
print(f"billed this run: ${usd:.2f}  ({tin:,} in / {tout:,} out)")
