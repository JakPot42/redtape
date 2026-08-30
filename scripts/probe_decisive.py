"""Do is_disabled and age-60 status show up as DECISIVE facts for SNAP?

They should. The published excess-shelter cap ($712 FFY2025) does not apply to a
household containing a member aged 60+ or disabled, so for a household with high shelter
costs, either fact should flip the benefit.

If the prober does not find them, the prober is missing real determinability.
"""

from redtape.generator.households import withhold
from redtape.oracle.determinability import SWEEPS, probe
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import Household, ImmigrationStatus, Person


def hh(ages, disabled=None, earned=1500.0, housing_m=2500.0, month="2025-04") -> Household:
    disabled = disabled or [False] * len(ages)
    people = tuple(
        Person(person_id=f"p{i+1}", age=a,
               employment_income=earned * 12 if i == 0 else 0.0,
               immigration_status=ImmigrationStatus.CITIZEN, is_disabled=disabled[i])
        for i, a in enumerate(ages)
    )
    return Household(household_id="probe", seed=0, index=0, month=month,
                     people=people, housing_cost=housing_m * 12)


print("=" * 92)
print("Direct effect of age and disability at HIGH shelter cost (cap exemption)")
print("=" * 92)
print("  2-person household, $1,500/mo earned, $2,500/mo rent, 2025-04")
print(f"  {'p1 age':>7} {'disabled':>9} {'snap $/mo':>11}")
for age in (35, 59, 60, 66, 75):
    v = compute(hh([age, 8])).answer.snap.benefit
    print(f"  {age:>7} {'False':>9} {v:>11,.2f}")
for dis in (False, True):
    v = compute(hh([35, 8], [dis, False])).answer.snap.benefit
    print(f"  {35:>7} {str(dis):>9} {v:>11,.2f}")

print()
print("=" * 92)
print("PROBER verdicts - is the fact reported as deciding?")
print("=" * 92)
SCENARIOS = [
    ("high shelter $2,500/mo, earned $1,500/mo", hh([35, 8])),
    ("low shelter $600/mo, earned $1,500/mo", hh([35, 8], housing_m=600.0)),
    ("high shelter, zero earnings", hh([35, 8], earned=0.0)),
]
for label, base in SCENARIOS:
    print(f"\n{label}")
    for fact in ("p1.age", "p1.is_disabled"):
        lab = probe(withhold(base, fact), fact)
        mark = "DECIDING" if "snap" in lab.deciding_programs else "not deciding for snap"
        print(f"  {fact:<18} {lab.label.value:<24} {mark}")
        snap_v = next(v for v in lab.per_program if v.program == "snap")
        vals = sorted({o for o in snap_v.observed})
        print(f"     sweep {SWEEPS[fact.split('.')[1]]}")
        print(f"     distinct snap outcomes: {len(vals)}")
        for o in vals[:6]:
            print(f"       {o}")
