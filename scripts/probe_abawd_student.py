"""Do ABAWD time limits or student status produce is_snap_eligible FLIPS?

Disability and the gross income test both get squeezed out by California's broad-based
categorical eligibility. These are the two other routes.

ABAWD (HR 1 widened the age band to 18-64 from 18-54, narrowed the dependent-child
exemption to children under 14, and REMOVED the veteran, former foster youth and
homeless exemptions). The engine has `is_snap_abawd_hr1_in_effect`, so some of this is
modelled.

Students (7 CFR 273.5): enrolled more than half-time in higher education means ineligible
unless an exemption applies.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables

print("=" * 92)
print("A. ABAWD and student variables present")
print("=" * 92)
for group in ("abawd", "student", "work_requirement", "wsr", "foster", "homeless", "veteran"):
    hits = sorted(k for k in vs if group in k.lower() and "snap" in k.lower())
    if hits:
        print(f"  --- {group} ---")
        for h in hits:
            print(f"      {h:<56} {vs[h].entity.key}/{vs[h].definition_period}/{vs[h].value_type.__name__}")


def build(age=30, months_abawd=None, student=None, hours=0, n_children=0,
          child_age=8, earned=0, month="2025-11", extra_person=None):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    p1 = {"age": {"2025": age}, "employment_income": {"2025": earned},
          "immigration_status": {"2025": "CITIZEN"}}
    if months_abawd is not None and "months_of_snap_abawd_exemption_received" in vs:
        p1["months_of_snap_abawd_exemption_received"] = {"2025": months_abawd}
    for name, val in (("is_full_time_student", student), ("is_in_school", student)):
        if val is not None and name in vs:
            p1[name] = {"2025": val}
    if hours and "weekly_hours_worked" in vs:
        p1["weekly_hours_worked"] = {"2025": hours}
    people = {"p1": p1}
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


def report(sim, month="2025-11"):
    out = {}
    for v in ("is_snap_eligible", "snap", "is_snap_abawd_hr1_in_effect",
              "is_snap_abawd", "meets_snap_abawd_work_requirements",
              "is_snap_ineligible_student", "meets_snap_work_requirements",
              "snap_unit_size"):
        if v in vs:
            per = month if vs[v].definition_period == "month" else 2025
            try:
                r = sim.calculate(v, per)
                out[v] = bool(r[0]) if r.dtype == bool else float(r[0])
            except Exception:
                out[v] = "ERR"
    return out


print()
print("=" * 92)
print("B. ABAWD: single adult, no children, no earnings, 2025-11")
print("=" * 92)
for age in (25, 40, 52, 56, 60, 64, 66):
    d = report(build(age=age))
    print(f"  age {age:>3}: " + "  ".join(f"{k}={v}" for k, v in d.items()))

print()
print("=" * 92)
print("C. ABAWD across months of 2025 (age 40, single, no earnings)")
print("=" * 92)
for m in ("2025-01", "2025-06", "2025-07", "2025-09", "2025-11", "2025-12"):
    d = report(build(age=40, month=m), m)
    print(f"  {m}: " + "  ".join(f"{k}={v}" for k, v in d.items()))

print()
print("=" * 92)
print("D. Dependent-child exemption: HR 1 narrowed it to children UNDER 14")
print("=" * 92)
for ca in (5, 12, 13, 14, 16):
    d = report(build(age=40, n_children=1, child_age=ca))
    print(f"  child age {ca:>2}: " + "  ".join(f"{k}={v}" for k, v in d.items()))

print()
print("=" * 92)
print("E. Student status (7 CFR 273.5)")
print("=" * 92)
for st in (None, False, True):
    d = report(build(age=20, student=st))
    print(f"  student={st!s:<6}: " + "  ".join(f"{k}={v}" for k, v in d.items()))
