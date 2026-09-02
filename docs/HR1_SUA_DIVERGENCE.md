# PolicyEngine divergence report: HR 1 SUA changes not implemented for California

**Status: draft, not yet filed.** Prepared for review before any issue or pull request is
opened with the PolicyEngine maintainers.

**Package:** `policyengine-us==1.821.4` (with `policyengine-core==3.31.1`)
**Also confirmed on:** `policyengine-core==3.31.0` — we pinned back to 3.31.0, captured a
five-household golden master of oracle output, moved to 3.31.1 and re-ran it. Output is
byte-identical across the bump, so nothing below depends on which of the two you use.
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

CBPP states the federal rule more cleanly than the state guidance does (endnote 12 of
"A Quick Guide to SNAP Eligibility and Benefits", updated 2025-10-03):

> Under prior law, households receiving more than **$20** in annual LIHEAP-type benefits
> automatically qualified for the heating and cooling Standard Utility Allowance. Under
> HR 1 they qualify automatically **only if they have an elderly or disabled member**;
> other households must document actual heating and cooling costs.

This confirms the federal threshold is **$20**. California's $20.01 SUAS payment and
Illinois's $21 LIHEAP requirement are each that state's own implementation of the same
federal hook, which is why a fix must be parameterised per state rather than hardcoded.

**Governing sources:** Public Law 119-21 (HR 1); CDSS ACIN I-46-25 (FFY2026 COLA);
CDSS ACL 25-68; CBPP "A Quick Guide to SNAP Eligibility and Benefits", updated
2025-10-03, endnote 12.

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
- **HR 1 is implemented carefully elsewhere in the same area of the model.** The ABAWD
  work-requirement provisions are not merely present but detailed: `is_snap_abawd_exempt`
  distinguishes a `pre_hr1_exempt` set (including the homeless, veteran and former foster
  youth exemptions) from a `post_hr1_exempt` set (which drops those three and adds the
  American Indian / Alaska Native exemption), gated on `is_snap_abawd_hr1_in_effect` —
  which in turn reads a California-specific parameter and **cites CDSS ACL 25-93** for
  California's delayed adoption. That is current, state-aware, well-sourced work on the
  same statute. It is why we read the SUA gap as a specific omission rather than general
  staleness.
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

## A second, related divergence

While probing this we found that the **HR 1 immigrant eligibility restrictions** also
appear unimplemented. PL 119-21 restricted SNAP to citizens, LPRs, Cuban/Haitian entrants
and COFA residents, but `policyengine-us==1.821.4` continues to model refugees, asylees,
people with deportation withheld, conditional entrants and one-year parolees as fully
eligible in every month of 2025 — identical to citizens, with no change at the 2025-07-04
boundary. The enum also has no COFA value, so one of the categories that remains eligible
cannot be expressed.

We mention it here because it suggests the two are part of the same gap: HR 1's SNAP
provisions appear to have been implemented for ABAWD work requirements
(`is_snap_abawd_hr1_in_effect`) but not for the SUA or immigrant-eligibility changes. If
the maintainers would prefer these as separate reports we are happy to split them.

## Two structural gaps, distinct from the parameter divergences above

These are **modelling gaps rather than wrong values**, so they need a different fix and
probably a different judgement about priority.

**1. COFA status is not representable.** The `immigration_status` enum has eleven values
and none expresses residence under a Compact of Free Association. COFA residents are one
of the four categories that *remain* eligible under HR 1, so even a correct implementation
of the restriction could not express an eligible COFA household. This needs a new enum
value before the immigrant-eligibility rule can be implemented completely.

**2. The LPR five-year bar is unmodelled.** HR 1 preserves eligibility for lawful
permanent residents "after a five-year waiting period where applicable", but the model has
no date-of-entry or date-of-status input, so `LEGAL_PERMANENT_RESIDENT` is treated as
eligible unconditionally. That is correct for long-resident LPRs and wrong for recent
ones, with no way to distinguish them. Fixing it requires a new input, not a parameter
change.

We raise both because they bound what a fix to the immigrant-eligibility rule can
achieve: the first category cannot be expressed at all, and the second cannot be
conditioned correctly, so an implementation would be partial by construction until the
inputs exist.

## What we are not claiming

- We have not audited any state other than California.
- We have not verified whether the maintainers already know about this.
- We are not asserting the engine is wrong about anything else. Every other figure we
  checked against published sources matched exactly:
  **22/22 SNAP comparisons** against CDSS, FNS and county-published tables, and
  **34/34 EITC and CTC checks** against IRS and Tax Foundation figures across the
  phase-in, plateau and phase-out regions for 0, 1, 2 and 3+ children.

  We report the SNAP figure by kind rather than as a bare total, because the three are
  not equally strong evidence:

  | kind | n | what it tests |
  |---|---|---|
  | FORMULA | 9 | calculation logic — full hand calculation from the published tables and formula, household sizes 1–6 |
  | ALLOTMENT | 8 | parameter loading — a zero-income household must receive the published maximum allotment, sizes 1–8 |
  | PARAMETER | 5 | engine parameter values read directly against published figures |

  Only the 9 FORMULA cases exercise calculation logic; the ALLOTMENT cases would pass
  even if the deduction logic were wrong. Among the FORMULA cases is CBPP's published
  FY2026 worked example, reproduced to the cent. FORMULA cases are restricted to months
  before 2025-07-04 — after that date they would be comparing the engine against the
  rules this report says it does not implement.
- Where we initially suspected a divergence and were wrong, the engine was right and we
  were not: ABAWD time limits do not bind in California for two correct reasons we had not
  accounted for, and the model cites the specific CDSS letter for one of them.

Those points are the reason we think this report is worth making. The engine is accurate,
current and well-sourced almost everywhere we looked, which is exactly what makes a
narrow, undocumented gap costly: a downstream user has every reason to trust it and no
signal telling them where not to.
