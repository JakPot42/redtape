# PolicyEngine divergence report: HR 1 SUA changes not implemented for California

**Status: draft, not yet filed.** Prepared for review before any issue or pull request is
opened with the PolicyEngine maintainers.

**Package:** `policyengine-us==1.821.4` (with `policyengine-core==3.31.0`)
**Reported by:** the Redtape project, 2026-08-30
**Reproduction:** `scripts/probe_hr1.py` in this repository
**Severity:** moderate — over-states SNAP benefits for a specific, identifiable
population in a bounded month range; no crash, no obviously wrong output.

---

## Summary

Public Law 119-21 (HR 1) changed the basis on which California households qualify for the
SNAP Standard Utility Allowance, with two effective dates in 2025. `policyengine-us`
appears not to implement either. California is modelled as granting the SUA
unconditionally to every household, before and after both dates.

The result is that SNAP benefits are over-stated for **non-elderly, non-disabled
California households that qualify for the SUA only through the nominal-payment
mechanism rather than through an actual utility expense**, for months from **2025-07-04**
onward.

We are not certain this is unintended — it may be a known gap, or a deliberate decision
pending guidance. We are reporting it because it is not documented anywhere we could find,
and because a downstream user reasonably reading `policyengine-us` as ground truth would
not discover it.

## The published rules

**1. Heat and Eat termination — effective 2025-07-04.** California's Heat and Eat option
ends, *except* for households containing an elderly (60+) or disabled member.

**2. SUAS restriction — effective 2025-10-31.** The Standard Utility Allowance Subsidy
nominal payment ($20.01), the mechanism by which many California households establish SUA
entitlement without a separately billed utility cost, is limited to households that:

- are **not** otherwise SUA-eligible, **and**
- are **not** already receiving the maximum allotment for their household size, **and**
- **do** contain a member aged 60+ or disabled.

Applied at initial certification for new applicants and at recertification for ongoing
households.

**Governing sources:** Public Law 119-21 (HR 1); CDSS ACIN I-46-25 (FFY2026 COLA);
CDSS ACL 25-68.

## What the engine does

`gov.usda.snap.income.deductions.utility.always_standard` is `True` for `CA` at every
instant tested — **2025-05-01, 2025-07-05, 2025-10-01, 2025-11-01** — spanning both
effective dates.

Measured `snap_utility_allowance` for a California household with **no** heating or
cooling expense (`has_heating_cooling_expense = False`), under
`policyengine-us==1.821.4`:

| month | non-elderly, non-disabled | elderly (67) | disabled (45) |
|---|---|---|---|
| 2025-05 | 645.00 | 645.00 | 645.00 |
| 2025-06 | 645.00 | 645.00 | 645.00 |
| **2025-07** | 645.00 | 645.00 | 645.00 |
| 2025-08 | 645.00 | 645.00 | 645.00 |
| 2025-09 | 645.00 | 645.00 | 645.00 |
| **2025-10** | 663.00 | 663.00 | 663.00 |
| 2025-11 | 663.00 | 663.00 | 663.00 |
| 2025-12 | 663.00 | 663.00 | 663.00 |

The only movement across the year is the FFY2025 → FFY2026 COLA ($645 → $663), which is
correct. Neither HR 1 boundary produces any change, and no column differs from any other —
elderly and disabled households are treated identically to everyone else, which is
precisely the distinction both rules turn on.

`snap_utility_allowance_type` returns `SUA` in every month for a household that, under the
published rules, should not qualify for one after 2025-07-04.

## Supporting evidence that the mechanism is absent rather than mis-parameterised

- **No variable matching `suas`** exists anywhere in the model. The nominal-payment
  mechanism is not represented.
- **No California Heat-and-Eat or LIHEAP variable exists.** The only `ca_*` LIHEAP
  variables are `ca_riv_liheap_countable_income` and `ca_riv_liheap_eligible`, which are a
  Riverside County programme. Illinois and DC have LIHEAP variables; California does not.
- **HR 1 is partially implemented.** `is_snap_abawd_hr1_in_effect` exists, so the model
  does track HR 1 — for ABAWD work requirements, a different provision. This suggests the
  SUA provisions were not part of the same implementation pass rather than that HR 1 was
  overlooked entirely.
- **A structural side effect:** because `always_standard` is `True` for California, the
  Limited Utility Allowance is unreachable there. `snap_limited_utility_allowance_by_household_size`
  returns `0` for a California household, so the published CA LUA ($166 FFY2025, $170
  FFY2026) is not observable through the model.

## Affected population and months

Non-elderly, non-disabled California SNAP households that qualify for the SUA **only**
through the Heat-and-Eat / SUAS nominal payment rather than an actual separately billed
utility expense, in **2025-07 (from the 4th) through 2025-12** and continuing.

For those households the engine grants a $645 (FFY2025) or $663 (FFY2026) utility
allowance that the published rules withdraw. That inflates the excess shelter deduction,
lowers net income, and over-states the SNAP allotment. The size of the error is bounded
above by 30% of the utility allowance, so up to roughly **$194–$199 per month**, and is
zero for households already at the maximum allotment.

Elderly and disabled households are unaffected in outcome, since both rules preserve their
entitlement — though the engine reaches that result by not implementing the rule rather
than by implementing the exemption.

## Proposed fix

The cleanest shape consistent with the existing model:

1. **Make `always_standard` time-varying for CA** rather than a constant `True`. Set it
   `False` from `2025-07-04`, so the SUA is granted on the ordinary basis (an actual
   heating or cooling expense) rather than automatically.
2. **Add an elderly-or-disabled carve-out** so that households satisfying
   `has_snap_elderly_disabled_member` continue to receive the SUA on the Heat-and-Eat
   basis after 2025-07-04. The predicate already exists and is already used by
   `snap_excess_shelter_expense_deduction`, so this reuses tested machinery.
3. **For the 2025-10-31 SUAS restriction**, add a parameter capturing the three-part
   test (not otherwise SUA-eligible; not already at the maximum allotment; contains a
   member 60+ or disabled). The "already at the maximum allotment" leg requires care — it
   depends on the allotment, which depends on the deduction, which depends on the SUA —
   so it likely needs to be evaluated against the allotment computed *without* the SUAS
   allowance, to avoid a circular dependency. We flag this as the hard part of the fix and
   do not propose a specific resolution.
4. **Note the certification-date dependency.** Both rules phase in at initial
   certification or recertification, which the model has no representation of. A
   month-boundary approximation is probably the right simplification, but it should be
   documented as an approximation rather than presented as exact.

## Multi-state note

States are implementing HR 1 differently, so a California-only fix will not generalise.
For example, **Illinois** requires that a qualifying member receive **$21 or more** in
LIHEAP to establish the heating standard. Any fix should be parameterised per state rather
than hardcoded to the California rule.

## What we are not claiming

- We have not audited any state other than California.
- We have not verified whether the maintainers already know about this.
- We are not asserting the engine is wrong about anything else; every other SNAP figure we
  checked against published CDSS and FNS tables matched exactly (26/26 comparisons,
  including the FFY2025 and FFY2026 maximum allotment tables, standard deductions, shelter
  caps, homeless shelter deduction, and the California SUA for both fiscal years).

That last point is the reason we think this report is worth making: the engine is accurate
enough that a user has every reason to trust it, which is exactly what makes an
undocumented gap costly.
