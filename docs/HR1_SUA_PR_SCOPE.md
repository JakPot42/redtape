# PR scope: gate the HR 1 SUA change like the ABAWD change

**Status: SCOPE ONLY — not implemented.** Written 2026-09-16 in response to Max Ghenis on
[#9374](https://github.com/PolicyEngine/policyengine-us/issues/9374), who asked for a PR
"gated like the H.R. 1 ABAWD machinery (`is_snap_abawd_hr1_in_effect`)".

Target: `PolicyEngine/policyengine-us`, against `1.821.4`+.

---

## 1. The machinery being copied

Read from the installed 1.821.4. The ABAWD gate is three parts:

**A federal boolean parameter**, `gov/usda/snap/work_requirements/abawd/in_effect.yaml`:

```yaml
values:
  0000-01-01: false
  2025-07-04: true
metadata:
  unit: bool
  period: year
```

**Per-state override parameters** with the same leaf name, one per delaying state — CA
(`gov/states/ca/cdss/snap/work_requirements/abawd/hr1_in_effect.yaml`), HI
(`gov/states/hi/dhs/...`), AK (`gov/states/ak/dpa/...`). California's:

```yaml
values:
  0000-01-01: false
  2026-06-01: true
metadata:
  unit: bool
  period: month
  reference:
    - title: California All County Letter No. 25-93
```

**One variable that selects between them**,
`variables/gov/usda/snap/eligibility/work_requirements/is_snap_abawd_hr1_in_effect.py`:

```python
    def formula(person, period, parameters):
        # States that delay HR1 adoption have their own hr1_in_effect
        # parameter with a later effective date. Add new states here.
        federal = parameters(period).gov.usda.snap.work_requirements.abawd.in_effect
        state_code = person.household("state_code", period.this_year)
        ca = parameters(period).gov.states.ca.cdss.snap.work_requirements.abawd.hr1_in_effect
        hi = ...
        ak = ...
        return select(
            [state_code == StateCode.CA, state_code == StateCode.HI, state_code == StateCode.AK],
            [ca, hi, ak],
            default=federal,
        )
```

Consumed by `is_snap_abawd_exempt` and `has_snap_abawd_household_child`.

Three properties worth naming, because the SUA version should preserve all three: the
federal date is stated once and not repeated per state; a non-delaying state needs no
parameter file at all; and the gate is a **boolean in time**, not a rewrite of the
underlying rule — the old and new behaviour both stay in the code, selected by date.

Note the inconsistency to follow rather than fix: the federal parameter is `period: year`
and the state ones are `period: month`. The SUA parameters should be `period: month`
throughout, since the SUA is a monthly deduction and both California dates are mid-year.

## 2. What is actually wrong today

`gov/usda/snap/income/deductions/utility/always_standard.yaml` carries:

```yaml
CA:
  2015-10-01: true
```

with no later entry. Verified in 1.821.4. It is read by exactly one variable,
`snap_state_using_standard_utility_allowance` (`p.always_standard[state]`), which feeds the
first leg of `snap_utility_allowance_type`:

```python
        return select(
            [
                has_heating_cooling | always_sua,      # <-- this leg
                lua_is_defined & (distinct_utility_bills >= 2),
                distinct_utility_bills > 0,
            ],
            [SNAPUtilityAllowanceType.SUA, ..LUA, ..IUA],
            default=SNAPUtilityAllowanceType.NONE,
        )
```

So in California every household takes the SUA unconditionally, and the
elderly-or-disabled condition P.L. 119-21 §10103 added to the LIHEAP-triggered SUA is never
drawn. `has_snap_elderly_disabled_member` already exists and is already used by
`snap_excess_shelter_expense_deduction`, so the predicate needs no new machinery.

Magnitude, from the original report: the over-statement is bounded by 30% of the utility
allowance, roughly $194–$199/month, and is zero for households already at maximum
allotment.

## 3. Proposed structure

### 3a. Parameters

| path | period | values | reference |
|---|---|---|---|
| `gov/usda/snap/income/deductions/utility/hr1_in_effect.yaml` | month | `0000-01-01: false`, `2025-07-04: true` | P.L. 119-21 §10103; CBPP endnote 12 for the $20 federal threshold |
| `gov/states/ca/cdss/snap/income/deductions/utility/hr1_in_effect.yaml` | month | `0000-01-01: false`, **date TBD** | CDSS ACL 25-68 (+ ACIN I-46-25 if the date is kept) |

**The California date is the open question in the reply to Max and must not be encoded
before he answers.** ACL 25-68 keys the SUAS limitation to completion of automation, at
initial certification and next recertification, with a 120-day hold-harmless from
2025-07-04 — it names no date. Our 2025-10-31 came from ACIN I-46-25. Two shapes, and the
choice is his:

1. `2025-10-31: true`, citing ACIN I-46-25 for the date.
2. Structure around the hold-harmless: `2025-07-04` for the statutory change with the
   hold-harmless expiry as the operative California date, so the
   automation-completion dependency is visible in the parameter rather than resolved into a
   single day.

Do not invent a third option. If he declines both, the honest fallback is to ship only the
federal layer plus a CA parameter that stays `false`, with the open sourcing question in
the description — that is still an improvement, because today California cannot be
switched at all.

A **third parameter** is needed for the multi-state point, and it is not a boolean:

| path | type | note |
|---|---|---|
| `gov/usda/snap/income/deductions/utility/liheap_minimum.yaml` | USD by state, or federal scalar + state overrides | federal $20; CA's SUAS nominal payment is $20.01; IL requires $21 |

The original report established that these are each a state's implementation of the same
federal hook, so it must be parameterised, not hardcoded. **Scope call: leave this out of
the first PR.** It is a separate change with its own sourcing burden per state, and
bundling it repeats the mistake that produced #9374 — two claims in one issue, one of them
weaker. Land the gate first; note the threshold as follow-up.

### 3b. The gate variable

`variables/gov/usda/snap/income/deductions/utility/is_snap_sua_hr1_in_effect.py`, entity
`SPMUnit`, `definition_period = MONTH`, structurally parallel to
`is_snap_abawd_hr1_in_effect`:

```python
    def formula(spm_unit, period, parameters):
        # States that delay HR1 adoption have their own hr1_in_effect
        # parameter with a later effective date. Add new states here.
        federal = parameters(period).gov.usda.snap.income.deductions.utility.hr1_in_effect
        state_code = spm_unit.household("state_code", period)
        ca = parameters(period).gov.states.ca.cdss.snap.income.deductions.utility.hr1_in_effect
        return select([state_code == StateCode.CA], [ca], default=federal)
```

`SPMUnit` rather than `Person` because every consumer is an SPM unit; the ABAWD gate is
`Person` only because work requirements are personal.

### 3c. The variable that reads it

`snap_utility_allowance_type` — **not** `snap_state_using_standard_utility_allowance`.

Reasoning: `always_standard` is a genuine statement of standing state policy ("California
always uses the SUA"), and that remains true of the state's *option*. What HR 1 changed is
who may establish entitlement through the LIHEAP/nominal-payment route. Flipping
`always_standard[CA]` to `false` on a date would model the right outcome for the wrong
reason and would silently break any other future consumer of that parameter. So leave
`always_standard` alone and condition the leg that uses it:

```python
        hr1 = spm_unit("is_snap_sua_hr1_in_effect", period)
        elderly_disabled = spm_unit("has_snap_elderly_disabled_member", period)
        # P.L. 119-21 s.10103 limits the LIHEAP/nominal-payment route to the SUA to
        # households with an elderly or disabled member. An actual heating or cooling
        # expense still qualifies on its own.
        deemed_sua = always_sua & (~hr1 | elderly_disabled)
        return select(
            [has_heating_cooling | deemed_sua, ...],
            ...
        )
```

`has_heating_cooling` keeps its own leg untouched: a household with a real heating or
cooling cost qualifies regardless. That is the correct post-HR 1 reading — the restriction
is on automatic/deemed qualification, not on documented costs.

### 3d. The hard part Max did not mention: the "already at maximum allotment" leg

The SUAS three-part test (ACL 25-68) limits the nominal payment to households that are not
otherwise SUA-eligible, **are not already receiving the maximum allotment**, and contain a
member 60+ or disabled. The middle leg is circular on its face: the allotment depends on
the deduction, which depends on the SUA. The original report flagged this and explicitly
declined to propose a resolution. It should not be hand-waved in the PR, so here it is.

**It resolves, and this was checked against the engine rather than argued.** Two facts:

1. `snap_net_income = max_(0, gross - deductions)` is floored at zero, so
   `snap_expected_contribution = ceil(0.30 * net_income) >= 0`, and since
   `snap_normal_allotment = max_(min_allotment, max_allotment - contribution)`, "at the
   maximum allotment" is exactly `snap_net_income == 0`. Measured: **0 mismatches** between
   `net_income == 0` and `snap >= max_allotment` across all 288 engine configurations
   (144 cells of income × housing × size × elderly, each run with the SUA on and off).
2. The utility allowance enters only through
   `snap_excess_shelter_expense_deduction` as `max_(expense_share * housing + UA - subtracted, 0)`,
   then `min_(uncapped, cap)` — both monotone non-decreasing in the allowance. So
   deductions are non-decreasing in the SUA, net income is non-increasing, and the at-max
   flag can only go `False -> True` when the SUA is added, never `True -> False`. Measured
   by toggling `has_heating_cooling_expense` in TX over the same 144 cells: **0
   monotonicity violations and 0 at-max violations.**

Therefore evaluating the leg against the allotment computed **without** the SUAS allowance
is non-circular *and* correct rather than merely convenient: a household at maximum
allotment before the subsidy is still at maximum allotment after it, so the counterfactual
answers exactly the question the rule asks — is this household already at max before we
grant the payment.

Check script: `scripts/check_sua_monotone.py` in this repo; port it to a YAML test when the
PR lands (see §4). **This argument goes in a code comment, not just the PR description**,
and the PR should flag it for maintainer review as the one place the shape of the fix is a
judgement call — the monotonicity holds in 1.821.4 but is a property of the current
deduction formula, not a guarantee.

**Scope call: the full three-part SUAS test is NOT in the first PR.** The first PR
implements the §10103 elderly-or-disabled condition (3c), which is the divergence actually
demonstrated with numbers in #9374. The SUAS test's other two legs need the per-state
LIHEAP threshold parameter (3a) and a not-otherwise-SUA-eligible predicate, and the
at-max leg needs a counterfactual computation that should be reviewed on its own. Saying
so explicitly is better than a PR that half-implements a three-part test.

### 3e. The certification-date approximation

Both rules phase in at initial certification or next recertification, which the model has
no representation of. A month-boundary approximation is the right simplification, but the
PR must **document it as an approximation**, not present it as exact — in the parameter
`description` or a variable comment, not only in the PR description where it will be lost.
Same for the 120-day hold-harmless if shape (2) is chosen.

## 4. Tests

Upstream convention is YAML integration tests under `tests/policy/`. Mirror the ABAWD
tests' layout.

| test | asserts |
|---|---|
| `sua_hr1_federal_date` | non-delaying state, non-elderly, no heating/cooling, LIHEAP-only: SUA before 2025-07-04, not after |
| `sua_hr1_ca_delay` | same household in CA: SUA still granted through the CA date, withdrawn after |
| `sua_hr1_elderly_exempt` | CA, member 60+: SUA granted before *and* after both dates |
| `sua_hr1_disabled_exempt` | CA, disabled member: same |
| `sua_hr1_actual_expense_unaffected` | `has_heating_cooling_expense = true`: SUA granted after the date regardless of elderly/disabled — the restriction is on deemed qualification only |
| `sua_hr1_lua_iua_unaffected` | a household falling to LUA/IUA is routed identically before and after — the change must not perturb the other legs |
| `sua_hr1_amount` | the dollar consequence: deduction and final `snap` for one worked household, showing the ≤30%-of-allowance effect |
| `sua_hr1_always_standard_unchanged` | `snap_state_using_standard_utility_allowance` for CA is still `true` after the date — proves the fix did not flip `always_standard` |

Plus, if the at-max leg lands: a monotonicity regression test asserting the at-max flag
never goes `True -> False` when the utility allowance increases, so the argument in 3d
fails loudly if the deduction formula changes.

## 5. Redtape-side consequences

None of this changes redtape's answer keys until it is released and we bump the pin. When
that happens, `docs/LIMITS.md` §11 (the SUA scope limitation) and the restriction of
formula-validation cases to months before 2025-07-04 both become reviewable. Do not bump the
pin mid-phase.

An engine bump *is* one of the independent reasons to regenerate the splits, which
`docs/LIMITS.md` §31 otherwise defers — so the pin bump and the regeneration are one
decision, not two. `tests/test_corpus_drift.py` will already be failing on the generator
fingerprint if anything on our side changed in the meantime.

## 6. What not to do

- Do not flip `always_standard[CA]` to `false` on a date (see 3c).
- Do not bundle the LIHEAP threshold parameter, the other two SUAS legs, or anything about
  immigrant eligibility into this PR. #9374 already demonstrated the cost of pairing a
  strong claim with a weaker one.
- Do not encode the California date before Max answers.
- Do not present the certification-date month approximation as exact.
