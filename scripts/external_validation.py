"""External validation of the oracle against PUBLISHED CalFresh tables.

Every published constant below is quoted from an external source with its retrieval date.
Nothing here comes from memory, and nothing comes from PolicyEngine.

Two comparison types:

  FORMULA - full hand calculation from published tables and the published formula:
      net    = gross - standard deduction - 20% of EARNED income - excess shelter
      excess = min(cap, max(0, shelter + SUA - 0.5 * income_after_other_deductions))
      benefit= max allotment - ceil(0.30 * net)

  ALLOTMENT - a zero-income household has zero net income, so its benefit must equal the
      published maximum allotment for its size. This checks one published cell directly
      and needs no deduction constants.

CalWORKs/TANF is explicitly suppressed. PolicyEngine models a zero-earnings California
household as receiving cash aid, which counts as unearned income for SNAP; a published
SNAP example takes gross income as given. Not suppressing it produces a spurious
discrepancy whose cause is "modelling scope", not an engine error.

Run: ./.venv/bin/python scripts/external_validation.py
"""

from __future__ import annotations

import math

from policyengine_us import Simulation

# ----------------------------------------------------------------------------------
# PUBLISHED CONSTANTS
# ----------------------------------------------------------------------------------
# [A] LSNC Guide to CalFresh Benefits, "Maximum CalFresh deductions",
#     https://calfresh.guide/maximum-calfresh-deductions/  retrieved 2026-08-29.
#     Effective 10/01/2024-09/30/2025 (FFY2025).
# [B] Santa Clara County DEBS, "CalFresh Program Monthly Allotment and Income
#     Eligibility Standards Charts", retrieved 2026-08-29.
#     Effective 10/01/2025-09/30/2026 (FFY2026).
# [C] Santa Clara County DEBS Update 24-07, "CalFresh COLA for FFY 2025",
#     retrieved 2026-08-29. Effective 10/01/2024-09/30/2025.

FFY2025 = {
    "std_deduction": {1: 204, 2: 204, 3: 204, 4: 217, 5: 254, 6: 291},  # [A]
    "earned_rate": 0.20,                                                # [A]
    "sua": 645,                                                          # [A][C]
    "max_excess_shelter": 712,                                           # [A][C]
    "max_allotment": {2: 536},                                           # [C]
    "cite": "[A] LSNC calfresh.guide; [C] SCC Update 24-07",
}
FFY2026 = {
    "max_allotment": {1: 298, 2: 546, 3: 785, 4: 994, 5: 1183, 6: 1421, 7: 1571, 8: 1789},  # [B]
    "gross_limit": {1: 1696, 2: 2292, 3: 2888, 4: 3483},                 # [B]
    "net_limit": {1: 1305, 2: 1763, 3: 2221, 4: 2680},                   # [B]
    "cite": "[B] SCC DEBS allotment/income chart",
}

MONTHS_2025 = [f"2025-{i:02d}" for i in range(1, 13)]


def oracle(size: int, earned_annual: float, shelter_annual: float, month: str):
    ids = [f"p{i+1}" for i in range(size)]
    people = {
        i: {
            "age": {"2025": 30 if k == 0 else 8},
            "employment_income": {"2025": earned_annual if k == 0 else 0},
            "immigration_status": {"2025": "CITIZEN"},
        }
        for k, i in enumerate(ids)
    }
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {
            "s": {
                "members": ids,
                "housing_cost": {"2025": shelter_annual},
                "tanf": {"2025": 0},
                "ca_tanf": {"2025": 0},
            }
        },
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": [ids[0]]}},
    }
    sim = Simulation(situation=sit)
    return float(sim.calculate("snap", month)[0])


def formula_ffy2025(size: int, earned_m: float, shelter_m: float) -> float:
    p = FFY2025
    std = p["std_deduction"][min(size, 6)]
    after = max(0.0, earned_m - std - p["earned_rate"] * earned_m)
    excess = min(p["max_excess_shelter"], max(0.0, shelter_m + p["sua"] - 0.5 * after))
    net = max(0.0, after - excess)
    return max(0.0, p["max_allotment"][size] - math.ceil(0.30 * net))


CASES = [
    # kind, label, size, earned/mo, shelter/mo, month, published value, source
    ("FORMULA", "2p, $1,200 earned, $900 rent", 2, 1200, 900, "2025-04"),
    ("FORMULA", "2p, $0 earned, $900 rent", 2, 0, 900, "2025-04"),
    ("FORMULA", "2p, $2,000 earned, $1,200 rent", 2, 2000, 1200, "2025-04"),
    ("FORMULA", "2p, $800 earned, $0 rent", 2, 800, 0, "2025-04"),
    ("ALLOTMENT", "1p, zero income", 1, 0, 0, "2025-11"),
    ("ALLOTMENT", "2p, zero income", 2, 0, 0, "2025-11"),
    ("ALLOTMENT", "3p, zero income", 3, 0, 0, "2025-11"),
    ("ALLOTMENT", "4p, zero income", 4, 0, 0, "2025-11"),
    ("ALLOTMENT", "5p, zero income", 5, 0, 0, "2025-11"),
    ("ALLOTMENT", "6p, zero income", 6, 0, 0, "2025-11"),
]


def main() -> None:
    print("=" * 112)
    print("TEN EXTERNAL WORKED EXAMPLES - published CalFresh tables vs redtape oracle")
    print("=" * 112)
    print(f"{'#':>2} {'kind':<10} {'case':<31} {'month':<8} {'FFY':<8} "
          f"{'published':>10} {'oracle':>9} {'delta':>8} verdict")
    print("-" * 112)

    rows = []
    for i, (kind, label, size, earned, shelter, month) in enumerate(CASES, 1):
        ffy = "FFY2026" if month >= "2025-10" else "FFY2025"
        if kind == "FORMULA":
            pub = formula_ffy2025(size, earned, shelter)
        else:
            pub = float(FFY2026["max_allotment"][size])
        got = oracle(size, earned * 12, shelter * 12, month)
        delta = got - pub
        verdict = "MATCH" if abs(delta) < 1.0 else "DISCREPANCY"
        rows.append((i, kind, label, month, ffy, pub, got, delta, verdict))
        print(f"{i:>2} {kind:<10} {label:<31} {month:<8} {ffy:<8} "
              f"{pub:>10,.2f} {got:>9,.2f} {delta:>8,.2f} {verdict}")

    print("-" * 112)
    ok = sum(1 for r in rows if r[8] == "MATCH")
    print(f"matched within the $1 tolerance: {ok}/{len(rows)}")
    print()
    print("sources: " + FFY2025["cite"] + "; " + FFY2026["cite"])
    print("all constants retrieved 2026-08-29; engine policyengine-us==1.821.4")


if __name__ == "__main__":
    main()
