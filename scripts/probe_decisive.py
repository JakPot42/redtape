"""Which declared fact actually restores the disability axis for SNAP?

7 CFR 271.2 defines an elderly-or-disabled member by RECEIPT of a qualifying benefit.
PolicyEngine implements this as is_usda_disabled = OR over gov.usda.disabled_programs:

    is_ssi_disabled                          (bool - an SSI DETERMINATION, not an amount)
    social_security_disability               (float - SSDI receipt)
    is_permanently_disabled_veteran          (bool)
    is_surviving_spouse_of_disabled_veteran  (bool)
    is_surviving_child_of_disabled_veteran   (bool)

So declaring an SSI dollar amount is NOT enough - `ssi` is not in that list. Declaring
SSDI receipt should be.
"""

from policyengine_us import Simulation

from redtape.oracle.takeup import apply_suppression

MONTH = "2025-04"


def run(age=45, is_disabled=False, declare=None):
    declare = declare or {}
    ids = ["p1", "p2"]
    p1 = {"age": {"2025": age}, "employment_income": {"2025": 18_000},
          "immigration_status": {"2025": "CITIZEN"}, "is_disabled": {"2025": is_disabled}}
    for k, v in declare.items():
        if isinstance(v, bool):
            p1[k] = {"2025": v}
    sit = {
        "people": {"p1": p1,
                   "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0},
                          "immigration_status": {"2025": "CITIZEN"}}},
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 30_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    money = {k: v for k, v in declare.items() if not isinstance(v, bool)}
    sim = Simulation(situation=apply_suppression(sit, 2025, {"p1": money} if money else {}))
    return (
        float(sim.calculate("snap", MONTH)[0]),
        float(sim.calculate("snap_excess_shelter_expense_deduction", MONTH)[0]),
        bool(sim.calculate("has_snap_elderly_disabled_member", MONTH)[0]),
        bool(sim.calculate("is_usda_disabled", 2025)[0]),
    )


CASES = [
    ("baseline: age 45, nothing declared", dict(age=45)),
    ("is_disabled=True only (self-report)", dict(age=45, is_disabled=True)),
    ("declared SSI $967/mo (an AMOUNT)", dict(age=45, declare={"ssi": 967 * 12})),
    ("declared SSDI $1,200/mo", dict(age=45, declare={"social_security_disability": 1200 * 12})),
    ("declared disabled veteran", dict(age=45, declare={"is_permanently_disabled_veteran": True})),
    ("age 60 (elderly threshold)", dict(age=60)),
    ("age 67", dict(age=67)),
]

print("=" * 100)
print("2-person, $1,500/mo earned, $2,500/mo rent, CA, 2025-04  (shelter cap FFY2025 = $712)")
print("=" * 100)
print(f"{'case':<40} {'snap $/mo':>10} {'shelter ded':>12} {'eld/dis?':>9} {'usda_dis?':>10}")
print("-" * 100)
for label, kw in CASES:
    snap, ded, eld, usda = run(**kw)
    print(f"{label:<40} {snap:>10,.2f} {ded:>12,.2f} {str(eld):>9} {str(usda):>10}")
print("-" * 100)
print("shelter deduction of exactly 712.00 means the cap applied (no exemption).")
