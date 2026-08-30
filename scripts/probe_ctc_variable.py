"""Which CTC variable is the amount a household actually receives?

Published TY2025 (PL 119-21): $2,200 per qualifying child; refundable ACTC capped at
$1,700 per child; ACTC requires at least $2,500 of earned income; the credit phases out
above $200,000 (single) at $50 per $1,000.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables


def build(n_children, earned, child_age=8):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    people = {"p1": {"age": {"2025": 35}, "employment_income": {"2025": earned},
                     "immigration_status": {"2025": "CITIZEN"}}}
    for i in range(n_children):
        people[f"c{i+1}"] = {"age": {"2025": child_age}, "employment_income": {"2025": 0},
                             "immigration_status": {"2025": "CITIZEN"}}
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025))


VARS = ["ctc", "non_refundable_ctc", "refundable_ctc", "ctc_value", "income_tax"]

print("=" * 92)
print("2 children under 17, single filer. Published: $2,200/child gross, ACTC cap")
print("$1,700/child, ACTC needs >= $2,500 earned.")
print("=" * 92)
print(f"{'earned':>9} " + "".join(f"{v:>20}" for v in VARS))
print("-" * 92)
for earned in (0, 2_000, 2_500, 5_000, 12_000, 20_000, 30_000, 45_000, 60_000):
    sim = build(2, earned)
    row = []
    for v in VARS:
        try:
            row.append(float(sim.calculate(v, 2025)[0]))
        except Exception:
            row.append(float("nan"))
    print(f"{earned:>9,} " + "".join(f"{x:>20,.2f}" for x in row))

print()
print("=" * 92)
print("CTC phase-out at high income (1 child). Published: begins $200,000 single,")
print("-$50 per $1,000 over, so zero at $244,000.")
print("=" * 92)
print(f"{'earned':>10} {'ctc':>14} {'ctc_value':>14} {'refundable_ctc':>16}")
for earned in (190_000, 200_000, 210_000, 230_000, 244_000, 260_000):
    sim = build(1, earned)
    vals = [float(sim.calculate(v, 2025)[0]) for v in ("ctc", "ctc_value", "refundable_ctc")]
    print(f"{earned:>10,} {vals[0]:>14,.2f} {vals[1]:>14,.2f} {vals[2]:>16,.2f}")

print()
print("=" * 92)
print("Child aged 17 (NOT a qualifying child for CTC) vs aged 8")
print("=" * 92)
for age in (8, 16, 17, 18):
    sim = build(1, 30_000, child_age=age)
    print(f"  child age {age}: ctc={float(sim.calculate('ctc', 2025)[0]):>10,.2f}  "
          f"ctc_value={float(sim.calculate('ctc_value', 2025)[0]):>10,.2f}")
