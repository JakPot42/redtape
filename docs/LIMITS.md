# Limits and known hazards

What is deterministic, what is approximate, and what we do not claim. Written as we
go, not retrofitted at release. Every entry states how it was established.

Engine version for all findings below: `policyengine-us==1.821.4`,
`policyengine-core==3.31.0`, CPython 3.13.15.

---

## 1. Monthly-to-annual aggregation is per-variable, and one branch is a trap

**Status: characterized, resolved by a design rule.** This was the Phase 1 gating question.

`policyengine_core.simulations.simulation.Simulation._calculate` resolves a period
mismatch by branching on the variable's `quantity_type`:

```python
if variable.definition_period == MONTH and period.unit == YEAR:
    if variable.quantity_type == QuantityType.STOCK:
        contained_months = period.get_subperiods(MONTH)
        values = self._calculate(variable_name, contained_months[-1])   # LAST month only
    else:
        values = self.calculate_add(variable_name, period)              # sum of months
```

So an annual query on a **monthly `stock`** variable silently returns **December's value
alone** — not "any month", not "all months", not a count.

Confirmed empirically by supplying `is_snap_eligible` as a month-varying input and
querying annually:

| months eligible | annual query returns |
|---|---|
| Jan–Jun only | `False` |
| Jul–Dec only | `True` |
| March only | `False` |
| December only | `True` |
| never | `False` |

`is_snap_eligible` is `quantity_type=stock`, so it takes the December branch.
`snap` is `flow`, so its annual query sums all twelve months.

**Why this matters.** A task asking "was this household eligible for SNAP in 2025",
answered by `calculate("is_snap_eligible", 2025)`, would be wrong for every household
whose December differs from the rest of the year — silently, with no error, in a way
that looks entirely plausible.

**Design rule adopted:** *never query a monthly variable at an annual period.* SNAP is
always queried at an explicit month and always scored monthly. The oracle enforces this;
`tests/test_period_semantics.py` locks the behaviour so a version bump that changes it
fails the suite instead of quietly changing answer keys.

Relevant `quantity_type` values as of the pinned version:

| variable | period | type | quantity_type | annual query means |
|---|---|---|---|---|
| `snap` | month | float | flow | sum of 12 months |
| `is_snap_eligible` | month | bool | **stock** | **December only** |
| `snap_excess_shelter_expense_deduction` | month | float | flow | sum of 12 months |
| `is_medicaid_eligible` | year | bool | stock | natively annual, no aggregation |
| `medicaid` | year | float | flow | natively annual |
| `eitc`, `ctc` | year | float | flow | natively annual |
| `housing_cost` | year | float | flow | natively annual |

## 2. Overriding a mid-chain computed variable does not propagate

**Status: known trap, avoided by construction.**

Supplying `snap_earned_income` directly as a situation input changed the SNAP benefit
amount but left `is_snap_eligible` untouched — the eligibility path recomputes from
upstream person-level income rather than reading the override. A household given
$50,000/month via `snap_earned_income` still reported `is_snap_eligible=True`.

**Consequence:** the generator sets facts only at genuine *input* variables
(`employment_income`, `age`, `immigration_status`, `housing_cost`, `state_name`).
Never at intermediate computed variables. A household constructed by overriding a
mid-chain variable would carry an internally inconsistent answer key.

## 3. PolicyEngine has no representation of "unknown"

**Status: fundamental; drives the T1b design.**

The engine never raises on a missing fact. Every omitted input silently becomes a
default, and the model returns a confident, plausible, wrong answer:

| variable | default when omitted |
|---|---|
| `employment_income` | `0` |
| `age` | `40.0` |
| `state_name` | `California` |
| `is_disabled` | `False` |
| `immigration_status` | `CITIZEN` |
| `housing_cost` | `0` |

Omitting a fact and stating it as zero are indistinguishable to the engine. This is why
T1b abstention labels cannot come from the oracle and are instead produced by the
perturbation prober — see §4.

## 4. T1b determinability labels are approximate

**Status: approximate by construction. Labelled as such, not presented as exact.**

Abstention labels come from a perturbation sweep: vary the omitted fact across a
declared plausible range, recompute, and check whether the outcome flips. This is a
finite-sample **under-approximation**. It can prove a fact is deciding (a flip was
observed) but cannot prove one is not — it only sampled the range.

Two measured cautions:

- **Plateaus.** Shelter cost 0 → SNAP $93/mo; 12,000 → $306; 36,000 → $306. Past the
  deduction cap the fact stops mattering, so a sweep that only samples high values will
  wrongly conclude the fact is not deciding. Sweep ranges are declared per fact and
  recorded with the label.
- **Counterintuitive direction.** Omitting `immigration_status` defaults to CITIZEN
  → $93/mo, while `UNDOCUMENTED` → **$199/mo**: the parent is excluded from the unit
  and income is prorated, so the benefit goes *up*. Direction is never assumed; it is
  always computed.

The intended replacement is SMT-based determinability (see CLAUDE.md, deferred design
decisions), which answers the same question exactly.

## 5. Cross-platform determinism

**Status: verified, and re-checked in CI.**

Identical oracle output on Windows and WSL2/Linux for the same household: `snap` 969.0,
`eitc` 4328.0, `ctc` 2200.0, `household_net_income` 32658.21, `medicaid`
[11360.863, 7112.096]. Both on CPython 3.13.15.

This is only evidence about the engine because the interpreter is pinned. Without the
3.13 pin an identical result would be luck and a divergent one uninterpretable.

## 6. Scope of v0

- **One state.** California only. Chosen because it is the best-covered state in the
  engine (296 CA-specific variables; next is IL at 198).
- **Four programs.** SNAP, Medicaid, EITC, CTC.
- **Tax year 2025**, month varying. 2025 is complete and checkable against published
  tables; 2026 parameters may still be provisional.
- **No live portals, no real PII, no LLM-as-judge.** All households synthetic. All v0
  scoring is deterministic.

## 7. Oracle validation status

Three tracks, kept separate because they are evidence of different things. Re-run with
`scripts/external_validation.py`; locked as regression tests in
`tests/test_external_validation.py`.

### Track (a) - the engine's own shipped fixtures: PASSED, and it proves less than it looks

| fixture set | files | assertions | result |
|---|---|---|---|
| `tests/policy/baseline/gov/usda/snap` | 75 | **651** | all passed (319s) |
| `tests/policy/baseline/gov/states/ca` | 122 | **659** | all passed (136s) |

Establishes wiring correctness and drift detection. Does **not** establish external
validity: these are the library's own tests, so passing them shows we agree with
PolicyEngine about what PolicyEngine computes. Not citable as validation of correctness.

### Track (b) - published CalFresh tables: 22 / 22 exact matches

Reported by kind, because the three are not equally strong evidence.

| kind | n | result | what it actually tests |
|---|---|---|---|
| **FORMULA** | 9 | 9/9 match | calculation logic - full hand calculation from published tables and the published formula, household sizes 1-6 |
| **ALLOTMENT** | 8 | 8/8 match | parameter loading only - a zero-income household must receive the published max allotment, sizes 1-8 |
| **PARAMETER** | 5 | 5/5 match | engine parameter values read directly vs published figures |

**Do not read "22/22" as broad validation.** Only the 9 FORMULA cases exercise
calculation logic. The 8 ALLOTMENT cases confirm the engine loaded the right table and
would pass even if the deduction logic were wrong.

FORMULA cases (published vs oracle, all deltas 0.00): size 1 $900/$700 -> 292; size 2
$1,200/$900 -> 522; size 3 $1,500/$1,100 -> 682; size 4 $2,000/$1,400 -> 773; size 5
$2,400/$1,600 -> 871; size 6 $2,800/$1,800 -> 1,018; size 2 $0/$900 -> 536; size 2
$2,000/$1,200 -> 330; size 3 $800/$0 -> 765.

PARAMETER cases: SUA FFY2025 $645, **SUA FFY2026 $663**, earned income deduction 20%,
gross limit 1.3x FPL, net limit 1.0x FPL - all matching published figures.

**FORMULA cases are restricted to months before 2025-07-04.** From that date the
published HR 1 rules change SUA entitlement in ways the engine does not model (SS11), so
a later-month formula case would compare the engine against rules it never implemented.

**Sources**, all retrieved 2026-08-29 unless noted:

- **[A]** LSNC *Guide to CalFresh Benefits*, "Maximum CalFresh deductions",
  https://calfresh.guide/maximum-calfresh-deductions/ - FFY2025, eff. 10/01/2024-09/30/2025.
  Standard deduction 1-3 $204 / 4 $217 / 5 $254 / 6+ $291; earned income deduction 20%;
  SUA $645; LUA $166; telephone $19; max excess shelter $712; homeless shelter $190.30.
- **[D]** LSNC, maximum allotments as of 10/01/2024 - FFY2025 full table:
  292 / 536 / 768 / 975 / 1,158 / 1,390 / 1,536 / 1,756, +$220 per additional member.
- **[B]** Santa Clara County DEBS allotment/income chart - FFY2026, eff.
  10/01/2025-09/30/2026. Max allotment 298 / 546 / 785 / 994 / 1,183 / 1,421 / 1,571 /
  1,789. Gross limit (130% FPL) 1-4: 1,696 / 2,292 / 2,888 / 3,483. Net limit (100% FPL)
  1-4: 1,305 / 1,763 / 2,221 / 2,680.
- **[C]** SCC DEBS Update 24-07, CalFresh COLA FFY2025.
- **[E]** CDSS ACIN I-46-25, FFY2026 COLA, supplied by the reviewer 2026-08-29:
  SUA $663, LUA $170, resource limits $3,000 / $4,500, overall COLA 2.1%.

**LSNC (calfresh.guide) is the primary California source going forward.** It was
reachable and is well-cited; eCFR, USDA FNS and CBPP all blocked or timed out. Cornell LII
works for federal regulation text.

**Two effects controlled for**, either of which would have produced spurious discrepancies:

1. **The federal fiscal year boundary falls inside our tax year.** FFY2025 runs
   2024-10-01 to 2025-09-30; FFY2026 begins 2025-10-01. The engine switches correctly at
   October - a 1-person zero-income household is paid $292 in September and $298 in
   October. Comparing a November household against an FY2025 example is a **tax-year
   mismatch**, not an engine error.
2. **Modelled take-up.** See SS12 and CLAUDE.md. Not suppressing it produces a
   discrepancy whose cause is **modelling scope**, not an engine error.

**Still needed to extend FORMULA cases into FFY2026:** the FFY2026 **standard deduction
by household size** and **maximum excess shelter deduction**. LSNC still published only
FFY2025 figures when checked on 2026-08-29. Without them, FFY2026 coverage is limited to
allotment and SUA cells.

### Track (c) - Atlanta Fed Policy Rules Database: NOT an independent check

SPEC.md §2 names the PRD as a cross-check. **It cannot serve that purpose as written.**
PolicyEngine and the Atlanta Fed signed a memorandum of understanding under which
PolicyEngine validates its results against the PRD and the two parties collaborate on
resolving discrepancies
(https://www.policyengine.org/us/research/policyengine-atlanta-fed-mou-prd).

The PRD is a separately developed model — PolicyEngine does not import PRD rules — so
agreement is not purely circular. But agreement has been actively engineered by the
reconciliation process, and will become more so over time. Using the PRD as our
"independent second engine" would overstate the independence of the result.

**Recommendation:** either drop the PRD cross-check from the spec, or keep it while
stating plainly that it is a *partially* independent check whose independence decays as
the MOU reconciliation proceeds. It is not a substitute for published-table validation.

The PRD data was in any case not retrievable in this pass: atlantafed.org returned
HTTP 403.

### What may be claimed

**Validated, for these program-and-year cells only:**

- SNAP benefit amount, California, **FFY2025** (2024-10-01 - 2025-09-30), household sizes
  **1-6**, months **before 2025-07-04**, against published deduction and allotment tables
  and the published formula. This is the only claim backed by calculation-logic testing.
- SNAP maximum allotment, California, **FFY2026**, household sizes **1-8** - parameter
  loading only.
- SNAP structural parameters: standard deduction (FFY2025), SUA (FFY2025 and FFY2026),
  earned income deduction rate, gross/net income limit multipliers.

**Wired but NOT externally validated - everything else**, specifically:

- **Medicaid** - no external comparison of any kind. Every Medicaid output is unvalidated.
- **EITC and CTC** - no external comparison performed.
- All eligibility **booleans** (`is_snap_eligible`, `is_medicaid_eligible`) - only benefit
  *amounts* were checked.
- SNAP benefit *amounts* for **FFY2026** - blocked on the two missing published constants.
- SNAP for any household containing a member who is elderly or disabled in
  **2025-07 through 2025-12**, where the engine diverges from published HR 1 rules (SS11).

### Sources that could not be retrieved

Listed so they can be pulled manually and the figures pasted in:

| source | what was wanted | result |
|---|---|---|
| eCFR (ecfr.gov) title 7 §§273.2, 273.6 | regulation text | 302 redirect to a bot-block page |
| CBPP, "A Quick Guide to SNAP Eligibility and Benefits" | worked example | HTTP 403 |
| USDA FNS SNAP recipient eligibility page | allotment/limit tables | request timed out (twice) |
| CDSS ACIN I-46-25 (FFY2026 COLA), via basicneeds.ucmerced.edu | FFY2026 std deduction, SUA, shelter cap | HTTP 403 |
| Atlanta Fed Policy Rules Database | PRD dataset for cross-check | HTTP 403 |
| CRS R42505 (congress.gov PDF) | worked example | retrieved file contained only signature data |
| mchaccess.org FFY2025 COLA fact sheet (PDF) | FFY2025 allotment table | PDF text layer unreadable |

Cornell LII (law.cornell.edu) **was** reachable and supplied the regulation text used to
correct three citations in the rules table — see §10.

**Nothing was substituted from memory at any point.**

## 8. Float precision

PolicyEngine computes in float32, so amounts carry visible precision artifacts — a
household's EITC came back as `519.8599853515625` rather than `519.86`. This sits far
inside the ±$1 scoring tolerance and is not a correctness problem, but answer keys are
rounded for display and compared with a tolerance, never with `==`.

## 9. Determinability distribution is heavily skewed toward abstention

On the first ten generated households, withholding one fact produced:

- **indeterminate (abstention correct): 8 / 10**
- **incomplete-but-determinate (should answer anyway): 2 / 10**

Under the current sweep ranges, withholding almost any fact from a randomly generated
household makes at least one program indeterminate. The two class-3 cases both arose
because the household was already SNAP-ineligible on income, so shelter cost could not
matter.

**Consequence for task design:** class-3 cases are the ones that stop a model from
scoring well by always abstaining, and random withholding produces too few of them.
They will need deliberate construction — withhold a fact whose value is already
constrained by the rest of the household — rather than being sampled. The 25% T1b
fraction in SPEC.md §4 cannot be met with a representative class mix by random
withholding alone.

## 10. Three rules-table citations were wrong and have been corrected

Found while reading 7 CFR 273.2 and 273.6 for the SSN citation check. The
mandatory-verification list at **7 CFR 273.2(f)(1)** reads, per Cornell LII
(law.cornell.edu/cfr/text/7/273.2, retrieved 2026-08-29):

| subparagraph | subject |
|---|---|
| (i) | Gross nonexempt income |
| (ii) | Alien eligibility |
| (iii) | **Utility expenses** |
| (iv) | Medical expenses |
| (v) | **Social security numbers** — the duty to verify a reported SSN with SSA |
| (vi) | Residency |
| (vii) | Identity |
| (viii) | **Disability** |

and **7 CFR 273.6** is headed "Social security numbers", carrying the substantive
requirement that a household "provide the State agency with the social security number
(SSN) of each household member or apply for one before certification."

Corrections applied:

| rule | was | now | why |
|---|---|---|---|
| SNAP-UTIL-01 | 273.2(f)(1)(iv) | **273.2(f)(1)(iii)** | (iv) is medical expenses, not utilities |
| SNAP-DIS-01 | 273.2(f)(1)(v) | **273.2(f)(1)(viii)** | (v) is SSN verification, not disability |
| SNAP-SSN-01 | 273.2(f)(1)(v) | **273.6** | the substantive SSN requirement is at 273.6; (f)(1)(v) is only the duty to verify one already reported |

Only SNAP-SSN-01 was flagged as doubtful in advance. The other two were believed correct
and were not: reading the actual section text found them. That is the argument for
never accepting a citation that has not been read.

**No confidence level was changed.** These are corrections to wrong citations, not
promotions — the table remains high 0 / medium 6 / low 4. SNAP-SSN-01's rule text now
matches the language of its cited section and is a promotion candidate for the reviewer,
but only the reviewer promotes it.

## 11. HR 1 SUA changes are NOT modelled — named scope limitation

**Status: divergence confirmed and classified as "state rule change since publication",
not an engine bug.** Probe: `scripts/probe_hr1.py`.

Published rules (CDSS ACIN I-46-25 and HR 1, supplied by the reviewer):

1. **Effective 2025-07-04** — California's Heat and Eat option ends, **except** for
   households containing an elderly (60+) or disabled member.
2. **Effective 2025-10-31** — the SUAS nominal payment ($20.01), the mechanism that
   qualifies many California households for the Standard Utility Allowance, is limited to
   households that are *not* otherwise SUA-eligible, are *not* already receiving the
   maximum allotment for their size, and *do* contain a member aged 60+ or disabled.
   Applied at initial certification for new applicants, at recertification for ongoing
   households.

**What the engine does.** The California parameter
`gov.usda.snap.income.deductions.utility.always_standard` is **`True` at every instant
tested** — 2025-05-01, 2025-07-05, 2025-10-01, 2025-11-01. The engine grants every
California household the full SUA unconditionally: regardless of whether it has a
separately-billed heating or cooling expense, regardless of age or disability, and
identically on both sides of the two effective dates.

Measured `snap_utility_allowance` for a household with **no** heating/cooling expense:

| month | non-elderly, non-disabled | elderly 67 | disabled 45 |
|---|---|---|---|
| 2025-05 … 2025-09 | 645.00 | 645.00 | 645.00 |
| 2025-10 … 2025-12 | 663.00 | 663.00 | 663.00 |

The only movement is the FFY2025→FFY2026 COLA. Neither HR 1 boundary produces any change,
and `snap_utility_allowance_type` reports `SUA` in every month for a household that
should not qualify for one.

Supporting evidence that the mechanism is simply absent:

- **no variable matching `suas`** exists anywhere in the engine;
- **no California LIHEAP/Heat-and-Eat variable** exists (only `ca_riv_liheap_*`, a
  Riverside County programme, plus IL and DC LIHEAP);
- the only HR 1-aware variable in the whole model is `is_snap_abawd_hr1_in_effect`, which
  concerns ABAWD work requirements — a different provision. So the engine models *some*
  of HR 1 but not this part.

**Affected population and months.** Non-elderly, non-disabled California households that
qualify for the SUA only via the Heat-and-Eat / SUAS nominal payment rather than an actual
utility expense, in **2025-07 (from the 4th) through 2025-12**. For those households the
engine grants a $645/$663 utility allowance the published rules would withdraw, inflating
the excess shelter deduction and therefore the SNAP benefit.

**Consequences adopted:**

- FORMULA validation cases are restricted to months **before 2025-07-04**, where engine
  and published rules agree. A later-month formula case would be measuring the engine
  against rules it never implemented.
- This is a **scope limitation of v0**, disclosed here and in the README. It is not
  scored against the oracle and must not be described as an engine defect: PolicyEngine
  has not yet implemented a rule change that post-dates its California SUA modelling.
- **Do not generate T1b cases that turn on SUA entitlement in 2025-07 through 2025-12.**
  The answer key would encode pre-HR 1 policy.

## 12. Take-up suppression and its side effects

See CLAUDE.md for the decision and reasoning. Two consequences worth recording as limits:

**Scope limit.** v0 answers the question "what would this household receive, given only
the facts stated" — *not* "what does this household actually receive, given the other
benefits it is enrolled in". A real caseworker must account for actual cash-aid receipt;
v0 deliberately does not. Suppressed: `tanf`, `ca_tanf`, `ssi`, `ca_state_supplement`,
`social_security`, `unemployment_compensation`.

**Disability stops being decisive for SNAP.** SNAP's definition of a "disabled member"
requires receipt of a disability benefit, not a self-reported flag. With SSI take-up
suppressed, `is_disabled=True` no longer makes a household "elderly or disabled", so the
excess-shelter-cap exemption does not apply to it. Measured with
`scripts/probe_decisive.py`, 2-person household, $1,500/mo earned, $2,500/mo rent, 2025-04:

| p1 age | is_disabled | SNAP $/mo |
|---|---|---|
| 35 | False | 450.00 |
| 59 | False | 450.00 |
| **60** | False | **536.00** |
| 66 | False | 536.00 |
| 35 | **True** | 450.00 *(unchanged)* |

So **age 60 is a decisive fact for SNAP; `is_disabled` is not.** The prober agrees:
`p1.age` is labelled `indeterminate` with SNAP deciding (2 distinct outcomes at high
shelter, 3 at low shelter), `p1.is_disabled` is labelled `incomplete_determinate` with a
single outcome. The prober is not missing determinability here — the determinability
genuinely is not there, as a direct consequence of the take-up decision.

Do not build a T1b case around disability for SNAP in v0 without re-checking this.

