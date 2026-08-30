"""Enumerate modelled take-up for household shapes v0 will generate, including the
elderly/disabled shapes the HR 1 probe needs.

Goal: find EVERY program the engine pays a household that the narrative never mentions.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

MONTHS = [f"2025-{i:02d}" for i in range(1, 13)]
vs = CountryTaxBenefitSystem().variables


def build(ages, disabled=None, earned=0, shelter=10_800, suppress_spm=(), suppress_person=()):
    disabled = disabled or [False] * len(ages)
    ids = [f"p{i+1}" for i in range(len(ages))]
    people = {}
    for k, i in enumerate(ids):
        d = {
            "age": {"2025": ages[k]},
            "employment_income": {"2025": earned if k == 0 else 0},
            "immigration_status": {"2025": "CITIZEN"},
            "is_disabled": {"2025": disabled[k]},
        }
        for var in suppress_person:
            v = vs[var]
            d[var] = {"2025": 0} if v.definition_period == "year" else {m: 0 for m in MONTHS}
        people[i] = d
    spm = {"members": ids, "housing_cost": {"2025": shelter}}
    for var in suppress_spm:
        v = vs[var]
        spm[var] = {"2025": 0} if v.definition_period == "year" else {m: 0 for m in MONTHS}
    return Simulation(
        situation={
            "people": people,
            "tax_units": {"tu": {"members": ids}},
            "families": {"f": {"members": ids}},
            "spm_units": {"s": spm},
            "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
            "marital_units": {"m": {"members": [ids[0]]}},
        }
    )


SHAPES = {
    "adult+child, no earnings": ([30, 8], [False, False]),
    "senior 67 alone": ([67], [False]),
    "senior 67 + disabled 40": ([67, 40], [False, True]),
    "disabled adult 45 alone": ([45], [True]),
    "adult 30 alone": ([30], [False]),
}

PROBE = [
    "snap_gross_income", "snap_earned_income", "snap_unearned_income",
    "tanf", "ca_tanf", "ssi", "ca_state_supplement", "social_security",
    "unemployment_compensation", "ca_cfap", "wic",
]

print("=" * 88)
print("MODELLED TAKE-UP BY HOUSEHOLD SHAPE (nothing suppressed, zero stated income)")
print("=" * 88)
for label, (ages, dis) in SHAPES.items():
    sim = build(ages, dis)
    print(f"\n{label}")
    for v in PROBE:
        if v not in vs:
            continue
        per = "2025-04" if vs[v].definition_period == "month" else 2025
        try:
            val = sim.calculate(v, per)
            tot = float(sum(val))
            if abs(tot) > 0.01 or v.startswith("snap_"):
                print(f"    {v:<32} {per!s:<9} {val}")
        except Exception as e:
            print(f"    {v:<32} ERR {type(e).__name__}")

print()
print("=" * 88)
print("WITH tanf/ca_tanf/ssi/ca_state_supplement SUPPRESSED")
print("=" * 88)
SPM_SUPPRESS = ("tanf", "ca_tanf")
PERSON_SUPPRESS = tuple(v for v in ("ssi", "ca_state_supplement", "social_security")
                        if v in vs and vs[v].entity.key == "person")
print(f"  spm-level:    {SPM_SUPPRESS}")
print(f"  person-level: {PERSON_SUPPRESS}")
for label, (ages, dis) in SHAPES.items():
    try:
        sim = build(ages, dis, suppress_spm=SPM_SUPPRESS, suppress_person=PERSON_SUPPRESS)
        g = float(sim.calculate("snap_gross_income", "2025-04")[0])
        u = float(sim.calculate("snap_unearned_income", "2025-04")[0])
        flag = "" if abs(g) < 0.01 else "   <-- STILL LEAKING"
        print(f"  {label:<28} gross={g:>9,.2f}  unearned={u:>9,.2f}{flag}")
    except Exception as e:
        print(f"  {label:<28} ERROR {type(e).__name__}: {str(e)[:90]}")
