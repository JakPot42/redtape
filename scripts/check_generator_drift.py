"""Isolate the generator change: OLD status weights vs NEW status weights, same seed.

The previous check compared regenerated households against the committed NARRATIVES, which
is unreliable - T1b cases withhold facts, so a withheld age or housing cost is simply
absent from the prose and reads as a false mismatch.

This compares household OBJECTS generated under the pre-re-widening weights against the
current ones, which isolates the generator change from all narrative and withholding noise.

`_weighted()` consumes exactly one `rng.random()` call regardless of how many weights it
holds, so the RNG stream should not shift and the drift should be confined to
`immigration_status`.
"""

import redtape.generator.households as H

OLD_WEIGHTS = (
    (H.ImmigrationStatus.CITIZEN, 0.80),
    (H.ImmigrationStatus.LEGAL_PERMANENT_RESIDENT, 0.12),
    (H.ImmigrationStatus.CUBAN_HAITIAN_ENTRANT, 0.02),
    (H.ImmigrationStatus.UNDOCUMENTED, 0.06),
)
NEW_WEIGHTS = H._STATUS_WEIGHTS

SEED, N = 20260828, 1200

H._STATUS_WEIGHTS = OLD_WEIGHTS
old = {h.index: h for h in H.generate_many(SEED, N)}
H._STATUS_WEIGHTS = NEW_WEIGHTS
new = {h.index: h for h in H.generate_many(SEED, N)}

FIELDS = ("age", "employment_income", "is_disabled", "is_higher_ed_student")

diff_status = 0
diff_other = []
diff_shape = 0
diff_hh = []

for i in range(N):
    o, n = old[i], new[i]
    if len(o.people) != len(n.people):
        diff_shape += 1
        continue
    if (o.housing_cost, o.dependent_care_cost, o.month) != (
        n.housing_cost, n.dependent_care_cost, n.month
    ):
        diff_hh.append(i)
    for po, pn in zip(o.people, n.people):
        for f in FIELDS:
            if getattr(po, f) != getattr(pn, f):
                diff_other.append((i, po.person_id, f, getattr(po, f), getattr(pn, f)))
        if po.immigration_status != pn.immigration_status:
            diff_status += 1

W = 100
print("=" * W)
print(f"OLD weights vs NEW weights, seed {SEED}, {N} households")
print("=" * W)
print(f"  household-shape differences (person count):        {diff_shape}")
print(f"  household-level differences (housing/care/month):  {len(diff_hh)}")
print(f"  person-level NON-status differences:               {len(diff_other)}")
for d in diff_other[:10]:
    print(f"    {d}")
print(f"  person-level immigration_status differences:        {diff_status}")

total_people = sum(len(old[i].people) for i in range(N))
print(f"  (of {total_people} people total = "
      f"{100 * diff_status / total_people:.1f}%)")

print()
print("=" * W)
print("Status distribution, old vs new")
print("=" * W)
from collections import Counter  # noqa: E402

co = Counter(p.immigration_status.value for i in range(N) for p in old[i].people)
cn = Counter(p.immigration_status.value for i in range(N) for p in new[i].people)
print(f"  {'status':<28}{'OLD':>10}{'NEW':>10}")
print("  " + "-" * 48)
for s in sorted(set(co) | set(cn)):
    print(f"  {s:<28}{co.get(s, 0):>10}{cn.get(s, 0):>10}")

print()
print("=" * W)
print("VERDICT")
print("=" * W)
clean = not diff_shape and not diff_hh and not diff_other
print(f"  RNG stream unshifted (every non-status field identical): {clean}")
if clean:
    print("  => The drift is confined EXACTLY to immigration_status. Ages, incomes,")
    print("     disability, student status, housing, dependent care and benefit month are")
    print("     byte-identical between old and new generation for all "
          f"{N} households.")
    print(f"  => {diff_status} of {total_people} people ({100*diff_status/total_people:.1f}%) "
          "get a different status, so the committed")
    print("     corpus is NOT reproducible from its recorded (seed, index) under the")
    print("     current generator - the narrower thing it is, it is correctly labelled.")
else:
    print("  => The stream DID shift; drift is broader than immigration status.")
