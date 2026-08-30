"""HR 1 SUA probe.

Published rules to test against (supplied by the reviewer from CDSS ACIN I-46-25 and
HR 1):

  1. Effective 2025-07-04, California's Heat and Eat option ends EXCEPT for households
     containing an elderly (60+) or disabled member.
  2. Effective 2025-10-31, the SUAS nominal payment ($20.01) - the mechanism that
     qualifies many CA households for the SUA - is limited to households that are NOT
     otherwise SUA-eligible, are NOT already receiving the maximum allotment for their
     size, and DO contain a member aged 60+ or disabled.

Both dates fall inside tax year 2025. If the engine does not model them, the affected
month ranges are a scope limitation, not an engine bug.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables
P = CountryTaxBenefitSystem().parameters

MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09",
          "2025-10", "2025-11", "2025-12"]


def build(ages, disabled=None, earned=12_000, housing=10_800, heating=True):
    disabled = disabled or [False] * len(ages)
    ids = [f"p{i+1}" for i in range(len(ages))]
    people = {
        i: {
            "age": {"2025": ages[k]},
            "employment_income": {"2025": earned if k == 0 else 0},
            "immigration_status": {"2025": "CITIZEN"},
            "is_disabled": {"2025": disabled[k]},
        }
        for k, i in enumerate(ids)
    }
    spm = {
        "members": ids,
        "housing_cost": {"2025": housing},
        "has_heating_cooling_expense": {"2025": heating},
    }
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": spm},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": [ids[0]]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025))


print("=" * 92)
print("A. Engine SUA / LUA parameter values vs published CDSS figures")
print("=" * 92)
print("  published: FFY2025 SUA $645, LUA $166   |   FFY2026 SUA $663, LUA $170")
for month, ffy in [("2025-04", "FFY2025"), ("2025-11", "FFY2026")]:
    sim = build([30, 8], heating=True)
    sua = float(sim.calculate("snap_standard_utility_allowance", month)[0])
    lua = float(sim.calculate("snap_limited_utility_allowance", month)[0])
    print(f"  {ffy} ({month}): engine SUA = {sua:,.2f}   LUA = {lua:,.2f}")

print()
print("=" * 92)
print("B. Heat-and-Eat boundary 2025-07-04 - does the SUA change for a NON-elderly")
print("   household with no separately-billed heating cost?")
print("=" * 92)
print(f"  {'month':<9} {'non-eld/non-dis':>18} {'elderly 67':>14} {'disabled 45':>14}")
for m in MONTHS:
    a = float(build([35, 8], heating=False).calculate("snap_utility_allowance", m)[0])
    b = float(build([67], heating=False).calculate("snap_utility_allowance", m)[0])
    c = float(build([45], [True], heating=False).calculate("snap_utility_allowance", m)[0])
    print(f"  {m:<9} {a:>18,.2f} {b:>14,.2f} {c:>14,.2f}")

print()
print("=" * 92)
print("C. Same households WITH a heating/cooling expense (ordinary SUA eligibility)")
print("=" * 92)
print(f"  {'month':<9} {'non-eld/non-dis':>18} {'elderly 67':>14}")
for m in MONTHS:
    a = float(build([35, 8], heating=True).calculate("snap_utility_allowance", m)[0])
    b = float(build([67], heating=True).calculate("snap_utility_allowance", m)[0])
    print(f"  {m:<9} {a:>18,.2f} {b:>14,.2f}")

print()
print("=" * 92)
print("D. Is the SUAS $20.01 nominal-payment mechanism modelled at all?")
print("=" * 92)
hits = sorted(k for k in vs if "suas" in k.lower())
print(f"  variables matching 'suas': {hits or 'NONE'}")
liheap_ca = sorted(k for k in vs if "liheap" in k.lower() and k.startswith("ca_"))
print(f"  CA LIHEAP variables:       {liheap_ca or 'NONE'}")
print(f"  HR1-aware variables:       {sorted(k for k in vs if 'hr1' in k.lower())}")
print()
print("  snap_utility_allowance_type by month (non-elderly, no heating expense):")
sim = build([35, 8], heating=False)
for m in ("2025-06", "2025-08", "2025-11", "2025-12"):
    try:
        t = sim.calculate("snap_utility_allowance_type", m, decode_enums=True)
        print(f"    {m}: {t}")
    except Exception as e:
        print(f"    {m}: {type(e).__name__}")

print()
print("  parameter: does anything in the utility subtree change during 2025?")
for name in ("standard", "limited", "always_standard", "single"):
    try:
        node = getattr(P.gov.usda.snap.income.deductions.utility, name)
        vals = []
        for inst in ("2025-05-01", "2025-07-05", "2025-10-01", "2025-11-01"):
            v = node(inst)
            ca = v._children.get("CA") if hasattr(v, "_children") else v
            vals.append(f"{inst[:7]}={ca}")
        print(f"    {name:<16} " + "  ".join(vals))
    except Exception as e:
        print(f"    {name:<16} {type(e).__name__}")
