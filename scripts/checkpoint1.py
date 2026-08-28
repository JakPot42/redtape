"""Checkpoint 1 deliverable: ten households, their oracle outputs, and the
determinability table.

Run:  ./.venv/bin/python scripts/checkpoint1.py
"""

from __future__ import annotations

import platform
from importlib.metadata import version

from redtape.generator.households import generate_many
from redtape.oracle.determinability import probe
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import Determinability

SEED = 20260828
N = 10

# One withheld fact per household, cycling, so the table covers every sweepable fact.
FACT_CYCLE = (
    "housing_cost",
    "p1.employment_income",
    "p1.immigration_status",
    "p1.age",
    "p1.is_disabled",
)


def main() -> None:
    print(f"redtape Checkpoint 1  |  seed={SEED}  n={N}")
    print(f"policyengine-us=={version('policyengine-us')}  python {platform.python_version()}")
    print()

    households = generate_many(SEED, N)

    print("=" * 100)
    print("TEN GENERATED HOUSEHOLDS AND THEIR ORACLE OUTPUT")
    print("=" * 100)
    hdr = f"{'household':<20} {'ppl':>3} {'month':<8} {'income':>9} {'housing':>8} " \
          f"{'snap_el':>7} {'snap$/mo':>9} {'medicaid':<10} {'eitc':>9} {'ctc':>8}"
    print(hdr)
    print("-" * 100)

    results = []
    for hh in households:
        r = compute(hh)
        a = r.answer
        results.append((hh, r))
        income = sum(p.employment_income for p in hh.people)
        med = "".join("T" if v else "." for v in a.medicaid.person_eligible.values())
        print(
            f"{hh.household_id:<20} {len(hh.people):>3} {hh.month:<8} {income:>9,.0f} "
            f"{hh.housing_cost:>8,.0f} {str(a.snap.eligible):>7} {a.snap.benefit:>9,.2f} "
            f"{med:<10} {a.eitc.amount:>9,.2f} {a.ctc.amount:>8,.2f}"
        )

    print("-" * 100)
    print("medicaid column: one char per person, T=eligible")
    print("snap is MONTHLY for the stated month. eitc/ctc are ANNUAL for 2025.")
    print()

    print("=" * 100)
    print("DETERMINABILITY TABLE - one withheld fact per household")
    print("=" * 100)
    print(f"{'household':<20} {'withheld fact':<24} {'label':<24} {'deciding programs'}")
    print("-" * 100)

    counts = {d: 0 for d in Determinability}
    rows = []
    for i, (hh, _) in enumerate(results):
        fact = FACT_CYCLE[i % len(FACT_CYCLE)]
        from redtape.generator.households import withhold

        lab = probe(withhold(hh, fact), fact)
        counts[lab.label] += 1
        rows.append(lab)
        print(
            f"{hh.household_id:<20} {fact:<24} {lab.label.value:<24} "
            f"{', '.join(lab.deciding_programs) or '(none)'}"
        )

    print("-" * 100)
    total = len(rows)
    for d in Determinability:
        if d is Determinability.DETERMINATE:
            continue
        pct = 100 * counts[d] / total if total else 0
        print(f"  {d.value:<24} {counts[d]:>3} / {total}  ({pct:.0f}%)")
    print()
    print("  indeterminate         -> abstention is the correct answer (T1b class 2)")
    print("  incomplete_determinate-> model should answer despite the gap (T1b class 3)")
    print()
    print("  Labels are an UNDER-APPROXIMATION from a declared finite sweep; see")
    print("  docs/LIMITS.md 4. Sweep ranges are recorded with every label.")


if __name__ == "__main__":
    main()
