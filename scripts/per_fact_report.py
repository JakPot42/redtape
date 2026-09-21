"""Per-fact abstention, split by class, for a results file. Retires the employment_income
confound question: the prompt now lists every identifier instead of one example."""
import collections
import json
import sys

rows = {r["household_id"]: r for r in
        (json.loads(ln) for ln in open("data/dev/t1.jsonl", encoding="utf-8") if ln.strip())}
res = json.load(open(sys.argv[1], encoding="utf-8"))

by = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
for rec in res["per_task"]:
    src = rows.get(rec["household_id"])
    if src is None or src["determinability"] == "determinate":
        continue
    cls = "flip" if src.get("is_eligibility_flip") else src["determinability"]
    cell = by[src["withheld_fact"] or "(none)"][cls]
    cell[0] += int(bool(rec["abstention_correct"]))
    cell[1] += 1

CLASSES = ("flip", "indeterminate", "incomplete_determinate")
print(f"{'withheld fact':<26}" + "".join(f"{c:>26}" for c in CLASSES) + f"{'all T1b':>16}")
for fact in sorted(by, key=lambda f: -sum(v[1] for v in by[f].values())):
    line = f"{fact:<26}"
    tot = [0, 0]
    for c in CLASSES:
        ok, n = by[fact][c]
        tot[0] += ok
        tot[1] += n
        line += f"{(f'{ok / n:.3f} ({ok}/{n})' if n else '-'):>26}"
    line += f"{f'{tot[0] / tot[1]:.3f} ({tot[0]}/{tot[1]})':>16}"
    print(line)
print("\nAbstention is only REQUIRED in flip and indeterminate; in incomplete-determinate the")
print("correct behaviour is to answer, so a high number there means 'did not abstain'.")
