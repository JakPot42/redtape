"""HR 1 immigrant eligibility probe, RE-RUN WITH STATE AS A VARIABLE.

Why this script exists
----------------------
`scripts/probe_immigration.py` hardcoded `state_name: CA` and swept only months of
2025. It concluded the engine had not implemented PL 119-21 section 10108 at all.
That conclusion was wrong, and the hardcode is why.

PolicyEngine-US 1.821.4 encodes the restriction in two layers:

  * parameters/gov/usda/snap/eligibility/eligible_immigration_statuses.yaml
      2025-07-01 -> CITIZEN, LEGAL_PERMANENT_RESIDENT, CUBAN_HAITIAN_ENTRANT
  * parameters/gov/states/ca/cdss/snap/eligibility/eligible_immigration_statuses.yaml
      2026-04-01 -> same three, i.e. California DELAYS the restriction (CDSS ACL 25-92)

and `is_snap_immigration_status_eligible` returns `federal_eligible | ca_eligible`,
where `ca_snap_immigration_status_eligible` is `defined_for = StateCode.CA`.

So a California-only probe in 2025 sees no change at July 2025 because there is
correctly no change in California until April 2026. This probe varies the state and
crosses BOTH date boundaries, which is what the original should have done.
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression

vs = CountryTaxBenefitSystem().variables
STATUSES = [e.name for e in vs["immigration_status"].possible_values]

# Two boundaries, bracketed on each side.
#   2025-07-01 federal restriction begins (PL 119-21 s.10108, real effective 2025-07-04)
#   2026-04-01 California's delay expires (CDSS ACL 25-92)
CELLS = [
    ("2025-06", 2025),
    ("2025-07", 2025),
    ("2026-03", 2026),
    ("2026-04", 2026),
]
STATES = ["CA", "TX"]

REPORT = [
    "snap",
    "is_snap_eligible",
    "is_snap_immigration_status_eligible",
    "ca_snap_immigration_status_eligible",
    "ca_cfap",
]


def run(status, month, year, state, earned=14_400):
    y = str(year)
    ids = ["p1", "p2"]
    sit = {
        "people": {
            "p1": {"age": {y: 35}, "employment_income": {y: earned},
                   "immigration_status": {y: status}},
            "p2": {"age": {y: 8}, "employment_income": {y: 0},
                   "immigration_status": {y: "CITIZEN"}},
        },
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {y: 12_000},
                            "has_heating_cooling_expense": {y: True}}},
        "households": {"h": {"members": ids, "state_name": {y: state}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    sim = Simulation(situation=apply_suppression(sit, year))
    out = {}
    for v in REPORT:
        if v not in vs:
            out[v] = "N/A"
            continue
        per = month if vs[v].definition_period == "month" else year
        try:
            r = sim.calculate(v, per)
            out[v] = bool(r[0]) if r.dtype == bool else float(r[0])
        except Exception as exc:  # noqa: BLE001 - probe, report don't raise
            out[v] = f"ERR:{type(exc).__name__}"
    return out


grid = {}
for state in STATES:
    for status in STATUSES:
        for month, year in CELLS:
            grid[(state, status, month)] = run(status, month, year, state)

W = 112
print("=" * W)
print("A. SNAP benefit by state x immigration status x month")
print("   2-person household, $1,200/mo earned, housing $1,000/mo, heating/cooling expense")
print("=" * W)
print()
for state in STATES:
    print(f"  --- {state} ---")
    print(f"  {'status':<28}" + "".join(f"{m:>12}" for m, _ in CELLS))
    print("  " + "-" * (28 + 12 * len(CELLS)))
    for status in STATUSES:
        row = "".join(
            f"{grid[(state, status, m)]['snap']:>12,.0f}" for m, _ in CELLS
        )
        print(f"  {status:<28}{row}")
    print()

print("=" * W)
print("B. is_snap_immigration_status_eligible (the status test itself)")
print("=" * W)
print()
print(f"  {'status':<28}" + "".join(
    f"{s + ' ' + m:>14}" for s in STATES for m, _ in CELLS))
print("  " + "-" * (28 + 14 * len(STATES) * len(CELLS)))
for status in STATUSES:
    cells = "".join(
        f"{str(grid[(s, status, m)]['is_snap_immigration_status_eligible']):>14}"
        for s in STATES for m, _ in CELLS
    )
    print(f"  {status:<28}{cells}")

print()
print("=" * W)
print("C. Where does each status change, per state?")
print("=" * W)
for state in STATES:
    print(f"\n  --- {state} ---")
    for status in STATUSES:
        seq = [grid[(state, status, m)]["is_snap_immigration_status_eligible"]
               for m, _ in CELLS]
        flips = [
            f"{CELLS[i - 1][0]}->{CELLS[i][0]}: {seq[i - 1]}->{seq[i]}"
            for i in range(1, len(seq)) if seq[i] != seq[i - 1]
        ]
        print(f"    {status:<28}" + ("  ".join(flips) if flips
                                     else f"no change (constant {seq[0]})"))

print()
print("=" * W)
print("D. The claim LIMITS 16 made, tested directly")
print("=" * W)
tx_ref_jun = grid[("TX", "REFUGEE", "2025-06")]["is_snap_immigration_status_eligible"]
tx_ref_jul = grid[("TX", "REFUGEE", "2025-07")]["is_snap_immigration_status_eligible"]
ca_ref_jul = grid[("CA", "REFUGEE", "2025-07")]["is_snap_immigration_status_eligible"]
ca_ref_mar = grid[("CA", "REFUGEE", "2026-03")]["is_snap_immigration_status_eligible"]
ca_ref_apr = grid[("CA", "REFUGEE", "2026-04")]["is_snap_immigration_status_eligible"]
print(f"  TX REFUGEE 2025-06 -> 2025-07 : {tx_ref_jun} -> {tx_ref_jul}"
      f"   (expect True -> False; federal layer bites immediately)")
print(f"  CA REFUGEE 2025-07            : {ca_ref_jul}"
      f"   (expect True; ACL 25-92 delay)")
print(f"  CA REFUGEE 2026-03 -> 2026-04 : {ca_ref_mar} -> {ca_ref_apr}"
      f"   (expect True -> False; CA delay expires)")
print()
print("  LIMITS 16 claimed 'nothing changes at the 2025-07-04 boundary'. That is true")
print("  of California and ONLY of California. The probe never varied the state.")

print()
print("=" * W)
print("E. COFA representability (unchanged finding; tracked upstream as issue #8296)")
print("=" * W)
print(f"  immigration_status values ({len(STATUSES)}): {STATUSES}")
hit = any(t in s.upper() for s in STATUSES
          for t in ("COFA", "COMPACT", "MICRO", "MARSHALL", "PALAU"))
print(f"  contains a COFA / Compact of Free Association option? {hit}")
print("  The federal 2025-07-01 parameter layer carries COFA only as a YAML comment,")
print("  so the category that REMAINS eligible still cannot be expressed as an input.")

print()
print("=" * W)
print("F. years_since_us_entry: exists as an input, but SNAP's status test ignores it")
print("=" * W)
present = "years_since_us_entry" in vs
print(f"  variable present: {present}")
if present:
    v = vs["years_since_us_entry"]
    print(f"  default_value: {v.default_value}   definition_period: {v.definition_period}")
print("  LIMITS 16 said the engine 'has no date-of-entry input'. It has one; the")
print("  five-year bar is still not applied to SNAP, but for a different reason:")
print("  is_snap_immigration_status_eligible never reads this variable.")
