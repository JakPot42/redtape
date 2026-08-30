"""External validation of the oracle against PUBLISHED CalFresh tables.

Every published constant is quoted from an external source with its retrieval date.
Nothing comes from memory, and nothing comes from PolicyEngine.

Comparison kinds:

  FORMULA   - full hand calculation from published tables and the published formula.
              This exercises calculation logic.
  ALLOTMENT - a zero-income household has zero net income, so its benefit must equal the
              published maximum allotment. This tests parameter loading, NOT calculation.
  PARAMETER - an engine parameter value read directly and compared to the published one.

The three are reported separately because they are not equally strong evidence.

FORMULA cases are restricted to months BEFORE 2025-07-04. From that date the published
HR 1 rules change SUA entitlement in ways the engine does not model (docs/LIMITS.md 11),
so a later-month formula case would be comparing against rules the engine never
implemented.

Run: ./.venv/bin/python scripts/external_validation.py
"""

from __future__ import annotations

import math

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

# ----------------------------------------------------------------------------------
# PUBLISHED CONSTANTS
# ----------------------------------------------------------------------------------
# [A] LSNC Guide to CalFresh Benefits, "Maximum CalFresh deductions",
#     calfresh.guide, retrieved 2026-08-29. FFY2025, eff. 10/01/2024-09/30/2025.
# [B] Santa Clara County DEBS allotment/income chart, retrieved 2026-08-29.
#     FFY2026, eff. 10/01/2025-09/30/2026.
# [C] SCC DEBS Update 24-07, CalFresh COLA FFY2025, retrieved 2026-08-29.
# [D] LSNC Guide to CalFresh Benefits, maximum allotments as of 10/01/2024,
#     retrieved 2026-08-29. FFY2025 full table.
# [E] CDSS ACIN I-46-25, FFY2026 COLA, supplied by the reviewer 2026-08-29.

FFY2025 = {
    "max_allotment": {1: 292, 2: 536, 3: 768, 4: 975, 5: 1158, 6: 1390, 7: 1536, 8: 1756},  # [D]
    "std_deduction": {1: 204, 2: 204, 3: 204, 4: 217, 5: 254, 6: 291},                       # [A]
    "earned_rate": 0.20,                                                                     # [A]
    "sua": 645, "lua": 166,                                                                  # [A][C]
    "max_excess_shelter": 712,                                                               # [A][C]
}
FFY2026 = {
    "max_allotment": {1: 298, 2: 546, 3: 785, 4: 994, 5: 1183, 6: 1421, 7: 1571, 8: 1789},  # [B][E]
    "sua": 663, "lua": 170,                                                                  # [E]
    # [F] FNS SNAP FY2026 COLA memo (sizes 1-3) and [G] CBPP endnote 9 (all sizes),
    # both read by the reviewer 2026-08-29. Sizes 4+ now have an external source, so the
    # engine's 223/261/299 is no longer self-confirming and FFY2026 FORMULA cases extend
    # to size 6.
    "std_deduction": {1: 209, 2: 209, 3: 209, 4: 223, 5: 261, 6: 299},
    "max_excess_shelter": 744,
    "earned_rate": 0.20,
    "homeless_shelter": 198.99,
    "min_benefit": 24,
}

MONTHS = [f"2025-{i:02d}" for i in range(1, 13)]


def snap(size, earned_m, shelter_m, month, ages=None, disabled=None, care_m=0):
    ages = ages or ([30] + [8] * (size - 1))
    disabled = disabled or [False] * size
    ids = [f"p{i+1}" for i in range(size)]
    people = {
        i: {
            "age": {"2025": ages[k]},
            "employment_income": {"2025": earned_m * 12 if k == 0 else 0},
            "immigration_status": {"2025": "CITIZEN"},
            "is_disabled": {"2025": disabled[k]},
        }
        for k, i in enumerate(ids)
    }
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": shelter_m * 12},
                            "childcare_expenses": {"2025": care_m * 12},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": [ids[0]]}},
    }
    return float(Simulation(situation=apply_suppression(sit, 2025)).calculate("snap", month)[0])


def formula(size, earned_m, shelter_m, ffy, care_m=0.0):
    """Published SNAP formula applied to published constants for the given FFY.

    Dependent care is deducted alongside the standard and earned-income deductions,
    before the shelter test - this is the channel the CBPP worked example exercises.
    """
    p = FFY2025 if ffy == "FFY2025" else FFY2026
    std = p["std_deduction"][min(size, 6)]
    after = max(0.0, earned_m - std - p["earned_rate"] * earned_m - care_m)
    excess = min(p["max_excess_shelter"], max(0.0, shelter_m + p["sua"] - 0.5 * after))
    return max(0.0, p["max_allotment"][size] - math.ceil(0.30 * max(0.0, after - excess)))


def formula_ffy2025(size, earned_m, shelter_m):
    p = FFY2025
    std = p["std_deduction"][min(size, 6)]
    after = max(0.0, earned_m - std - p["earned_rate"] * earned_m)
    excess = min(p["max_excess_shelter"], max(0.0, shelter_m + p["sua"] - 0.5 * after))
    return max(0.0, p["max_allotment"][size] - math.ceil(0.30 * max(0.0, after - excess)))


FORMULA_CASES = [
    # FFY2025, sizes 1-6. Months before 2025-07-04 only (HR 1 SUA divergence).
    (1, 900, 700, "2025-04"), (2, 1200, 900, "2025-04"), (3, 1500, 1100, "2025-04"),
    (4, 2000, 1400, "2025-04"), (5, 2400, 1600, "2025-04"), (6, 2800, 1800, "2025-04"),
    (2, 0, 900, "2025-04"), (2, 2000, 1200, "2025-04"), (3, 800, 0, "2025-06"),
    # FFY2026, sizes 1-3 only - the sizes for which the standard deduction is externally
    # sourced. These months are AFTER the HR 1 dates, so they carry the caveat in
    # docs/HR1_SUA_DIVERGENCE.md: they validate arithmetic against the engine's
    # (pre-HR 1) SUA treatment, not against post-HR 1 entitlement.
    (1, 900, 700, "2025-11"), (2, 1200, 900, "2025-11"), (3, 1500, 1100, "2025-11"),
    (3, 0, 1000, "2025-12"),
    # FFY2026 sizes 4-6, now that the standard deduction is externally sourced for them.
    (4, 2000, 1400, "2025-11"), (5, 2400, 1600, "2025-11"), (6, 2800, 1800, "2025-11"),
]

# Cases exercising the DEPENDENT CARE deduction, an otherwise untested channel.
# The last is CBPP's published FY2026 worked example, reproduced end to end.
#   (label, size, earned/mo, shelter/mo, care/mo, month, published_or_None)
CARE_CASES = [
    ("dependent care $200/mo", 3, 1500, 1100, 200, "2025-11", None),
    ("dependent care $600/mo", 3, 1500, 1100, 600, "2025-11", None),
    ("dependent care $56/mo", 3, 1672, 535, 56, "2025-11", None),
    # CBPP "A Quick Guide to SNAP Eligibility and Benefits", FY2026 worked example:
    # family of three, one full-time worker, two children; earnings $1,672/mo; child care
    # $56/mo; shelter $1,198/mo; countable income A $1,073; shelter deduction $661; net
    # $412; expected contribution ~$124; max allotment $785; benefit $661/mo.
    #
    # CBPP states shelter of $1,198 as the TOTAL including utilities. California grants
    # the $663 SUA unconditionally, so housing_cost is set to 1198 - 663 = 535 to make
    # the shelter total match.
    #
    # WHAT THIS CASE DOES AND DOES NOT VALIDATE. The substitution makes the comparison
    # like-for-like, and it is not circular - both sides are externally sourced. But it
    # BYPASSES the SUA logic entirely: the utility allowance is fed in as a fixed
    # quantity rather than derived. This case validates the deduction-and-benefit
    # formula, including the dependent care channel. It is NOT evidence that the
    # utility path is correct, and must never be counted as such - see
    # docs/HR1_SUA_DIVERGENCE.md for why the utility path is in fact known-divergent.
    ("CBPP FY2026 published example", 3, 1672, 535, 56, "2025-11", 661.0),
]
ALLOTMENT_CASES = [(s, "2025-11", FFY2026["max_allotment"][s]) for s in range(1, 9)]


def main():
    rows = []
    print("=" * 104)
    print("A. FORMULA - hand calculation from published tables (exercises calculation logic)")
    print("=" * 104)
    print(f"{'#':>2} {'size':>4} {'earned/mo':>10} {'rent/mo':>8} {'month':<9} {'FFY':<8} "
          f"{'published':>10} {'oracle':>9} {'delta':>8} verdict")
    print("-" * 104)
    for i, (size, e, sh, m) in enumerate(FORMULA_CASES, 1):
        ffy = "FFY2026" if m >= "2025-10" else "FFY2025"
        pub, got = formula(size, e, sh, ffy), snap(size, e, sh, m)  # no dependent care
        d = got - pub
        v = "MATCH" if abs(d) < 1.0 else "DISCREPANCY"
        rows.append(("FORMULA", v))
        print(f"{i:>2} {size:>4} {e:>10,} {sh:>8,} {m:<9} {ffy:<8} {pub:>10,.2f} "
              f"{got:>9,.2f} {d:>8,.2f} {v}")

    print()
    print("=" * 104)
    print("A2. DEPENDENT CARE - an otherwise untested deduction channel")
    print("=" * 104)
    print(f"{'#':>2} {'case':<34} {'size':>4} {'care/mo':>8} {'month':<9} "
          f"{'published':>10} {'oracle':>9} {'delta':>8} verdict")
    print("-" * 104)
    for i, (label, size, e, sh, care, m, pub_override) in enumerate(CARE_CASES, 1):
        ffy = "FFY2026" if m >= "2025-10" else "FFY2025"
        pub = pub_override if pub_override is not None else formula(size, e, sh, ffy, care)
        got = snap(size, e, sh, m, care_m=care)
        d = got - pub
        v = "MATCH" if abs(d) < 1.0 else "DISCREPANCY"
        rows.append(("FORMULA", v))
        src = " (CBPP)" if pub_override is not None else ""
        print(f"{i:>2} {label:<34} {size:>4} {care:>8,} {m:<9} {pub:>10,.2f} "
              f"{got:>9,.2f} {d:>8,.2f} {v}{src}")

    print()
    print("=" * 104)
    print("B. ALLOTMENT - zero-income households vs published FFY2026 table (tests parameter loading only)")
    print("=" * 104)
    print(f"{'#':>2} {'size':>4} {'month':<9} {'published':>10} {'oracle':>9} {'delta':>8} verdict")
    print("-" * 104)
    for i, (size, m, pub) in enumerate(ALLOTMENT_CASES, 1):
        got = snap(size, 0, 0, m)
        d = got - pub
        v = "MATCH" if abs(d) < 1.0 else "DISCREPANCY"
        rows.append(("ALLOTMENT", v))
        print(f"{i:>2} {size:>4} {m:<9} {pub:>10,.2f} {got:>9,.2f} {d:>8,.2f} {v}")

    print()
    print("=" * 104)
    print("C. PARAMETER - engine parameter values vs published figures")
    print("=" * 104)
    P = CountryTaxBenefitSystem().parameters
    sim = Simulation(situation=apply_suppression({
        "people": {"p1": {"age": {"2025": 30}}},
        "tax_units": {"tu": {"members": ["p1"]}}, "families": {"f": {"members": ["p1"]}},
        "spm_units": {"s": {"members": ["p1"], "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ["p1"], "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }, 2025))
    checks = [
        ("SUA FFY2025", 645, float(sim.calculate("snap_standard_utility_allowance", "2025-04")[0])),
        ("SUA FFY2026", 663, float(sim.calculate("snap_standard_utility_allowance", "2025-11")[0])),
        ("earned income deduction", 0.20,
         float(P.gov.usda.snap.income.deductions.earned_income("2025-04-01"))),
        ("gross income limit (xFPL)", 1.3, float(P.gov.usda.snap.income.limit.gross("2025-04-01"))),
        ("net income limit (xFPL)", 1.0, float(P.gov.usda.snap.income.limit.net("2025-04-01"))),
    ]
    print(f"{'parameter':<28} {'published':>10} {'engine':>10} verdict")
    print("-" * 104)
    for label, pub, got in checks:
        v = "MATCH" if abs(got - pub) < 0.01 else "DISCREPANCY"
        rows.append(("PARAMETER", v))
        print(f"{label:<28} {pub:>10,.2f} {got:>10,.2f} {v}")

    print()
    print("=" * 104)
    for kind in ("FORMULA", "ALLOTMENT", "PARAMETER"):
        sub = [r for r in rows if r[0] == kind]
        ok = sum(1 for r in sub if r[1] == "MATCH")
        print(f"  {kind:<10} {ok}/{len(sub)} match")
    ok = sum(1 for r in rows if r[1] == "MATCH")
    print(f"  {'TOTAL':<10} {ok}/{len(rows)} match")
    print()
    print("  FORMULA cases exercise calculation logic. ALLOTMENT cases test parameter")
    print("  loading only - do not read the combined figure as broad validation.")
    print("  Medicaid, EITC, CTC and all eligibility booleans remain unvalidated.")


if __name__ == "__main__":
    main()
