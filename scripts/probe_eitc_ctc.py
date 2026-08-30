"""EITC / CTC: what do the engine's variables actually mean, and do they match published?

Published TY2025 anchors (two independent sources: IRS.gov EITC tables page and Tax
Foundation's 2025 parameters page, both retrieved 2026-08-30; consistent with
Rev. Proc. 2024-40 as summarised by both):

  max EITC:            0 kids $649 | 1 kid $4,328 | 2 kids $7,152 | 3+ kids $8,046
  EITC phaseout ends (single/HoH): $19,104 | $50,434 | $57,310 | $61,555
  investment income limit: $11,950
  CTC: $2,200 per qualifying child (PL 119-21), refundable ACTC $1,700,
       phaseout begins $200,000 single / $400,000 MFJ, -$50 per $1,000 over,
       ACTC requires >= $2,500 earned income
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables

print("=" * 84)
print("A. CTC-related variables - which one is the amount a household RECEIVES?")
print("=" * 84)
for k in sorted(vs):
    kl = k.lower()
    if kl in ("ctc", "refundable_ctc", "non_refundable_ctc", "ctc_refundable",
              "additional_child_tax_credit", "ctc_value", "ctc_child_individual_maximum",
              "ctc_limiting_tax_liability", "ctc_phase_out"):
        print(f"  {k:<44} {vs[k].entity.key}/{vs[k].definition_period}")
print()
for k in sorted(vs):
    if k.startswith("ctc") or k.endswith("_ctc"):
        print(f"  {k:<44} {vs[k].entity.key}/{vs[k].definition_period}")


def build(n_children, earned, child_ages=None, month="2025-04"):
    child_ages = child_ages or [8] * n_children
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    people = {"p1": {"age": {"2025": 35}, "employment_income": {"2025": earned},
                     "immigration_status": {"2025": "CITIZEN"}}}
    for i, a in enumerate(child_ages):
        people[f"c{i+1}"] = {"age": {"2025": a}, "employment_income": {"2025": 0},
                             "immigration_status": {"2025": "CITIZEN"}}
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025))


print()
print("=" * 84)
print("B. CTC semantics: zero-income household with 2 children")
print("=" * 84)
sim = build(2, 0)
for v in ("ctc", "refundable_ctc", "non_refundable_ctc", "ctc_value",
          "income_tax", "income_tax_before_credits"):
    if v in vs:
        try:
            print(f"  {v:<34} {sim.calculate(v, 2025)}")
        except Exception as e:
            print(f"  {v:<34} ERR {type(e).__name__}")
print("  published: with $0 earned income the household gets NOTHING refundable")
print("             (ACTC requires >= $2,500 earned). A `ctc` of 4,400 would be the")
print("             GROSS credit before limitation, not what is received.")

print()
print("=" * 84)
print("C. EITC across the phase-in / plateau / phase-out regions")
print("=" * 84)
PUB_MAX = {0: 649, 1: 4328, 2: 7152, 3: 8046}
PUB_END = {0: 19_104, 1: 50_434, 2: 57_310, 3: 61_555}
for n in (0, 1, 2, 3):
    print(f"\n  {n} qualifying children (published max ${PUB_MAX[n]:,}, "
          f"phaseout ends ${PUB_END[n]:,})")
    for earned in (0, 5_000, 9_000, 15_000, 20_000, 30_000, 45_000, 55_000, 65_000):
        e = float(build(n, earned).calculate("eitc", 2025)[0])
        flag = ""
        if abs(e - PUB_MAX[n]) < 1:
            flag = "  <- equals published MAX"
        elif e == 0 and earned >= PUB_END[n]:
            flag = "  <- zero at/after published phaseout end"
        print(f"    earned {earned:>7,} -> eitc {e:>9,.2f}{flag}")
