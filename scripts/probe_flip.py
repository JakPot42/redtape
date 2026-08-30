"""Does declaring SSDI / veteran status ever flip ELIGIBILITY in California?

The gross-income-test exemption is modelled (meets_snap_gross_income_test flips), but
California operates broad-based categorical eligibility, which may already waive the
gross test for everyone - in which case the exemption is inert for outcomes here.

Sweep earnings to find any income at which the flip changes is_snap_eligible.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables


def run(earned_m, ssdi=0, month="2025-11"):
    ids = ["p1", "p2", "p3"]
    sit = {
        "people": {
            "p1": {"age": {"2025": 35}, "employment_income": {"2025": earned_m * 12},
                   "immigration_status": {"2025": "CITIZEN"}},
            "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0}},
            "p3": {"age": {"2025": 5}, "employment_income": {"2025": 0}},
        },
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 14_376},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    decl = {"p1": {"social_security_disability": ssdi}} if ssdi else {}
    sim = Simulation(situation=apply_suppression(sit, 2025, decl))
    g = {}
    for v in ("is_snap_eligible", "snap", "meets_snap_gross_income_test",
              "meets_snap_net_income_test", "is_snap_categorically_eligible",
              "snap_categorical_eligibility"):
        if v in vs:
            per = month if vs[v].definition_period == "month" else 2025
            r = sim.calculate(v, per)
            g[v] = bool(r[0]) if r.dtype == bool else float(r[0])
    return g


print("=" * 100)
print("3-person CA household, 2025-11. Sweep earnings; compare no-declaration vs SSDI declared.")
print("=" * 100)
print(f"{'earned/mo':>10} | {'--- no declaration ---':^34} | {'--- SSDI $1,200/mo declared ---':^34}")
print(f"{'':>10} | {'elig':>5} {'snap':>8} {'gross_t':>8} {'net_t':>6} | {'elig':>5} {'snap':>8} {'gross_t':>8} {'net_t':>6}")
print("-" * 100)
flips = []
for e in (1500, 2500, 2900, 3200, 3500, 4000, 4500, 5000, 6000, 8000):
    a = run(e)
    b = run(e, ssdi=1200 * 12)
    if a["is_snap_eligible"] != b["is_snap_eligible"]:
        flips.append(e)
    print(f"{e:>10,} | {str(a['is_snap_eligible']):>5} {a['snap']:>8,.0f} "
          f"{str(a['meets_snap_gross_income_test']):>8} {str(a['meets_snap_net_income_test']):>6} | "
          f"{str(b['is_snap_eligible']):>5} {b['snap']:>8,.0f} "
          f"{str(b['meets_snap_gross_income_test']):>8} {str(b['meets_snap_net_income_test']):>6}")
print("-" * 100)
print(f"  earnings at which declaring SSDI FLIPS eligibility: {flips or 'NONE'}")

print()
print("=" * 100)
print("categorical eligibility variables present")
print("=" * 100)
for k in sorted(vs):
    if "categoric" in k.lower():
        print(f"  {k:<52} {vs[k].entity.key}/{vs[k].definition_period}/{vs[k].value_type.__name__}")
d = run(5000)
for k, v in d.items():
    print(f"  at $5,000/mo, no declaration: {k:<42} {v}")
