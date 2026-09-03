"""Phase 3 reporting: headline table, plus abstention broken down by class and by fact.

Reads committed results files only. Computes nothing the results files do not already
contain, so the tables cannot disagree with the scored artifacts.
"""
import collections
import json
import pathlib
import sys

R = pathlib.Path("/home/jak/redtape/results")
SPLIT = pathlib.Path("/home/jak/redtape/data/dev/t1.jsonl")

ORDER = [
    ("live.claude-opus-5.tool_less", "Claude Opus 5 (live)"),
    ("baseline.always_abstain", "  baseline: always_abstain"),
    ("baseline.never_abstain", "  baseline: never_abstain"),
    ("baseline.always_eligible", "  baseline: always_eligible"),
    ("baseline.never_eligible", "  baseline: never_eligible"),
    ("baseline.rules_only", "  baseline: rules_only"),
    ("perfect", "  ceiling: perfect_agent"),
]


def load(stem):
    p = R / f"t1.{stem}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def fmt(v):
    return "  n/a" if v is None else f"{v:.3f}"


print("=" * 104)
print("HEADLINE METRICS - dev split, 1,200 tasks")
print("=" * 104)
print(f"{'':<30}{'exact-match':>13}{'abstention':>13}{'pair-consist':>14}"
      f"{'malformed':>11}{'schema_inv':>12}{'scorer_err':>12}")
print(f"{'':<30}{'(determinate)':>13}{'(T1b)':>13}{'(200 pairs)':>14}"
      f"{'JSON':>11}{'':>12}{'':>12}")
print("-" * 104)

for stem, label in ORDER:
    d = load(stem)
    if d is None:
        print(f"{label:<30}{'  -- not run --':>13}")
        continue
    pf = d["diagnostics"].get("parse_failures", {})
    print(
        f"{label:<30}"
        f"{fmt(d['t1_exact_match_determinate']['value']):>13}"
        f"{fmt(d['t1b_abstention_accuracy']['value']):>13}"
        f"{fmt(d['pair_consistency']['value']):>14}"
        f"{pf.get('malformed_json', 0):>11}"
        f"{pf.get('schema_invalid', 0):>12}"
        f"{d['diagnostics']['scorer_error_count']:>12}"
    )

live = load("live.claude-opus-5.tool_less")
if live is None:
    sys.exit("\nlive results not present yet")

n_det = live["t1_exact_match_determinate"]["n"]
n_t1b = live["t1b_abstention_accuracy"]["n"]
n_pairs = live["pair_consistency"].get("n_pairs")
print("-" * 104)
print(f"n: determinate={n_det}  T1b={n_t1b}  pairs={n_pairs}  "
      f"gate_pass_rate={live['diagnostics']['gate_pass_rate']:.3f}  "
      f"publishable={live['diagnostics']['publishable']}")
u = live["run"].get("usage", {})
if u:
    print(f"cost: ${u.get('usd_actually_spent', 0):.2f} spent this run  |  "
          f"${u.get('usd_if_uncached', 0):.2f} if run from cold")

# ---- breakdowns need the split for class/fact, joined by household_id ---------------------
rows = {r["household_id"]: r for r in
        (json.loads(ln) for ln in SPLIT.open(encoding="utf-8") if ln.strip())}

by_class = collections.defaultdict(lambda: [0, 0])
by_fact = collections.defaultdict(lambda: [0, 0])

for rec in live["per_task"]:
    src = rows.get(rec["household_id"])
    if src is None or src["determinability"] == "determinate":
        continue
    cls = ("eligibility_flip" if src.get("is_eligibility_flip")
           else src["determinability"])
    ok = 1 if rec["abstention_correct"] else 0
    by_class[cls][0] += ok
    by_class[cls][1] += 1
    fact = src["withheld_fact"] or "(none)"
    by_fact[fact][0] += ok
    by_fact[fact][1] += 1

print()
print("=" * 104)
print("ABSTENTION ACCURACY BY CLASS")
print("=" * 104)
print(f"{'class':<28}{'correct':>9}{'n':>7}{'accuracy':>11}   what a correct answer looks like")
print("-" * 104)
MEANING = {
    "indeterminate": "abstain, naming the deciding program(s)",
    "incomplete_determinate": "ANSWER anyway - the missing fact does not decide",
    "eligibility_flip": "abstain - the fact flips SNAP eligibility itself",
}
for cls in ("indeterminate", "incomplete_determinate", "eligibility_flip"):
    ok, n = by_class.get(cls, [0, 0])
    acc = f"{ok / n:.3f}" if n else "  n/a"
    print(f"{cls:<28}{ok:>9}{n:>7}{acc:>11}   {MEANING[cls]}")
tot_ok = sum(v[0] for v in by_class.values())
tot_n = sum(v[1] for v in by_class.values())
print("-" * 104)
print(f"{'ALL T1b':<28}{tot_ok:>9}{tot_n:>7}{tot_ok / tot_n:>11.3f}" if tot_n else "")

print()
print("=" * 104)
print("ABSTENTION ACCURACY BY WITHHELD FACT")
print("=" * 104)
print(f"{'withheld fact':<34}{'correct':>9}{'n':>7}{'accuracy':>11}")
print("-" * 104)
for fact, (ok, n) in sorted(by_fact.items(), key=lambda kv: -kv[1][1]):
    print(f"{fact:<34}{ok:>9}{n:>7}{ok / n:>11.3f}")
