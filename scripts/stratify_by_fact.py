"""Is the flip-vs-indeterminate difference a CLASS effect, or a composition effect?

The aggregate comparison the decision rule asks for pools over whatever facts happen to
populate each class - and they are not populated alike. If one withheld fact dominates the
flip class and the model handles that fact well for reasons of its own, the aggregate shows
a class effect that does not exist within any stratum. That is Simpson's paradox, and the
standing rule (CLAUDE.md: every green signal is checked for what it is not measuring) makes
looking for it mandatory rather than optional.

So: the same comparison within each withheld fact, a Mantel-Haenszel pooled difference that
holds fact constant, and the aggregate recomputed with the dominant fact removed.

Usage: stratify_by_fact.py <results.json> [...]
"""

import collections
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPLIT = ROOT / "data" / "dev" / "t1.jsonl"


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def newcombe(k1, n1, k2, n2, z=1.96):
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    return ((p1 - p2) - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            (p1 - p2) + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def klass(src):
    if src["determinability"] == "determinate":
        return "determinate"
    return "flip" if src.get("is_eligibility_flip") else src["determinability"]


def report(path, rows):
    res = json.load(open(path, encoding="utf-8"))
    name = res["run"]["model"]
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for rec in res["per_task"]:
        src = rows.get(rec["household_id"])
        if src is None:
            continue
        cls = klass(src)
        if cls not in ("flip", "indeterminate"):
            continue
        cell = cells[src["withheld_fact"] or "(none)"][cls]
        cell[0] += int(bool(rec["abstention_correct"]))
        cell[1] += 1

    print(f"\n=== {name} ===")
    print(f"{'withheld fact':<28}{'flip':>16}{'indeterminate':>18}{'difference':>14}")
    mh_num = mh_den = 0.0
    favours_flip = favours_indet = 0
    for fact in sorted(cells, key=lambda f: -sum(c[1] for c in cells[f].values())):
        (kf, nf) = cells[fact]["flip"]
        (ki, ni) = cells[fact]["indeterminate"]
        if not nf or not ni:
            print(f"{fact:<28}{f'{kf}/{nf}':>16}{f'{ki}/{ni}':>18}{'-- one cell empty':>14}")
            continue
        pf, pi = kf / nf, ki / ni
        diff = pf - pi
        favours_flip += diff > 0
        favours_indet += diff < 0
        # Mantel-Haenszel weights: n1*n2/(n1+n2), which is the inverse-variance weight for
        # a risk difference under the common-effect assumption.
        w = nf * ni / (nf + ni)
        mh_num += w * diff
        mh_den += w
        print(f"{fact:<28}{f'{pf:.3f} ({kf}/{nf})':>16}"
              f"{f'{pi:.3f} ({ki}/{ni})':>18}{diff:>+14.3f}")

    print(f"\n  strata where flip does better: {favours_flip};  worse: {favours_indet}")
    if mh_den:
        print(f"  Mantel-Haenszel pooled difference, holding the withheld fact constant: "
              f"{mh_num / mh_den:+.3f}")

    # The aggregate, and the aggregate without the fact that dominates the flip class.
    tot = collections.defaultdict(lambda: [0, 0])
    for fact in cells:
        for cls in ("flip", "indeterminate"):
            tot[cls][0] += cells[fact][cls][0]
            tot[cls][1] += cells[fact][cls][1]
    biggest = max(cells, key=lambda f: cells[f]["flip"][1])
    share = cells[biggest]["flip"][1] / tot["flip"][1]
    print(f"\n  the flip class is {share:.0%} one fact: {biggest} "
          f"({cells[biggest]['flip'][1]}/{tot['flip'][1]})")
    for label, drop in (("aggregate (as the decision rule asks)", None),
                        (f"aggregate WITHOUT {biggest}", biggest)):
        kf, nf = tot["flip"]
        ki, ni = tot["indeterminate"]
        if drop:
            kf -= cells[drop]["flip"][0]
            nf -= cells[drop]["flip"][1]
            ki -= cells[drop]["indeterminate"][0]
            ni -= cells[drop]["indeterminate"][1]
        if not nf or not ni:
            continue
        pf, pi = kf / nf, ki / ni
        lo, hi = newcombe(kf, nf, ki, ni)
        print(f"    {label:<42} flip {pf:.3f} ({kf}/{nf})  "
              f"indet {pi:.3f} ({ki}/{ni})  diff {pf - pi:+.3f} [{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    rows = {r["household_id"]: r for r in
            (json.loads(ln) for ln in open(SPLIT, encoding="utf-8") if ln.strip())}
    for p in sys.argv[1:]:
        report(p, rows)
