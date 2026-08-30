"""Retroactive extreme-range audit of every scored variable.

Both errors found so far were of one kind: a plausibly-named variable reporting a GROSS
entitlement where the RECEIVED value was meant (`ctc` vs `ctc_value`; `medicaid` dollars
vs `is_medicaid_eligible`). Both were invisible in the middle of the range and only
separated at an extreme - zero income, or income far above a phase-out.

This audit generalises the detection: for each variable the oracle reads, look for
sibling variables the engine defines that could represent the same quantity differently
(`X_value`, `refundable_X`, `non_refundable_X`, `is_X_eligible`), evaluate all of them at
both extremes of each input dimension, and report every place they disagree.

A disagreement is not automatically a bug - but a scored variable that disagrees with a
sibling at an extreme is exactly the shape of the two errors already found, so each one
must be explained rather than assumed benign.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables

# Variables the oracle currently reads for the answer.
ORACLE_VARIABLES = {
    "snap.eligible": "is_snap_eligible",
    "snap.benefit": "snap",
    "eitc.amount": "eitc",
    "ctc.amount": "ctc_value",
    "medicaid.person_eligible": "is_medicaid_eligible",  # computed, unscored
}

# Input dimensions and their extremes. Both ends of each, deliberately.
EXTREMES = {
    "employment_income": (0.0, 500_000.0),
    "housing_cost": (0.0, 120_000.0),
    "dependent_care_cost": (0.0, 40_000.0),
    "age": (18, 95),
    "n_children": (0, 6),
}


def siblings(name: str) -> list[str]:
    """Other engine variables that might represent the same quantity differently."""
    base = name
    for prefix in ("is_", "refundable_", "non_refundable_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
    base = base.removesuffix("_value").removesuffix("_eligible")
    cands = [
        base, f"{base}_value", f"refundable_{base}", f"non_refundable_{base}",
        f"is_{base}_eligible", f"{base}_eligible",
    ]
    return [c for c in dict.fromkeys(cands) if c in vs and c != name]


def build(employment_income=18_000.0, housing_cost=12_000.0, dependent_care_cost=0.0,
          age=35, n_children=2, month="2025-11"):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    people = {"p1": {"age": {"2025": age}, "employment_income": {"2025": employment_income},
                     "immigration_status": {"2025": "CITIZEN"}}}
    for i in range(n_children):
        people[f"c{i+1}"] = {"age": {"2025": 8}, "employment_income": {"2025": 0},
                             "immigration_status": {"2025": "CITIZEN"}}
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": housing_cost},
                            "childcare_expenses": {"2025": dependent_care_cost},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025)), month


def read(sim, name, month):
    v = vs[name]
    per = month if v.definition_period == "month" else 2025
    r = sim.calculate(name, per)
    return float(r[0]) if r.dtype != bool else float(bool(r[0]))


print("=" * 100)
print("SIBLING VARIABLES per oracle variable")
print("=" * 100)
for field, name in ORACLE_VARIABLES.items():
    sib = siblings(name)
    print(f"  {field:<28} reads {name:<22} siblings: {sib or '(none)'}")

print()
print("=" * 100)
print("EXTREME-RANGE DISAGREEMENTS")
print("=" * 100)
findings = []
for dim, (lo, hi) in EXTREMES.items():
    for end, val in (("MIN", lo), ("MAX", hi)):
        sim, month = build(**{dim: val})
        for field, name in ORACLE_VARIABLES.items():
            sib = siblings(name)
            if not sib:
                continue
            try:
                mine = read(sim, name, month)
            except Exception:
                continue
            for other in sib:
                try:
                    theirs = read(sim, other, month)
                except Exception:
                    continue
                if abs(mine - theirs) > 1.0:
                    findings.append((dim, end, val, field, name, mine, other, theirs))

if not findings:
    print("  none")
else:
    print(f"{'dimension':<22} {'end':<4} {'field':<26} {'reads':>18} {'=':>12} "
          f"{'sibling':>22} {'=':>12}")
    print("-" * 100)
    for dim, end, val, field, name, mine, other, theirs in findings:
        print(f"{dim + '=' + format(val, ',.0f'):<22} {end:<4} {field:<26} "
              f"{name:>18} {mine:>12,.2f} {other:>22} {theirs:>12,.2f}")

print()
print(f"  {len(findings)} disagreement(s). Each must be explained, not assumed benign.")
