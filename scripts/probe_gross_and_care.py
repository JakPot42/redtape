"""Two probes:

A. Is the gross-income-test exemption for elderly/disabled households modelled?
   CBPP endnote 4: households with a member aged 60+ or with a disability are not subject
   to the gross income test at all. If modelled, declaring SSDI or veteran status can flip
   a household from INELIGIBLE to ELIGIBLE - an eligibility flip, the scarcest T1b class.

B. Which variable carries the dependent care deduction?
   The CBPP FY2026 worked example uses $56/month of child care, an entire deduction
   channel our corpus does not currently exercise.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

s = CountryTaxBenefitSystem()
vs = s.variables

print("=" * 96)
print("B. dependent-care deduction variables")
print("=" * 96)
for k in sorted(vs):
    kl = k.lower()
    if ("snap" in kl and ("care" in kl or "depend" in kl)) or kl in (
        "childcare_expenses", "spm_unit_capped_work_childcare_expenses",
        "care_expenses", "childcare_hours_per_week",
    ):
        print(f"  {k:<56} {vs[k].entity.key}/{vs[k].definition_period}/{vs[k].value_type.__name__}")

print()
print("=" * 96)
print("A. gross income test exemption")
print("=" * 96)


def run(age=35, ssdi=0, veteran=False, earned=42_000, month="2025-11", care=0):
    ids = ["p1", "p2", "p3"]
    p1 = {"age": {"2025": age}, "employment_income": {"2025": earned},
          "immigration_status": {"2025": "CITIZEN"}}
    if veteran:
        p1["is_permanently_disabled_veteran"] = {"2025": True}
    sit = {
        "people": {
            "p1": p1,
            "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0}},
            "p3": {"age": {"2025": 5}, "employment_income": {"2025": 0}},
        },
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 14_376},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    if care:
        for name in ("childcare_expenses", "spm_unit_pre_subsidy_childcare_expenses"):
            if name in vs:
                target = sit["people"]["p1"] if vs[name].entity.key == "person" else sit["spm_units"]["s"]
                target[name] = {"2025": care}
                break
    decl = {"p1": {"social_security_disability": ssdi}} if ssdi else {}
    sim = Simulation(situation=apply_suppression(sit, 2025, decl))
    out = {}
    for v in ("snap", "is_snap_eligible", "meets_snap_gross_income_test",
              "meets_snap_net_income_test", "has_snap_elderly_disabled_member",
              "snap_gross_income"):
        if v in vs:
            per = month if vs[v].definition_period == "month" else 2025
            r = sim.calculate(v, per)
            out[v] = bool(r[0]) if r.dtype == bool else float(r[0])
    return out


print("  3-person CA household, 2025-11 (FFY2026). Gross limit for size 3 = $2,888/mo.")
print("  Earnings set to $3,500/mo, deliberately ABOVE the gross income test.")
print()
CASES = [
    ("age 35, nothing declared", dict(age=35)),
    ("age 60", dict(age=60)),
    ("age 35 + declared SSDI $1,200/mo", dict(age=35, ssdi=1200 * 12)),
    ("age 35 + disabled veteran", dict(age=35, veteran=True)),
]
for label, kw in CASES:
    d = run(earned=42_000, **kw)
    print(f"  {label:<36}")
    for k, v in d.items():
        print(f"      {k:<40} {v}")
    print()
