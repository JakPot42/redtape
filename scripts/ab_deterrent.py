"""A/B: does the prompt's one-sided deterrent clause suppress abstention?

**The confound.** The shipped prompt ends:

    "...instead of guessing. If a fact is missing but the outcome does not depend on it,
     answer normally - a needless abstention is scored as wrong as a wrong number."

The scoring is symmetric - abstaining needlessly and failing to abstain cost exactly the
same - but that sentence is not. It names only the over-abstention penalty and never the
under-abstention one, so a model has been told what it loses by abstaining wrongly and
nothing about what it loses by staying silent. Opus 5 emitted `cannot_determine` on 5.6%
of the dev split and scored 0.006 on the indeterminate class; publishing that as
calibration, with this clause in the prompt, invites the charge that the result was built
into the instructions.

**Arm B BALANCES the clause rather than deleting it.** Deleting it would compare silence
against a deterrent, which is a different question - silence would also remove the
benchmark's genuine warning that needless abstention is penalised, and any movement could
be explained by that removal alone. Balancing holds the information content constant and
changes only the symmetry, which is the thing under test.

Both arms run the same tasks, the same model and identical sampling parameters. The cache
key hashes the system prompt, so arm A is served from the existing dev-split cache at no
cost and arm B correctly misses.
"""
from __future__ import annotations

import collections
import json
import sys

sys.path.insert(0, "/home/jak/redtape")

from redtape.config import load_dotenv  # noqa: E402

load_dotenv()

from eval.run_eval import live_agent, score_one  # noqa: E402
from redtape.envs.t1_eligibility import SYSTEM_PROMPT, T1Data, T1Task, T1TaskConfig  # noqa: E402

MODEL = "claude-opus-5"
SPLIT = "/home/jak/redtape/data/dev/t1.jsonl"

DETERRENT = (
    "If a fact is missing but the outcome does not depend on it, answer normally - "
    "a needless abstention is scored as wrong as a wrong number."
)
BALANCED = (
    "If a fact is missing but the outcome does not depend on it, answer normally - "
    "a needless abstention is scored as wrong as a wrong number, and answering when a "
    "required fact is missing is scored as wrong as a needless abstention."
)

assert DETERRENT in SYSTEM_PROMPT, "deterrent clause not found; prompt changed"
PROMPT_A = SYSTEM_PROMPT
PROMPT_B = SYSTEM_PROMPT.replace(DETERRENT, BALANCED)
assert PROMPT_A != PROMPT_B

# Weighted toward the classes where abstention is the CORRECT answer, because those are
# the cells the confound would distort. incomplete_determinate and determinate are kept so
# the arms can be checked for a rise in NEEDLESS abstention - a balanced clause that merely
# made the model abstain more everywhere would not be evidence of better calibration.
QUOTA = {"indeterminate": 24, "eligibility_flip": 20,
         "incomplete_determinate": 10, "determinate": 6}


def cls(r):
    return "eligibility_flip" if r.get("is_eligibility_flip") else r["determinability"]


def sample():
    rows = [json.loads(ln) for ln in open(SPLIT, encoding="utf-8") if ln.strip()]
    by = collections.defaultdict(list)
    for r in rows:
        by[cls(r)].append(r)
    picked = []
    for c, n in QUOTA.items():
        # Deterministic: sort by index and take a fixed stride, so the sample is
        # reproducible and is not the first N of anything.
        group = sorted(by[c], key=lambda r: r["index"])
        stride = max(1, len(group) // n)
        picked.extend(group[::stride][:n])
    return picked


def run_arm(name, prompt, rows):
    cfg = T1TaskConfig()
    agent = live_agent("tool_less", MODEL, system_prompt=prompt)
    recs = []
    for r in rows:
        payload = {k: v for k, v in r.items()
                   if k not in ("task_hash", "task_key", "unscored_deciding_programs",
                                "pair_truth_differs")}
        task = T1Task(T1Data.model_validate(payload), cfg)
        reply = agent(task)
        recs.append((r, score_one(task, reply), reply))
    print(f"  arm {name}: {len(recs)} tasks scored", flush=True)
    return recs


def summarise(name, recs):
    by_class = collections.defaultdict(lambda: [0, 0])
    raw_abstain = 0
    t1b_ok = t1b_n = 0
    for r, rec, reply in recs:
        c = cls(r)
        try:
            obj = json.loads(reply)
            if obj.get("cannot_determine"):
                raw_abstain += 1
        except (json.JSONDecodeError, AttributeError):
            pass
        if c == "determinate":
            continue
        ok = 1 if rec.abstention_correct else 0
        by_class[c][0] += ok
        by_class[c][1] += 1
        t1b_ok += ok
        t1b_n += 1
    return {
        "name": name,
        "by_class": {k: tuple(v) for k, v in by_class.items()},
        "t1b": (t1b_ok, t1b_n),
        "raw_abstain": (raw_abstain, len(recs)),
    }


rows = sample()
print(f"sample: {len(rows)} tasks  " +
      "  ".join(f"{c}={sum(1 for r in rows if cls(r) == c)}" for c in QUOTA))

A = summarise("A (shipped, deterrent only)", run_arm("A", PROMPT_A, rows))
B = summarise("B (balanced clause)", run_arm("B", PROMPT_B, rows))

print()
print("=" * 92)
print("A/B: ONE-SIDED DETERRENT vs BALANCED CLAUSE")
print("=" * 92)
print(f"{'':<34}{'arm A (shipped)':>22}{'arm B (balanced)':>22}{'delta':>12}")
print("-" * 92)


def line(label, a, b):
    aa = a[0] / a[1] if a[1] else 0.0
    bb = b[0] / b[1] if b[1] else 0.0
    print(f"{label:<34}{f'{a[0]}/{a[1]} = {aa:.3f}':>22}"
          f"{f'{b[0]}/{b[1]} = {bb:.3f}':>22}{bb - aa:>+12.3f}")


for c in ("indeterminate", "eligibility_flip", "incomplete_determinate"):
    line(c, A["by_class"].get(c, (0, 0)), B["by_class"].get(c, (0, 0)))
print("-" * 92)
line("ALL T1b (abstention accuracy)", A["t1b"], B["t1b"])
line("replies with ANY cannot_determine", A["raw_abstain"], B["raw_abstain"])
