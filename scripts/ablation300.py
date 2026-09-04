"""Tool ablation at n=300, weighted toward T1b so the abstention cell is not thin.

The claim is DIRECTIONAL - the calculator moves arithmetic and not abstention; marking
withheld facts moves abstention and not arithmetic - so it needs enough that the direction
is not noise, not the full split. The previous table was a 60-task sample (n=34 / n=26),
where one task was worth ~3 points; that was the thinnest number in the repo and the one an
outside reader reaches for first.

NO MODEL IS CALLED. All three conditions use scripted agents: `tool_less` is the rules_only
baseline, the other two are a scripted extractor driving the real PolicyEngine-backed tool.
The cost is engine time, not tokens. That is also the limit of what this measures - an upper
bound on what the TOOL provides to a perfect extractor, not a measurement of any model.

Pair rows are excluded: they are all determinate, and partially sampling them would leave
pair_consistency reporting a sampling artifact rather than a property of any condition.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, "/home/jak/redtape")

from eval.run_eval import baseline_agent, run, scripted_tool_agent  # noqa: E402
from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig  # noqa: E402

SPLIT = pathlib.Path("/home/jak/redtape/data/dev/t1.jsonl")
RESULTS = pathlib.Path("/home/jak/redtape/results")
CONDITIONS = ("tool_less", "tool_equipped", "tool_equipped_unknowns")

# 150 determinate -> exact-match n=150; 150 T1b -> abstention n=150. Both comfortably above
# the n~100 a directional claim needs.
QUOTA = {"determinate": 150, "indeterminate": 60,
         "incomplete_determinate": 50, "eligibility_flip": 40}


def cls(r):
    return "eligibility_flip" if r.get("is_eligibility_flip") else r["determinability"]


rows = [json.loads(ln) for ln in SPLIT.open(encoding="utf-8") if ln.strip()]
by = collections.defaultdict(list)
for r in rows:
    if r.get("pair_id"):
        continue
    by[cls(r)].append(r)

picked = []
for c, want in QUOTA.items():
    group = sorted(by[c], key=lambda r: r["index"])
    if len(group) < want:
        sys.exit(f"only {len(group)} non-pair {c} rows available, need {want}")
    stride = max(1, len(group) // want)
    picked.extend(group[::stride][:want])

print(f"sample: {len(picked)} tasks (pair rows excluded)")
for c in QUOTA:
    print(f"  {c:<26}{sum(1 for r in picked if cls(r) == c):>5}")

cfg = T1TaskConfig()
tasks = []
for r in picked:
    payload = {k: v for k, v in r.items()
               if k not in ("task_hash", "task_key", "unscored_deciding_programs",
                            "pair_truth_differs")}
    tasks.append(T1Task(T1Data.model_validate(payload), cfg))

for condition in CONDITIONS:
    print(f"\ncondition: {condition}", flush=True)
    agent = (baseline_agent("rules_only") if condition == "tool_less"
             else scripted_tool_agent(condition))
    run(tasks, agent, model="scripted", split="t1_ablation300", condition=condition,
        out=RESULTS / f"t1_ablation300.scripted.{condition}.json",
        progress_every=100)

print()
print("=" * 74)
print("TOOL ABLATION - n=300, weighted toward T1b")
print("=" * 74)
print(f"{'condition':<30}{'exact-match':>20}{'abstention':>20}")
print("-" * 74)
for condition in CONDITIONS:
    d = json.loads((RESULTS / f"t1_ablation300.scripted.{condition}.json")
                   .read_text(encoding="utf-8"))
    e = d["t1_exact_match_determinate"]
    a = d["t1b_abstention_accuracy"]
    ecell = f"{e['value']:.3f} (n={e['n']})"
    acell = f"{a['value']:.3f} (n={a['n']})"
    print(f"{condition:<30}{ecell:>20}{acell:>20}")
