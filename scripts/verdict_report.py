"""Headlines with Wilson intervals, and the flip-vs-indeterminate comparison the decision
rule in docs/CORRECTION_DRAFT.md asks for."""
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
    """95% CI for the difference p1 - p2 (Newcombe's method 10, square-and-add)."""
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p1 - p2) - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = (p1 - p2) + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return lo, hi


def fisher(a, b, c, d):
    """Two-sided Fisher exact p for [[a,b],[c,d]]."""
    from math import comb
    n = a + b + c + d
    row1, col1 = a + b, a + c
    def pr(x):
        return comb(row1, x) * comb(n - row1, col1 - x) / comb(n, col1)
    obs = pr(a)
    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    return sum(pr(x) for x in range(lo, hi + 1) if pr(x) <= obs * (1 + 1e-9))


res = json.load(open(sys.argv[1], encoding="utf-8"))
rows = {r["household_id"]: r for r in
        (json.loads(ln) for ln in open("data/dev/t1.jsonl", encoding="utf-8") if ln.strip())}

cls = collections.defaultdict(lambda: [0, 0])
det = [0, 0]
for rec in res["per_task"]:
    src = rows.get(rec["household_id"])
    if src is None:
        continue
    if src["determinability"] == "determinate":
        det[0] += rec["exact_match"]
        det[1] += 1
        continue
    name = "flip" if src.get("is_eligibility_flip") else src["determinability"]
    cls[name][0] += int(bool(rec["abstention_correct"]))
    cls[name][1] += 1


def line(label, k, n):
    lo, hi = wilson(k, n)
    print(f"  {label:<34}{k:>5}/{n:<6}{k / n:>8.3f}   95% CI [{lo:.3f}, {hi:.3f}]")


print("HEADLINES (Wilson 95% intervals)")
line("T1 exact-match (determinate)", *det)
t1b = [sum(v[0] for v in cls.values()), sum(v[1] for v in cls.values())]
line("T1b abstention (all)", *t1b)
pc = res["pair_consistency"]
print(f"  {'pair-consistency':<34}{'':>5} {pc.get('n_pairs'):<6}{pc['value']:>8.3f}")
print()
print("ABSTENTION BY CLASS")
for name in ("flip", "indeterminate", "incomplete_determinate"):
    line(name, *cls[name])

f_k, f_n = cls["flip"]
i_k, i_n = cls["indeterminate"]
lo, hi = newcombe(f_k, f_n, i_k, i_n)
p = fisher(f_k, f_n - f_k, i_k, i_n - i_k)
print()
print("DECISION RULE: does the flip class EXCEED the indeterminate class?")
print(f"  flip {f_k}/{f_n} = {f_k / f_n:.3f}   indeterminate {i_k}/{i_n} = {i_k / i_n:.3f}")
print(f"  difference {f_k / f_n - i_k / i_n:+.3f}   95% CI [{lo:+.3f}, {hi:+.3f}]"
      f"   Fisher exact p = {p:.3f}")
print(f"  ratio {(f_k / f_n) / (i_k / i_n):.2f}x        (withdrawn Opus number: 7.9x)")
verdict = ("EXCEEDS - the interval excludes zero" if lo > 0 else
           "BELOW - the interval excludes zero in the other direction" if hi < 0 else
           "NO - the interval spans zero; the classes are not separated at these n")
print(f"  verdict: {verdict}")
