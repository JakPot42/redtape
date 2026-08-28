"""Decisive test of monthly->annual aggregation.

Core (_calculate, lines 70-76):
    if definition_period == MONTH and period.unit == YEAR:
        if quantity_type == STOCK:  values = last contained month
        else:                       values = calculate_add(...)   # sum

So the answer is per-variable, keyed on quantity_type. Confirm both branches,
and confirm what "sum" means for a bool-typed array.
"""

import numpy as np

from policyengine_us import CountryTaxBenefitSystem, Simulation

print("=" * 72)
print("PART 1 - quantity_type for the variables v0 depends on")
print("=" * 72)
vs = CountryTaxBenefitSystem().variables
for n in ("snap", "is_snap_eligible", "medicaid", "is_medicaid_eligible", "eitc", "ctc",
          "snap_excess_shelter_expense_deduction", "housing_cost"):
    v = vs[n]
    qt = getattr(v.quantity_type, "name", v.quantity_type)
    print(f"  {n:<40} period={v.definition_period:<8} type={v.value_type.__name__:<5} quantity_type={qt}")

print()
print("=" * 72)
print("PART 2 - what does summing a bool array mean in numpy?")
print("=" * 72)
a = np.array([True]); b = np.array([False])
print(f"  np.array([True]) + np.array([False]) = {a + b}  dtype={(a + b).dtype}")
print(f"  np.array([True]) + np.array([True])  = {a + a}  dtype={(a + a).dtype}")
print("  -> bool addition is logical OR, not an integer count.")

print()
print("=" * 72)
print("PART 3 - feed a monthly bool that genuinely differs, query annually")
print("=" * 72)
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]

def annual_for(pattern, label):
    """pattern: dict month -> bool, supplied directly as an input override."""
    situation = {
        "people": {"parent": {"age": {"2025": 35}}, "child": {"age": {"2025": 5}}},
        "tax_units": {"tu": {"members": ["parent", "child"]}},
        "families": {"fam": {"members": ["parent", "child"]}},
        "spm_units": {"spm": {"members": ["parent", "child"], "is_snap_eligible": pattern}},
        "households": {"hh": {"members": ["parent", "child"], "state_name": {"2025": "CA"}}},
        "marital_units": {"mu": {"members": ["parent"]}},
    }
    sim = Simulation(situation=situation)
    per_month = [bool(sim.calculate("is_snap_eligible", m)[0]) for m in MONTHS]
    ann = sim.calculate("is_snap_eligible", 2025)[0]
    print(f"  {label}")
    print(f"    months  : {''.join('T' if x else '.' for x in per_month)}  (ANY={any(per_month)} ALL={all(per_month)})")
    print(f"    annual  : {ann}")
    print(f"    == ANY? {bool(ann) == any(per_month)}    == ALL? {bool(ann) == all(per_month)}")
    return bool(ann), any(per_month), all(per_month)

cases = [
    ({m: (int(m[-2:]) <= 6) for m in MONTHS}, "eligible Jan-Jun only"),
    ({m: (int(m[-2:]) >= 7) for m in MONTHS}, "eligible Jul-Dec only"),
    ({m: (int(m[-2:]) == 3) for m in MONTHS}, "eligible March only"),
    ({m: (int(m[-2:]) == 12) for m in MONTHS}, "eligible December only"),
    ({m: False for m in MONTHS}, "never eligible"),
]
results = []
for pat, lbl in cases:
    results.append(annual_for(pat, lbl))
    print()

print("=" * 72)
matches_any = all(a == an for a, an, al in results)
matches_all = all(a == al for a, an, al in results)
print(f"  annual bool matches ANY in every case: {matches_any}")
print(f"  annual bool matches ALL in every case: {matches_all}")
print("=" * 72)
