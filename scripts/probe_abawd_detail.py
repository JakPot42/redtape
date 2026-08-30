"""Second pass: correct input variables for student status and ABAWD."""

import inspect

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

s = CountryTaxBenefitSystem()
vs = s.variables

for name in ("is_snap_abawd_hr1_in_effect", "is_subject_to_snap_abawd",
             "is_snap_ineligible_student", "is_snap_abawd_exempt"):
    print("=" * 88)
    print(name)
    print("=" * 88)
    try:
        print(inspect.getsource(type(vs[name])))
    except Exception as e:
        print("  no source:", e)


def build(age=30, month="2025-11", higher_ed=None, n_children=0, child_age=8,
          earned=0, abawd_months=None, indian=None):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    p1 = {"age": {"2025": age}, "employment_income": {"2025": earned},
          "immigration_status": {"2025": "CITIZEN"}}
    if higher_ed is not None:
        p1["is_snap_higher_ed_student"] = {"2025": higher_ed}
    if indian is not None and "is_snap_abawd_indian_exempt" in vs:
        p1["is_snap_abawd_indian_exempt"] = {"2025": indian}
    if abawd_months is not None:
        for cand in ("months_of_snap_abawd_time_limit_used",
                     "snap_abawd_months_used", "months_snap_abawd_received"):
            if cand in vs:
                p1[cand] = {"2025": abawd_months}
                break
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


def show(sim, month, label):
    out = []
    for v in ("is_snap_eligible", "snap", "is_snap_ineligible_student",
              "is_snap_higher_ed_student", "is_subject_to_snap_abawd",
              "is_snap_abawd_exempt", "is_snap_abawd_hr1_in_effect"):
        if v in vs:
            per = month if vs[v].definition_period == "month" else 2025
            try:
                r = sim.calculate(v, per)
                out.append(f"{v}={bool(r[0]) if r.dtype == bool else float(r[0])}")
            except Exception:
                out.append(f"{v}=ERR")
    print(f"  {label:<34} " + "  ".join(out))


print()
print("=" * 88)
print("STUDENT: is_snap_higher_ed_student")
print("=" * 88)
for he in (False, True):
    for age in (20, 25):
        show(build(age=age, higher_ed=he), "2025-11", f"age {age}, higher_ed={he}")

print()
print("=" * 88)
print("ABAWD: is_subject_to_snap_abawd by age and month")
print("=" * 88)
for m in ("2025-06", "2025-11"):
    for age in (25, 40, 52, 56, 64):
        show(build(age=age, month=m), m, f"{m}, age {age}")
