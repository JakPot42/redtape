"""Compare two models on the tasks BOTH actually answered, and say whether that subset is
representative of the split.

Written because the Opus run bound on the provider's key limit at 877 of 1,200 tasks. A
headline computed over 877 tasks is not comparable to one computed over 1,198 unless the
877 are a fair sample, and "the run stopped early" is not evidence either way: `prewarm`
fetches in file order, so an early stop truncates the TAIL of the file. If the split is
ordered in any way that correlates with class or difficulty, the surviving prefix is biased
and the comparison is worthless.

So this does three things, in this order, because the third is meaningless without the
first two:

1. describes what is missing, by class, against the full split;
2. tests whether the completed subset differs from the split in class composition;
3. only then reports both models restricted to the common task set.

Usage: compare_on_common_tasks.py <results_a.json> <results_b.json>
"""

import collections
import json
import math
import sys


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


def _logfact(n, _c={}):
    if n not in _c:
        _c[n] = math.lgamma(n + 1)
    return _c[n]


def fisher(a, b, c, d):
    n = a + b + c + d
    def p(a_, b_, c_, d_):
        return math.exp(_logfact(a_ + b_) + _logfact(c_ + d_) + _logfact(a_ + c_)
                        + _logfact(b_ + d_) - _logfact(n) - _logfact(a_) - _logfact(b_)
                        - _logfact(c_) - _logfact(d_))
    obs = p(a, b, c, d)
    tot = 0.0
    for i in range(0, min(a + b, a + c) + 1):
        j, k, m = a + b - i, a + c - i, d - a + i
        if j < 0 or k < 0 or m < 0:
            continue
        q = p(i, j, k, m)
        if q <= obs * (1 + 1e-9):
            tot += q
    return min(1.0, tot)


def load(path):
    res = json.load(open(path, encoding="utf-8"))
    return res, {r["household_id"]: r for r in res["per_task"]}


def klass(src):
    if src["determinability"] == "determinate":
        return "determinate"
    if src.get("is_eligibility_flip"):
        return "flip"
    return src["determinability"]


def main(path_a, path_b):
    rows = {r["household_id"]: r for r in
            (json.loads(ln) for ln in open("data/dev/t1.jsonl", encoding="utf-8")
             if ln.strip())}
    res_a, a = load(path_a)
    res_b, b = load(path_b)
    name_a = res_a["run"].get("model", path_a)
    name_b = res_b["run"].get("model", path_b)
    common = sorted(set(a) & set(b))

    print(f"split {len(rows)} tasks | {name_a} {len(a)} | {name_b} {len(b)} | "
          f"common {len(common)}")

    # 1. what is missing, by class
    print("\n--- coverage by class (is the completed subset representative?) ---")
    print(f"{'class':<24}{'split':>8}{'common':>8}{'share':>9}")
    split_n = collections.Counter(klass(s) for s in rows.values())
    common_n = collections.Counter(klass(rows[h]) for h in common if h in rows)
    for cls in sorted(split_n):
        s, c = split_n[cls], common_n.get(cls, 0)
        print(f"{cls:<24}{s:>8}{c:>8}{c / s:>8.1%}")
    overall = len(common) / len(rows)
    print(f"{'ALL':<24}{len(rows):>8}{len(common):>8}{overall:>8.1%}")

    # A chi-square-free check that reads honestly: does any class's retention differ from
    # the overall retention by more than sampling noise would explain?
    print("\n  retention vs overall, per class (95% Wilson on the class's retention):")
    biased = []
    for cls in sorted(split_n):
        s, c = split_n[cls], common_n.get(cls, 0)
        lo, hi = wilson(c, s)
        flag = "" if lo <= overall <= hi else "  <-- DIFFERS from overall retention"
        if flag:
            biased.append(cls)
        print(f"    {cls:<22}{c / s:>7.1%}  [{lo:.1%}, {hi:.1%}]{flag}")
    if biased:
        print(f"  !! the completed subset is NOT a fair sample for: {', '.join(biased)}")
    else:
        print("  every class was retained at a rate consistent with the overall rate")

    # 2. headlines on the common set
    print("\n--- headlines on the COMMON task set only ---")
    for name, recs in ((name_a, a), (name_b, b)):
        det = [recs[h] for h in common if rows[h]["determinability"] == "determinate"]
        k = sum(int(bool(r["exact_match"])) for r in det)
        lo, hi = wilson(k, len(det))
        print(f"  {name:<28} exact-match  {k / len(det):.3f} ({k}/{len(det)}) "
              f"[{lo:.3f}, {hi:.3f}]")
        abst = [recs[h] for h in common if rows[h]["determinability"] != "determinate"]
        k2 = sum(int(bool(r["abstention_correct"])) for r in abst)
        lo2, hi2 = wilson(k2, len(abst))
        print(f"  {' ':<28} abstention   {k2 / len(abst):.3f} ({k2}/{len(abst)}) "
              f"[{lo2:.3f}, {hi2:.3f}]")

    # 3. the decision rule: flip vs indeterminate, per model
    print("\n--- flip vs indeterminate (docs/CORRECTION_DRAFT.md decision rule) ---")
    for name, recs in ((name_a, a), (name_b, b)):
        cells = {}
        for cls in ("flip", "indeterminate"):
            sel = [recs[h] for h in common if klass(rows[h]) == cls]
            cells[cls] = (sum(int(bool(r["abstention_correct"])) for r in sel), len(sel))
        (kf, nf), (ki, ni) = cells["flip"], cells["indeterminate"]
        if not nf or not ni:
            print(f"  {name}: not enough data (flip n={nf}, indeterminate n={ni})")
            continue
        pf, pi = kf / nf, ki / ni
        lo, hi = newcombe(kf, nf, ki, ni)
        p = fisher(kf, nf - kf, ki, ni - ki)
        print(f"  {name}")
        print(f"    flip           {pf:.3f} ({kf}/{nf})")
        print(f"    indeterminate  {pi:.3f} ({ki}/{ni})")
        print(f"    difference     {pf - pi:+.3f}   95% CI [{lo:+.3f}, {hi:+.3f}]   "
              f"Fisher p = {p:.3f}   ratio {pf / pi if pi else float('nan'):.2f}x")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
