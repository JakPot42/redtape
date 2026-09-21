"""Fact-format compliance, and how often a model names a fact that was never withheld.

Both are methodological results rather than scores (LIMITS §39). The prompt lists every
withheld-fact identifier verbatim (`schemas.PERSON_FACTS` / `HOUSEHOLD_FACTS`), so naming
one in free text is a choice made against an explicit instruction, and naming a fact that
was never withheld is a claim about the case file that is simply false. The abstention
scorer's exact matching is only fair because of the first, and only meaningful if we also
report the second.

Read through the real parser and the real `_acceptable_facts`, so what is counted here is
what the scorer saw - not a second, kinder implementation of the same idea.

Usage: fact_format.py <results.json> [...]
"""

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redtape.schemas import HOUSEHOLD_FACTS, PERSON_FACTS      # noqa: E402
from redtape.scoring.core import _acceptable_facts             # noqa: E402
from redtape.scoring.parsing import parse_answer               # noqa: E402

CACHE = ROOT / "cache" / "responses" / "dev"
KNOWN = set(HOUSEHOLD_FACTS) | set(PERSON_FACTS)


def _is_identifier(fact: str) -> bool:
    """Exactly one of the identifiers the prompt lists, person-qualified or not."""
    f = fact.strip()
    if f != fact or " " in f:
        return False
    return f.rpartition(".")[2] in KNOWN


def report(path):
    res = json.load(open(path, encoding="utf-8"))
    name = res["run"]["model"]
    cache_model = res["run"]["provider"]["provider"] + ":" + res["run"]["provider"]["api_model"] \
        if res["run"].get("provider", {}).get("provider") != "anthropic" \
        else res["run"]["provider"]["api_model"]
    by_hash = {r["task_hash"]: r for r in res["per_task"]}

    replies = {}
    for p in CACHE.rglob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("model") == cache_model and rec.get("task_hash") in by_hash:
            replies[rec["task_hash"]] = rec["reply"]

    counts = collections.Counter()
    invented = collections.Counter()
    for h, reply in replies.items():
        task = by_hash[h]
        parsed = parse_answer(reply)
        if not parsed.ok:
            counts["unparseable"] += 1
            continue
        cds = [c for c in parsed.answer.cannot_determine if c.program != "medicaid"]
        if not cds:
            counts["no abstention"] += 1
            continue
        counts["abstained"] += 1
        facts = [c.missing_fact for c in cds]
        if all(_is_identifier(f) for f in facts):
            counts["exact identifier form"] += 1
        else:
            counts["free text or unknown identifier"] += 1
        # Did it name something that was not the withheld fact (or its coupled partner)?
        ok = _acceptable_facts(task["withheld_fact"]) if task["withheld_fact"] else set()
        for f in facts:
            if f.strip() not in ok:
                invented[task["determinability"]] += 1
                counts["named a fact that was not the withheld one"] += 1
                break

    print(f"\n=== {name} ({len(replies)} cached replies) ===")
    abst = counts["abstained"]
    for k in ("no abstention", "abstained", "exact identifier form",
              "free text or unknown identifier", "named a fact that was not the withheld one",
              "unparseable"):
        v = counts.get(k, 0)
        pct = f"  ({v / abst:.1%} of abstentions)" if abst and k not in (
            "no abstention", "abstained", "unparseable") else ""
        print(f"  {k:<46}{v:>6}{pct}")
    if invented:
        print("    of which, by class: " + ", ".join(
            f"{k}={v}" for k, v in invented.most_common()))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        report(p)
