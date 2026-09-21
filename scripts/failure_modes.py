"""Failure modes FIRST: stop reasons, parse failures, and fact-format compliance.

CLAUDE.md, LIMITS §25/§27: a degenerate headline is a harness bug until proven otherwise,
so these counts are read before any accuracy number. `truncated` in particular is a harness
budget failure wearing a model failure's clothes - a response that spent its whole token
allowance on reasoning and emitted no JSON scores as `no_json_found`, which looks like the
model could not answer.

Fact-format compliance is a methodological result in its own right (LIMITS §39): the prompt
now lists every withheld-fact identifier, so naming one in free text instead is a choice the
model made against an explicit instruction, not an ambiguity we left open.

Usage: failure_modes.py <results.json> [<results.json> ...]
"""

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "responses" / "dev"


def report(path):
    res = json.load(open(path, encoding="utf-8"))
    recs = res["per_task"]
    name = res["run"].get("model", path)
    print(f"\n=== {name}  ({len(recs)} scored) ===")

    parse = collections.Counter(r.get("parse_failure") or "ok" for r in recs)
    print("  parse outcome:      " + ", ".join(f"{k}={v}" for k, v in parse.most_common()))
    print(f"  scorer errors:      {sum(int(bool(r.get('scorer_error'))) for r in recs)}")
    print(f"  gate failures:      {sum(int(not r.get('gate_passed', True)) for r in recs)}")

    # Stop reasons live in the cache, not the results file: they are a property of the
    # request, and the results file is deliberately about scoring.
    stops = collections.Counter()
    for p in CACHE.rglob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("model") == res["run"].get("cache_model", res["run"].get("model")):
            stops[rec.get("stop") or "unknown"] += 1
    if stops:
        print("  stop reasons:       " + ", ".join(f"{k}={v}" for k, v in stops.most_common()))
        print(f"  TRUNCATED (length): {stops.get('length', 0)}  "
              f"<- harness budget, not model failure")

    # Fact-format compliance, over abstentions the model actually made.
    named = [r for r in recs if r.get("named_facts")]
    if named:
        exact = sum(1 for r in named if all(
            f == f.strip() and " " not in f for f in r["named_facts"]))
        print(f"  abstentions naming a fact: {len(named)}; "
              f"in exact identifier form: {exact} ({exact / len(named):.1%})")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        report(p)
