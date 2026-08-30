"""Build a T1/T1b split. Answer keys are baked here and never recomputed at rollout.

Run:  ./.venv/bin/python scripts/build_split.py --n 20 --out data/dev/t1.jsonl
"""

from __future__ import annotations

import argparse
import json
import platform
from importlib.metadata import version
from pathlib import Path

from redtape.generator.households import generate, withhold
from redtape.generator.narratives import render
from redtape.oracle.determinability import SWEEPS, probe
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import SCORED_PROGRAMS, Determinability

# T1b fact rotation. `is_higher_ed_student` is the only confirmed ELIGIBILITY-flipping
# fact (docs/LIMITS.md 22), so it gets its own share of the budget.
T1B_FACTS = (
    "housing_cost",
    "p1.employment_income",
    "p1.immigration_status",
    "p1.age",
    "dependent_care_cost",
)
FLIP_FACT = "p1.is_higher_ed_student"


def _record(hh, narrative, key, label, deciding, withheld, pair_id=""):
    return {
        "prompt": narrative,
        "name": hh.household_id,
        "household_id": hh.household_id,
        "seed": hh.seed,
        "index": hh.index,
        "answer_key_json": key.answer.model_dump_json(),
        "determinability": label.value,
        # Only scored programs. A task must never expect an abstention on a program
        # the scorer does not score (medicaid is computed but unscored, LIMITS 20).
        "deciding_programs": [p for p in deciding if p in SCORED_PROGRAMS],
        "withheld_fact": withheld,
        "pair_id": pair_id,
        "engine_version": key.engine_version,
        "python_version": key.python_version,
    }


def build(seed: int, n: int, t1b_fraction: float, flip_fraction: float):
    out = []
    n_flip = int(n * flip_fraction)
    n_t1b = int(n * t1b_fraction) - n_flip

    for i in range(n):
        hh = generate(seed, i)

        # Complete case: answer normally.
        if i >= n_t1b + n_flip:
            key = compute(hh)
            out.append(_record(hh, render(hh), key, Determinability.DETERMINATE, (), ""))
            continue

        fact = FLIP_FACT if i < n_flip else T1B_FACTS[i % len(T1B_FACTS)]
        # An eligibility-flip case needs the fact to be present-and-varied, so make
        # sure the base household actually has a student to withhold.
        holed = withhold(hh, fact)
        label = probe(holed, fact)

        # The key is computed on the household WITH the fact restored to its true value,
        # because the ground truth is what the household's real circumstances imply.
        key = compute(hh)
        out.append(
            _record(hh, render(holed), key, label.label, label.deciding_programs, fact)
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--t1b-fraction", type=float, default=0.35)
    ap.add_argument("--flip-fraction", type=float, default=0.08)
    ap.add_argument("--out", default="data/dev/t1.jsonl")
    args = ap.parse_args()

    rows = build(args.seed, args.n, args.t1b_fraction, args.flip_fraction)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    counts = {}
    for r in rows:
        counts[r["determinability"]] = counts.get(r["determinability"], 0) + 1
    print(f"wrote {len(rows)} tasks -> {path}")
    print(f"  engine {version('policyengine-us')}  python {platform.python_version()}")
    for k, v in sorted(counts.items()):
        print(f"  {k:<26} {v:>4}  ({100 * v / len(rows):.0f}%)")


if __name__ == "__main__":
    main()
