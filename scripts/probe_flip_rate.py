"""How often does withholding `is_higher_ed_student` actually flip SNAP ELIGIBILITY?

The 8% eligibility-flip target is only reachable if the base rate is high enough to find
96 of them in a reasonable candidate budget. Measure before committing to the number,
rather than discovering it three hours into a generation run.
"""

from __future__ import annotations

import sys

from redtape.generator.households import generate, withhold
from redtape.oracle.determinability import probe

FACT = "p1.is_higher_ed_student"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260828

    flips = amount_only = neither = 0
    for i in range(n):
        hh = generate(seed, i)
        label = probe(withhold(hh, FACT), FACT)
        snap = next(v for v in label.per_program if v.program == "snap")
        elig = {o.split(" benefit=")[0] for o in snap.observed}
        if len(elig) > 1:
            flips += 1
            verdict = "ELIGIBILITY FLIP"
        elif snap.deciding:
            amount_only += 1
            verdict = "amount only"
        else:
            neither += 1
            verdict = "-"
        p1 = hh.people[0]
        print(f"{i:>3} age={p1.age:>2} inc={p1.employment_income:>8,.0f} "
              f"n={len(hh.people)} {label.label.value:<24} {verdict}")

    print(f"\n{n} candidates: {flips} eligibility flips, {amount_only} amount-only, "
          f"{neither} not deciding")
    print(f"eligibility-flip rate: {flips / n:.0%}")


if __name__ == "__main__":
    main()
