"""HR 1 immigrant eligibility probe.

Published rule (CBPP, "A Quick Guide to SNAP Eligibility and Benefits", updated
2025-10-03, endnote 6, citing PL 119-21): SNAP eligibility is restricted to

  * U.S. citizens
  * lawful permanent residents (after a five-year waiting period where applicable)
  * people granted Cuban or Haitian entrant status
  * people living in the U.S. under a Compact of Free Association

PL 119-21 was enacted 2025-07-04. Categories that were previously eligible and are NOT in
that list - refugees, asylees, people with deportation withheld, conditional entrants -
should therefore lose federal SNAP eligibility.

Our generator already uses immigration_status as a T1b fact and the prober measured it as
decisive, so if the engine has not implemented this, answer keys already generated are
wrong for those statuses.

Confounder handled: California runs CFAP, a state-funded food programme for immigrants
ineligible for federal SNAP. `snap` and `ca_cfap` are reported separately so a CFAP
substitution is not mistaken for federal SNAP eligibility.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables
STATUSES = [e.name for e in vs["immigration_status"].possible_values]
MONTHS = ["2025-01", "2025-05", "2025-06", "2025-07", "2025-08", "2025-10", "2025-12"]


def run(status, month, earned=14_400):
    ids = ["p1", "p2"]
    sit = {
        "people": {
            "p1": {"age": {"2025": 35}, "employment_income": {"2025": earned},
                   "immigration_status": {"2025": status}},
            "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0},
                   "immigration_status": {"2025": "CITIZEN"}},
        },
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    sim = Simulation(situation=apply_suppression(sit, 2025))
    out = {"snap": float(sim.calculate("snap", month)[0])}
    for v, per in (("ca_cfap", month), ("is_snap_eligible", month),
                   ("ca_snap_immigration_status_eligible", 2025)):
        if v in vs:
            p = per if vs[v].definition_period == "month" else 2025
            try:
                r = sim.calculate(v, p)
                out[v] = float(r[0]) if r.dtype != bool else bool(r[0])
            except Exception:
                out[v] = "ERR"
    return out


print("=" * 104)
print("A. SNAP benefit by immigration status and month (2-person CA household, $1,200/mo earned)")
print("=" * 104)
print("   PL 119-21 enacted 2025-07-04. Under it, only CITIZEN / LPR(+5yr) /")
print("   CUBAN_HAITIAN_ENTRANT / COFA should remain federally eligible.")
print()
hdr = f"{'status':<28}" + "".join(f"{m[-2:]:>9}" for m in MONTHS)
print(hdr)
print("-" * 104)
rows = {}
for st in STATUSES:
    vals = [run(st, m)["snap"] for m in MONTHS]
    rows[st] = vals
    print(f"{st:<28}" + "".join(f"{v:>9,.0f}" for v in vals))
print("-" * 104)
print("columns are months of 2025: 01 05 06 07 08 10 12")

print()
print("=" * 104)
print("B. Did ANY status change across the 2025-07-04 boundary?")
print("=" * 104)
changed = []
for st, vals in rows.items():
    pre = vals[:3]   # Jan, May, Jun
    post = vals[3:]  # Jul, Aug, Oct, Dec
    # Ignore the Oct COLA step by comparing Jun -> Jul only.
    if abs(vals[2] - vals[3]) > 0.5:
        changed.append((st, vals[2], vals[3]))
if changed:
    for st, a, b in changed:
        print(f"  {st:<28} Jun {a:,.2f} -> Jul {b:,.2f}   CHANGED")
else:
    print("  NO status changed at the 2025-07-04 boundary.")
    print("  (The only movement anywhere is the FFY2026 COLA at 2025-10.)")

print()
print("=" * 104)
print("C. Detail for the four statuses HR 1 should have made ineligible")
print("=" * 104)
for st in ("REFUGEE", "ASYLEE", "DEPORTATION_WITHHELD", "CONDITIONAL_ENTRANT"):
    if st not in STATUSES:
        continue
    print(f"\n  {st}")
    for m in ("2025-06", "2025-08", "2025-12"):
        d = run(st, m)
        print(f"    {m}: " + "  ".join(f"{k}={v}" for k, v in d.items()))

print()
print("=" * 104)
print("D. Is a COFA / Compact of Free Association status representable at all?")
print("=" * 104)
print(f"  immigration_status values the engine supports ({len(STATUSES)}):")
print(f"    {STATUSES}")
print(f"  contains a COFA/Micronesia/Marshall/Palau option? "
      f"{any(t in s.upper() for s in STATUSES for t in ('COFA', 'COMPACT', 'MICRO', 'MARSHALL', 'PALAU'))}")
