"""Is the SUA monotone in the SNAP benefit, and is "already at the maximum allotment"
therefore safe to evaluate on the WITHOUT-SUA counterfactual?

This is the "hard part" the #9374 report flagged and did not resolve: the SUAS test asks
whether the household is "not already receiving the maximum allotment", but the allotment
depends on the deduction, which depends on the SUA.

The structural argument:

    snap_net_income   = max_(0, gross - deductions)          # floored at 0
    expected_contrib  = ceil(0.30 * net_income)              # therefore >= 0
    normal_allotment  = max_(min_allotment, max_allotment - expected_contrib)

so "at the maximum allotment" <=> expected_contrib == 0 <=> snap_net_income == 0. And the
utility allowance enters only through `snap_excess_shelter_expense_deduction` as

    uncapped_ded = max_(expense_share * housing_cost + utility_allowance - subtracted, 0)

which is monotone non-decreasing in the utility allowance, as is min_(uncapped, cap). So
deductions are non-decreasing in the SUA, net income is non-increasing, and "at max
allotment" can only go False -> True when the SUA is ADDED, never True -> False.

If that holds, evaluating the leg on the without-SUAS counterfactual is both non-circular
and the correct reading: a household already at max before the subsidy stays at max after.

Tested here by toggling `has_heating_cooling_expense`, which is what flips
`snap_utility_allowance_type` to SUA in a state that is not `always_standard` (TX).
No redtape imports - engine only.
"""

from itertools import product

from policyengine_us import Simulation

MONTH = "2025-08"
YEAR = "2025"


def run(*, heat, earned, housing, size, state="TX", elderly=False):
    ids = [f"p{i}" for i in range(size)]
    people = {}
    for i, pid in enumerate(ids):
        age = 70 if (i == 0 and elderly) else (35 if i == 0 else 8)
        people[pid] = {
            "age": {YEAR: age},
            "employment_income": {YEAR: earned if i == 0 else 0},
            "immigration_status": {YEAR: "CITIZEN"},
        }
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids,
                            "housing_cost": {YEAR: housing},
                            "has_heating_cooling_expense": {YEAR: heat}}},
        "households": {"h": {"members": ids, "state_name": {YEAR: state}}},
        "marital_units": {"m": {"members": [ids[0]]}},
    }
    sim = Simulation(situation=sit)
    g = lambda v: float(sim.calculate(v, MONTH)[0])  # noqa: E731
    net = g("snap_net_income")
    return {
        "ua": g("snap_utility_allowance"),
        "ded": g("snap_deductions"),
        "net": net,
        "contrib": g("snap_expected_contribution"),
        "max": g("snap_max_allotment"),
        "snap": g("snap"),
        "at_max": net == 0,
    }


EARNED = [0, 3_000, 9_000, 14_400, 21_000, 30_000]
HOUSING = [0, 6_000, 12_000, 24_000]
SIZES = [1, 2, 3]
ELDERLY = [False, True]

rows = []
violations_mono = []
violations_atmax = []

for earned, housing, size, eld in product(EARNED, HOUSING, SIZES, ELDERLY):
    off = run(heat=False, earned=earned, housing=housing, size=size, elderly=eld)
    on = run(heat=True, earned=earned, housing=housing, size=size, elderly=eld)
    rows.append((earned, housing, size, eld, off, on))

    # 1. Adding the SUA must never DECREASE the benefit.
    if on["snap"] < off["snap"] - 0.005:
        violations_mono.append((earned, housing, size, eld, off["snap"], on["snap"]))
    # 2. Adding the SUA must never move a household OUT of "at max allotment".
    if off["at_max"] and not on["at_max"]:
        violations_atmax.append((earned, housing, size, eld, off["net"], on["net"]))

W = 104
print("=" * W)
print("A. Does adding the SUA ever reduce the benefit, or move a household off max allotment?")
print(f"   {len(rows)} cells: earned x housing x size x elderly, TX, {MONTH}")
print("=" * W)
print(f"  benefit monotonicity violations (snap_on < snap_off): {len(violations_mono)}")
for v in violations_mono:
    print(f"    {v}")
print(f"  at-max violations (at_max True -> False):             {len(violations_atmax)}")
for v in violations_atmax:
    print(f"    {v}")

print()
print("=" * W)
print("B. The cells where the SUA actually bites, and what 'at max allotment' does")
print("=" * W)
print(f"  {'earn':>7}{'hous':>7}{'sz':>3}{'eld':>5} | "
      f"{'UA off':>7}{'UA on':>7} | {'net off':>8}{'net on':>8} | "
      f"{'snap off':>9}{'snap on':>9} | {'max':>6} | atmax off/on")
print("  " + "-" * (W - 2))
shown = 0
for earned, housing, size, eld, off, on in rows:
    if abs(on["snap"] - off["snap"]) < 0.005 and off["at_max"] == on["at_max"]:
        continue
    shown += 1
    print(f"  {earned:>7,}{housing:>7,}{size:>3}{str(eld):>5} | "
          f"{off['ua']:>7,.0f}{on['ua']:>7,.0f} | {off['net']:>8,.0f}{on['net']:>8,.0f} | "
          f"{off['snap']:>9,.0f}{on['snap']:>9,.0f} | {on['max']:>6,.0f} | "
          f"{str(off['at_max']):>5}/{str(on['at_max'])}")
print(f"  ({shown} cells where the SUA changes the benefit or the at-max flag)")

print()
print("=" * W)
print("C. Is 'at max allotment' equivalent to snap_net_income == 0?")
print("=" * W)
bad = [
    (earned, housing, size, eld, d["net"], d["contrib"], d["snap"], d["max"])
    for earned, housing, size, eld, off, on in rows
    for d in (off, on)
    if (d["net"] == 0) != (d["snap"] >= d["max"] - 0.005)
]
print(f"  cells where (net_income == 0) != (snap >= max_allotment): {len(bad)}")
for b in bad[:12]:
    print(f"    earn={b[0]:,} hous={b[1]:,} size={b[2]} eld={b[3]} "
          f"net={b[4]:,.0f} contrib={b[5]:,.0f} snap={b[6]:,.0f} max={b[7]:,.0f}")
if bad:
    print("  (mismatches are expected only where snap_min_allotment exceeds "
          "max - contribution, i.e. small household sizes)")

print()
print("=" * W)
print("VERDICT")
print("=" * W)
ok = not violations_mono and not violations_atmax
print(f"  SUA is monotone non-decreasing in the benefit:            {not violations_mono}")
print(f"  'at max allotment' never flips True -> False when SUA on: {not violations_atmax}")
print()
if ok:
    print("  => Evaluating the 'not already at the maximum allotment' leg against the")
    print("     allotment computed WITHOUT the SUAS allowance is non-circular AND safe:")
    print("     the SUAS can only move a household toward max allotment, so a household")
    print("     at max without it is at max with it. The counterfactual is the correct")
    print("     reading of the rule, not merely a convenient one.")
else:
    print("  => The counterfactual shortcut is NOT safe; see violations above.")
