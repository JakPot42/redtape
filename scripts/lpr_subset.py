"""Tasks containing an LPR adult, against the rest (LIMITS §39/§40).

The case files never state how long a lawful permanent resident has held that status, and
SNAP's five-year bar (8 U.S.C. 1612(a)(2)(L), 7 CFR 273.4(a)(6)(iii)) turns on exactly that. The answer keys already assume
the bar is satisfied - `years_since_us_entry` defaults to 5 in policyengine-us and SNAP's
status test never reads it - so the keys are unstated rather than wrong, and both models
face the same silence. This measures what the silence costs.

Reported for GPT before the Opus run so the comparison was fixed in advance, and reported
for Opus the same way. If the gap appears for both models it is a property of the corpus;
if only for one, it is a property of that model.

Usage: lpr_subset.py <results.json> [...]
"""

import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPLIT = ROOT / "data" / "dev" / "t1.jsonl"

# An adult described as a lawful permanent resident. Matched against the narrative the model
# actually saw, not against a structured field, because the narrative is what carries (or
# omits) the fact - and a structured match would silently include households where the
# status was the WITHHELD fact and therefore never appeared in the case file at all.
LPR = re.compile(r"lawful permanent resident", re.I)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def main(paths):
    rows = {r["household_id"]: r for r in
            (json.loads(ln) for ln in open(SPLIT, encoding="utf-8") if ln.strip())}
    for path in paths:
        res = json.load(open(path, encoding="utf-8"))
        print(f"\n=== {res['run']['model']} ===")
        groups = {"LPR adult present": [], "rest": []}
        for rec in res["per_task"]:
            src = rows.get(rec["household_id"])
            if src is None:
                continue
            key = "LPR adult present" if LPR.search(src["prompt"]) else "rest"
            groups[key].append((src, rec))

        for label, items in groups.items():
            abst = [r for s, r in items if s["determinability"] != "determinate"]
            det = [r for s, r in items if s["determinability"] == "determinate"]
            ka = sum(int(bool(r["abstention_correct"])) for r in abst)
            kd = sum(int(bool(r["exact_match"])) for r in det)
            la, ha = wilson(ka, len(abst))
            print(f"  {label:<20} n={len(items):>4}   "
                  f"abstention {ka / len(abst) if abst else float('nan'):.3f} "
                  f"({ka}/{len(abst)}) [{la:.3f}, {ha:.3f}]   "
                  f"exact-match {kd / len(det) if det else float('nan'):.3f} "
                  f"({kd}/{len(det)})")


if __name__ == "__main__":
    main(sys.argv[1:])
