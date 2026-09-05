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

**Direction of the resulting error, since the headline abstention figure rests on these
labels.** The sweep can only produce ONE kind of mistake: it can miss a fact that does decide
the outcome (it sampled a range where nothing flipped), labelling the case
*incomplete-determinate* when it is really *indeterminate*. It cannot invent a flip that did
not happen, because a label of *indeterminate* is only assigned after an observed change.

So mislabelled cases sit in the class where **answering is scored correct**, and the model
answers nearly all of them. Correcting such a label would move a task the model got "right"
into the class it almost always gets wrong. **The measured abstention accuracy is therefore
an over-estimate, and the true figure is lower.** The finding survives in direction — a
smaller number is a stronger version of this result, not a weaker one.

The size is unmeasured. **The planned interim, before the SMT upgrade, is a denser-sweep
subsample:** re-probe a random sample of incomplete-determinate cases at much finer
granularity and across wider ranges, and report the fraction whose label changes as an
estimate of the label error rate. Deliberately deferred, not dismissed.

## 5. Determinism and oracle drift

**Status: enforced by `tests/test_determinism.py` against a committed fixture, and run in
CI on every push (`.github/workflows/tests.yml`).**

`tests/data/determinism_reference.json` holds five households serialised in full, each
with the exact oracle output it produced and the engine, core and interpreter versions
that produced it. The test recomputes and compares **exactly** - no tolerance. The +/-$1
SNAP tolerance is a statement about what a *model* may get wrong; the engine reproducing
its own arithmetic gets no slack, so a cent of drift is a finding.

The generator is checked separately, against the same fixture. An engine change and a
generator change are different findings with different fixes, and storing each household
in full rather than as `(seed, index)` is what stops one masking the other.

### What this section used to claim, and why it changed

It previously read *"verified, and re-checked in CI"*, and cited `snap` 969.0, `eitc`
4328.0, `ctc` 2200.0, `household_net_income` 32658.21 as identical across Windows and
WSL2/Linux. **There was no CI and no test.** Those values lived only in prose here and in
CLAUDE.md, the household that produced them was never recorded, and the one determinism
test in the suite asserted `compute(hh) == compute(hh)` within a single process - which
returns a new value twice and passes after any version bump.

So the old numbers are **not reproducible and are not reproduced**. There is no way to ask
the engine the same question again, because the question was never written down. They also
included `household_net_income`, which `compute()` does not return. The fixture above is a
new reference over the oracle's actual output surface, first captured 2026-09-01.

The cross-*platform* half of the old claim is not re-established either, and will not be:
`verifiers.v1` cannot import on Windows at all (unguarded `import fcntl`), so Linux is the
only platform that can produce a shipping artifact. Determinism is now enforced across
*versions* on one platform, which is the axis that actually threatens an answer key -
`policyengine-us` ships three to four releases a day.

### What this is not

Expected values in the fixture are **engine output**. The test proves the engine still says
what it said; it says nothing about whether that is right. External validation is a
separate track under a hard rule that its expected values never come from the engine - see
`tests/test_parameter_drift.py` and `tests/test_external_validation.py`, and section 10.
A golden master cited as validation would be exactly the circularity this project exists to
avoid, so the two must not be blurred.

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

**Disability is decisive for SNAP — but only if the narrative declares the right thing.**

An earlier version of this section said the take-up decision had cost the disability axis.
That was wrong: the suppression was over-broad by one step, zeroing declared benefits as
well as imputed ones. It now suppresses imputation and permits declaration, and the axis
is back. Measured with `scripts/probe_decisive.py` — 2-person household, $1,500/mo earned,
$2,500/mo rent, CA, 2025-04 (FFY2025 shelter cap $712):

| declared | shelter deduction | SNAP $/mo | elderly-or-disabled? |
|---|---|---|---|
| nothing | 712.00 (capped) | 450.00 | no |
| `is_disabled=True` only | 712.00 (capped) | 450.00 | no |
| SSI **amount** $967/mo | 712.00 (capped) | 160.00 | no |
| **SSDI $1,200/mo** | 2,647.00 (uncapped) | **536.00** | yes |
| **disabled veteran** | 2,647.00 (uncapped) | **536.00** | yes |
| age 60 | 2,647.00 (uncapped) | 536.00 | yes (elderly) |

The subtlety that matters for narrative design: 7 CFR 271.2 keys on *receipt of a
qualifying benefit*, implemented as `is_usda_disabled` = OR over
`gov.usda.disabled_programs` = {`is_ssi_disabled`, `social_security_disability`,
`is_permanently_disabled_veteran`, `is_surviving_spouse_of_disabled_veteran`,
`is_surviving_child_of_disabled_veteran`}. **`ssi` is not in that list** — only
`is_ssi_disabled`, the determination, is. So a narrative stating an SSI dollar amount does
not establish disability status; one stating SSDI receipt or veteran disability does.

Both directions are usable T1b facts, and they move opposite ways: declared SSDI raises
the benefit (cap exemption), declared SSI lowers it (counted income).

## 13. California's Limited Utility Allowance is unobservable

Because `always_standard` is `True` for California, every CA household receives the
Standard Utility Allowance and the LUA is never reached;
`snap_limited_utility_allowance_by_household_size` returns 0. The published CA LUA
($166 FFY2025, $170 FFY2026) therefore **cannot be validated** against the engine. This is
a direct consequence of the gap in §11, and is asserted as such in
`tests/test_parameter_drift.py` so that we are told if California ever becomes conditional
upstream.

## 14. States implement HR 1 differently

A California-scoped fix will not generalise, and neither will a California-scoped
validation. For example, **Illinois** requires a qualifying member to receive **$21 or
more** in LIHEAP to establish the heating standard, where California used the $20.01 SUAS
nominal payment. Any future multi-state expansion must treat the HR 1 SUA rules as
per-state parameters, and must re-run the external validation per state — the FFY2026
figures validated here are the 48-state federal maxima plus California's own utility
allowances, and neither generalises to a state with its own options.

## 15. Parameter drift detection

`tests/test_parameter_drift.py` compares the engine's parameter values against externally
published figures and fails the build on divergence — see CLAUDE.md for the policy.
Currently 14 checks covering FFY2025 and FFY2026 allotments, standard deductions, shelter
caps, homeless shelter deduction, CA SUA, structural rates, the October fiscal-year
boundary, and the known HR 1 divergence.

**A gap this exposed:** the FFY2026 standard deduction is externally sourced for household
sizes **1-3 only** (FNS FY2026 COLA memo). The engine holds 223 / 261 / 299 for sizes
4 / 5 / 6+, and those are **not** asserted, because asserting the engine's own value
against itself proves nothing. FFY2026 FORMULA validation is limited to sizes 1-3 for the
same reason.

## 16. HR 1 immigrant eligibility restrictions are NOT modelled — corpus restricted

**Status: divergence confirmed. Affects ELIGIBILITY, not just amounts, and touched answer
keys already generated.** Probe: `scripts/probe_immigration.py`.

**Published rule** (CBPP, "A Quick Guide to SNAP Eligibility and Benefits", updated
2025-10-03, endnote 6, citing PL 119-21, enacted 2025-07-04): SNAP eligibility is
restricted to U.S. citizens; lawful permanent residents (after a five-year wait where
applicable); people granted Cuban or Haitian entrant status; and people living in the U.S.
under a Compact of Free Association.

**What the engine does.** Nothing changes at the 2025-07-04 boundary. SNAP benefit for a
2-person California household, $1,200/mo earned, by status and month:

| status | Jan | May | Jun | **Jul** | Aug | Oct | Dec |
|---|---|---|---|---|---|---|---|
| CITIZEN | 522 | 522 | 522 | 522 | 522 | 543 | 543 |
| LEGAL_PERMANENT_RESIDENT | 522 | 522 | 522 | 522 | 522 | 543 | 543 |
| **REFUGEE** | 522 | 522 | 522 | **522** | 522 | 543 | 543 |
| **ASYLEE** | 522 | 522 | 522 | **522** | 522 | 543 | 543 |
| **DEPORTATION_WITHHELD** | 522 | 522 | 522 | **522** | 522 | 543 | 543 |
| **CONDITIONAL_ENTRANT** | 522 | 522 | 522 | **522** | 522 | 543 | 543 |
| **PAROLED_ONE_YEAR** | 522 | 522 | 522 | **522** | 522 | 543 | 543 |
| CUBAN_HAITIAN_ENTRANT | 522 | 522 | 522 | 522 | 522 | 543 | 543 |
| UNDOCUMENTED / DACA / TPS | 292 | 292 | 292 | 292 | 292 | 298 | 298 |

The only movement anywhere is the FFY2026 COLA in October. Five statuses that HR 1 made
ineligible are still modelled as fully eligible, identical to a citizen.
`ca_snap_immigration_status_eligible` also reports `True` for all of them, so this is not
a CFAP substitution — it is federal SNAP eligibility.

**COFA is not representable at all.** The engine's `immigration_status` enum has 11 values
and none of them is a Compact of Free Association status, so one of the four categories
that *remain* eligible cannot be expressed.

**Action taken — the corpus is restricted, not merely annotated.**
`SAFE_IMMIGRATION_STATUSES` (in `redtape/schemas.py`) now limits generation and the
determinability sweep to statuses where the engine and the published rules agree:

- eligible under both: `CITIZEN`, `LEGAL_PERMANENT_RESIDENT`, `CUBAN_HAITIAN_ENTRANT`
- ineligible under both: `UNDOCUMENTED`, `DACA`, `TPS`

`REFUGEE` and `ASYLEE` were previously generated (3% and 2% of adults) and have been
**removed**. `tests/test_immigration_scope.py` fails if any generated household or sweep
value falls outside the safe set, and separately asserts the current known-wrong engine
behaviour so that an upstream fix notifies us to re-widen.

**Caveat within the safe set.** The five-year bar for LPRs is not modelled — the engine
has no date-of-entry input — so `LEGAL_PERMANENT_RESIDENT` is only correct for the
long-resident case. Narratives must not imply recent arrival.

## 17. The gross-income-test exemption produces no eligibility flips in California

**Status: modelled, but inert for outcomes here.** Probe: `scripts/probe_flip.py`.

CBPP endnote 4: households with a member aged 60+ or with a disability are not subject to
the gross income test. The engine **does** implement this —
`meets_snap_gross_income_test` flips `False` → `True` when SSDI or veteran status is
declared, or at age 60.

But it never changes `is_snap_eligible`. Sweeping a 3-person California household's
earnings, 2025-11:

| earned/mo | no declaration: eligible / gross test | SSDI declared: eligible / gross test |
|---|---|---|
| 3,200 | True / **False** | True / True |
| 3,500 | True / **False** | True / True |
| 4,000 | **False** / False | **False** / True |
| 5,000+ | False / False | False / True |

The exemption is squeezed out from both sides. Below the net-income threshold, California's
broad-based categorical eligibility (`meets_snap_categorical_eligibility`) already makes
the household eligible regardless of the gross test. Above it, the household fails the
**net** income test, from which elderly/disabled status grants no exemption.

**Consequence:** this route does not unlock eligibility-flipping T1b cases in California.
The scarcest and most valuable T1b class remains unavailable through it. A state without
broad-based categorical eligibility would behave differently — another reason the
single-state scope is load-bearing (§14).

## 18. Dependent care is now an exercised deduction channel

Previously untested. The generator now emits a dependent care cost for households with
children, `childcare_expenses` is wired through the oracle, `dependent_care_cost` is a
withholdable T1b fact with a declared sweep, and four validation cases exercise it —
including CBPP's published FY2026 worked example, reproduced end to end.

## 19. No automated PDF table extraction without a second source

See CLAUDE.md for the rule. Recorded here because it produced a near-miss: an automated
extraction of the FNS FY2026 allotments PDF returned a $688 shelter cap, a $193 standard
deduction and allotments 291/535/768/**1,023**/1,219/… — none of which match any other
source. The likely cause is that the document carries separate tables for the 48 states +
DC, Alaska, Hawaii, Guam and the USVI, and an extraction that does not respect column
boundaries splices values across jurisdictions, which is why $1,023 appeared where $994
belongs. Three independent sources (the reviewer's manual read of the COLA memo, the Santa
Clara County chart, and the engine's own parameters) agreed against it, so it was
discarded.

## 20. Medicaid is computed but NOT SCORED in v0

**Decision, not a defect.** `is_medicaid_eligible` remains in the T1 answer object with
full provenance, and `MedicaidAnswer.scored` is `False`. `SCORED_PROGRAMS` is
`("snap", "eitc", "ctc")`.

**Why.** SNAP is externally validated across two fiscal years and household sizes 1-6;
EITC and CTC are now validated against published IRS figures (§21). Medicaid MAGI
eligibility has **no external validation at all** - we found no reachable published
source giving MAGI eligibility outcomes for concrete households, and unlike the other
three the parameters are not published as a simple table that can be checked cell by
cell. Shipping a scored benchmark cell backed only by the engine agreeing with itself
would be exactly the circularity this project exists to avoid.

**What this costs.** T1 loses its only per-person eligibility output, which was the one
place the answer schema exercised person-level rather than unit-level reasoning. That is
a real reduction in what v0 measures and is stated as such in the README.

**To lift it:** a source giving MAGI eligibility determinations for specified households -
a state handbook worked example, or published MAGI conversion tables with enough detail
to reconstruct a determination.

## 21. EITC and CTC are externally validated

Sources, both retrieved 2026-08-30 and agreeing on every figure used:
**[H]** IRS.gov "Earned income and Earned Income Tax Credit (EITC) tables";
**[I]** Tax Foundation "2025 Tax Brackets and Federal Income Tax Rates"; each consistent
with Rev. Proc. 2024-40 as they summarise it. **[J]** PL 119-21 CTC provisions per IRS
Schedule 8812 guidance and Tax Foundation.

**EITC, TY2025** - 34 checks in `tests/test_eitc_ctc.py` spanning all three regions:

| children | published max | engine at plateau | published phaseout end | engine above it |
|---|---|---|---|---|
| 0 | $649 | $649 | $19,104 | $0 |
| 1 | $4,328 | $4,328 | $50,434 | $0 |
| 2 | $7,152 | $7,152 | $57,310 | $0 |
| 3+ | $8,046 | $8,046 | $61,555 | $0 |

Phase-in is monotonic increasing and never exceeds the maximum; phase-out is monotonic
decreasing between plateau and end. Zero earned income gives zero credit for every child
count.

**CTC, TY2025** - $2,200 per qualifying child (PL 119-21), phasing out above $200,000
single at $50 per $1,000. Engine matches exactly: $200,000 → $2,200; $210,000 → $1,700;
$230,000 → $700; $244,000 → $0. A child aged 16 qualifies, 18 does not, and 17 yields the
$500 other-dependent credit rather than the CTC.

### A semantic correction: `ctc` is not what the household receives

`ctc` is the **gross** credit before limitation. `ctc_value` is the amount actually
received once tax liability and the $1,700-per-child refundable cap are applied. For a
zero-income family with two children:

| variable | value |
|---|---|
| `ctc` | 4,400.00 |
| `non_refundable_ctc` | 4,400.00 |
| `refundable_ctc` | 0.00 |
| **`ctc_value`** | **0.00** |

The T1 answer previously reported `ctc` and so credited that family with $4,400 it does
not receive. **The scored answer now uses `ctc_value`**, with the gross entitlement kept
alongside it in `AnnualAmount.gross_entitlement`. This is the same class of error as
reporting `medicaid` (a dollar value) where `is_medicaid_eligible` (a boolean) was meant.

## 22. Eligibility-flipping T1b cases: student status works, ABAWD does not

Three routes to an `is_snap_eligible` flip were probed. Flips are the scarcest and most
valuable T1b class because they cannot be reached by adjusting an amount.

**Student status — WORKS.** 7 CFR 273.5: enrolment more than half-time in higher
education makes a person ineligible absent an exemption. Measured
(`scripts/probe_abawd_student.py`), single adult, CA, 2025-11:

| `is_snap_higher_ed_student` | `is_snap_ineligible_student` | `is_snap_eligible` | SNAP |
|---|---|---|---|
| False | False | **True** | 298.00 |
| True | True | **False** | 0.00 |

A clean binary flip that survives broad-based categorical eligibility, because student
ineligibility is a composition rule rather than an income test. `is_higher_ed_student` is
now a generated fact and is in the prober's fact space.

**ABAWD — does not flip, and the engine is right about that.** `is_snap_abawd_exempt` is
`True` and `is_subject_to_snap_abawd` is `False` for every age 25-64 in every month
tested. Two independent, *correctly modelled* reasons:

1. California is an ABAWD-waived area (`gov.usda.snap.work_requirements.abawd.waived_states`).
2. California delayed HR 1 ABAWD adoption. `is_snap_abawd_hr1_in_effect` reads
   `gov.states.ca.cdss.snap.work_requirements.abawd.hr1_in_effect`, and the variable
   **cites CDSS ACL 25-93** for it.

The engine also already implements HR 1's exemption changes — its source distinguishes
`pre_hr1_exempt` (including homeless, veteran and former foster youth) from
`post_hr1_exempt` (which drops them and adds the American Indian / Alaska Native
exemption), gated on `hr1_in_effect`. **So the removals the reviewer asked about are
implemented; they are simply not yet switched on for California.** This is not a
divergence, and it is worth recording as a case where the engine was better informed
than our assumption — which is also why the SUA and immigrant-eligibility gaps stand out
as specific omissions rather than general neglect.

**Gross income test — does not flip.** See §17.

## 23. Extreme-sweep audit: no third instance found

`tests/test_extreme_sweep.py` was applied retroactively to every variable the oracle
reads, across both ends of five input dimensions (employment income, housing cost,
dependent care cost, age, number of children).

**It found no new bug.** The only same-type divergences it surfaced were the three already
known and explained — `ctc_value` against `ctc`, `non_refundable_ctc` and `refundable_ctc`
— which are now registered in its `EXPLAINED` table with reasons. Every other flagged pair
was a boolean compared against a dollar amount (`is_snap_eligible` vs `snap`,
`is_medicaid_eligible` vs `medicaid`), which is a naming hazard rather than a value
disagreement and is asserted separately.

Reported as a negative result rather than quietly dropped: the rule is now mechanical and
will catch the next instance, but applying it retroactively did not reveal a third one.

**Its limits.** It compares only variables it can *name* as siblings, and only across
matching value types. A gross-versus-received pair under an unrelated name would still
pass. It narrows the class; it does not close it.


## 24. Pair-phase cost, and a retracted "discrepancy"

**Planning numbers.** Declaring `is_permanently_disabled_veteran` on p1 moves the scored
answer for roughly **22%** of pair candidates. Filling 200 pairs at a 50/50 differ ratio
consumes about **600 pair candidates** (1,200 oracle calls) and takes about **325 seconds**
on three workers, uncontended.

**A retraction, recorded because the mistake is more instructive than the number.** An
earlier session report claimed the differ rate was ~3% against a pre-build probe's ~26%,
called that an unexplained 8x gap, and recommended 3% for planning. **There is no gap. The
3% was an arithmetic error**, and both the "finding" and the cost figure derived from it
were wrong.

What went wrong: the candidate count was inferred from elapsed wall-time rather than read
from the `pair_candidates_discarded` counter the builder already emits. The wall-time was
itself inflated, because the dev build's pair phase (4,021s) ran while the full test suite
and other eval jobs were competing for the same four cores; the held-out build, run
uncontended, took 325s for identical work.

Three measurements of the same quantity, once computed correctly, agree:

| measurement | differ rate |
|---|---|
| `probe_pair_rate.py`, 150 households | 26% |
| direct comparison, 40 households per index range | 18-30% |
| the real build, chunks 1-2 before the bucket filled | 22.5% |

Two candidate explanations for the phantom gap were tested and eliminated before the
arithmetic error was found, and both results are worth keeping:

- **Tolerance is not a factor.** Comparing rounded values exactly and applying the metric's
  $1 tolerance produce *identical* differ counts in both index ranges — zero pairs differ
  by less than a dollar. The two comparison rules agree completely on this data.
- **`declared_statuses` replacement is not a factor.** `build_pair` replaces p1's
  `declared_statuses` rather than appending, which would perturb a second channel in any
  household that already declared a benefit. **Zero of 300** households in the pair index
  range declare anything on p1, so there is nothing to overwrite. The concern is void as
  the generator currently stands - but it is latent, and would become real if the generator
  ever starts declaring benefits on p1.

**The lesson, which is the reason this section exists.** The builder emits an exact
counter; the report used an estimate derived from a timer that was measuring something else
(CPU contention). That is this project's own recurring pathology - a plausible number
standing where the real one belongs, with nothing raising - applied to our own reporting
rather than to our code. Read the counter. See CLAUDE.md, "Every green signal must be
checked for what it is NOT measuring."

## 25. The prompt was never tested, and 242 tests could not have caught it

**Status: fixed 2026-09-02. Recorded because the failure mode is general and the fix is not
the interesting part.**

The first live model run against the dev split returned **0.000 on all three headline
metrics**. Nothing was wrong with the model.

`SYSTEM_PROMPT` said *"Answer with a single JSON object and nothing else"* and named **no
fields at all**. Claude Opus 5 produced correct, well-argued determinations — right SNAP
arithmetic, sensible abstentions, explicit reasoning about which facts were missing — using
the field names `monthly_benefit`, `annual_amount` and `period`. `T1Answer` requires
`benefit`, `amount` and `period_label`. All ten sampled responses were rejected as
`schema_invalid`, gate pass rate 0.0.

Had that run been scaled and reported, the published claim would have been that a frontier
model scores zero on public-benefits determinability. The actual finding would have been that
our prompt omitted the schema.

**Why the test suite was silent.** Every agent in `eval/` — five baselines, three scripted
tool conditions, `oracle_agent`, `perfect_agent` — builds a `T1Answer` **in Python** and
serialises it. None can emit a wrong shape. The suite covered
`T1Answer -> JSON -> parse -> score` exhaustively and covered
`SYSTEM_PROMPT -> model -> JSON -> parse -> score` **zero times**. The prompt was an
untested input to a scored pipeline.

`tests/test_env.py`'s parse tests are the sharpest illustration: they feed `parse_answer`
strings produced by `T1Answer.model_dump_json()`. That can only fail if the class disagrees
with itself. It cannot fail when the *prompt* disagrees with the class, which is the failure
that actually occurred.

**The general rule, which is the reason this section exists:** a test whose input is
constructed on the far side of the interface under test is not testing that interface. If the
fixture is built by the same code that consumes it, the fixture cannot be wrong in the way
real input can be — and every metric downstream reports confidently on a path nothing
traverses.

**Fix.** `SYSTEM_PROMPT` now renders the exact answer shape from `T1Answer` itself via
`_answer_shape()`, so a schema change updates the prompt rather than silently diverging from
it. Measured effect on the same ten tasks:

| | exact-match | gate pass rate | `schema_invalid` |
|---|---|---|---|
| before | 0.000 | 0.00 | 10 / 10 |
| after | 1.000 | 0.80 | 1 / 10 |

Cost per task fell from $0.0485 to $0.0338 at the same time, because the model stopped
spending output tokens deciding what shape to use.

**The coverage gap is now CLOSED** (2026-09-04). `tests/test_parse_real_replies.py` drives
verbatim Claude Opus 5 output through `parse_answer` — 40 valid replies, 25 abstentions
carrying nulls, empty output, markdown-fenced JSON, and the pre-fix field-name variants
(`monthly_benefit`, `annual_amount`, `period`-as-label) which must still be rejected. The
fixtures are real strings from the committed response cache, regenerated by
`scripts/build_reply_fixtures.py` rather than hand-written, so they stay real output rather
than what we imagine real output looks like.

Both fixes are teeth-verified: reverting the schema-in-prompt change fails
`test_the_prompt_still_states_every_required_field` and
`test_the_prompt_example_is_itself_a_valid_answer`; reverting the null-in-schema change
(§27) fails `test_real_abstentions_with_null_amounts_parse` and
`test_scorers_do_not_raise_on_a_null_answer`.

**Closing this gap immediately found a second defect of the same family — §27 — which had
already corrupted a published number.** That is the argument for the rule, not a coincidence
alongside it: the first real model output the suite had ever seen contained a failure mode
nothing had been able to express.

## 26. The abstention prompt was one-sided; we A/B'd it before publishing

**Status: confound identified, tested, and NOT supported. The abstention result stands, and
this section is part of why it should be believed.**

### The confound

The shipped `SYSTEM_PROMPT` describes abstention in three places — a populated
`cannot_determine` entry in the schema example, a field note, and a closing paragraph
instructing the model to list a program there "instead of guessing". So the headline
abstention figure is **not** a measure of whether the model knows the mechanism exists.

But that closing paragraph ends:

> "...a needless abstention is scored as wrong as a wrong number."

**The scoring is symmetric; that sentence is not.** Abstaining needlessly and failing to
abstain cost exactly the same, and the prompt names only the first. A model reading it has
been told what it loses by abstaining wrongly and nothing about what it loses by staying
silent. Claude Opus 5 scored **0.006 on the indeterminate class** and emitted
`cannot_determine` on 5.6% of the dev split; publishing that as calibration, with that
clause in the prompt, invites the obvious charge that the result was written into the
instructions.

We found this ourselves, before publication, and tested it.

### Design

60 tasks from the dev split, deterministically sampled and **weighted toward the classes
where abstention is the correct answer**: 24 indeterminate, 20 eligibility-flip, 10
incomplete-determinate, 6 determinate. The last two are retained so a rise in *needless*
abstention would be visible — a clause that merely made the model abstain more everywhere
would not be evidence of better calibration.

- **Arm A** — the shipped prompt, unchanged.
- **Arm B** — identical except the clause is **balanced, not deleted**: *"...a needless
  abstention is scored as wrong as a wrong number, and answering when a required fact is
  missing is scored as wrong as a needless abstention."*

Deleting the clause would have compared silence against a deterrent, which is a different
question: removal also strips the benchmark's genuine warning about needless abstention, so
any movement could be attributed to that loss alone. Balancing holds information content
constant and changes only symmetry, which is the variable under test.

Same model, same tasks, identical sampling parameters. The response cache keys on the
system prompt, so arm A was served from the existing dev-split cache at zero cost and arm B
correctly missed.

### Result — arm B barely moves

| | arm A (shipped) | arm B (balanced) | delta | Fisher exact (2-sided) |
|---|---|---|---|---|
| indeterminate | 0 / 24 = 0.000 | 0 / 24 = 0.000 | +0.000 | — (no events) |
| eligibility-flip | 2 / 20 = 0.100 | 4 / 20 = 0.200 | +0.100 | p = 0.661 |
| incomplete-determinate | 8 / 10 = 0.800 | 8 / 10 = 0.800 | +0.000 | — |
| **ALL T1b** | 10 / 54 = 0.185 | 12 / 54 = 0.222 | +0.037 | p = 0.812 |
| **replies containing any `cannot_determine`** | **12 / 60** | **12 / 60** | **+0.000** | p = 1.000 |

The cleanest behavioural measure — how often the model volunteers an abstention at all — is
**identical to the task**: 12 of 60 in both arms. Not similar, identical. The model abstained
on a slightly different *set* of tasks, not a larger number of them. Nothing reaches
significance, and the indeterminate class produced zero correct abstentions under either
prompt.

### Which claim this leaves us with

**Models fail to recognise that a required fact is missing, even when told plainly and
symmetrically what failing to flag it costs.** That is the stronger of the two available
claims and the one the data supports. The alternative — that models can recognise
indeterminacy but suppress it under mild discouragement — predicts a rise in arm B, and the
rise is absent at the level of raw behaviour.

### What this test does NOT establish

- **It is not powered to exclude a modest effect.** At a 12/60 base rate, 60 tasks per arm
  can only reliably detect roughly a doubling. A real but moderate effect — say 12 → 18 of
  60 — would not have been detected here. The claim is "no detectable effect at this power",
  not "no effect".
- **One prompt pair, one model.** A differently-worded balancing, a stronger instruction, or
  a few-shot example of a correct abstention could all move the number. This tests the
  specific asymmetry we shipped, not the general question of whether abstention is
  promptable.
- **It does not rule out the prompt suppressing abstention in some other way**, only that
  restoring symmetry to this clause does not change behaviour.

The honest summary: the one-sided clause is a real defect in the prompt and should be
balanced regardless of measured effect, but it is not the explanation for the 0.006.


## 27. The schema demanded a number from a model that had just abstained

**Status: fixed 2026-09-04. It had already corrupted a published number, which is why this
section exists rather than a line in a changelog.**

`SnapAnswer.benefit`, `SnapAnswer.eligible` and `AnnualAmount.amount` were required floats
and bools. The prompt tells the model that if a required fact is missing it should list the
program in `cannot_determine` **"instead of guessing"**. The schema then required it to
guess anyway.

Models resolved the contradiction the sensible way: they named the program in
`cannot_determine`, gave the missing fact, and set that program's value to `null`. Pydantic
rejected the whole reply as `schema_invalid`, the format gate failed, and the task was scored
as an abstention *failure*.

**On the first 1,200-task run, every one of the 47 `schema_invalid` responses was rejected
for exactly this.** Not most — all of them. The other two rejections were empty output.

### What it cost

| | as published | after the fix |
|---|---:|---:|
| abstention accuracy (T1b) | 0.357 | **0.438** |
| indeterminate class | 0.006 | **0.050** |
| eligibility-flip class | 0.125 | **0.396** |
| `p1.employment_income` | 0.236 | **0.709** |
| schema-invalid responses | 47 | **0** |

The eligibility-flip class was understated three-fold and `employment_income` three-fold.
The qualitative headline changed with it: "the model essentially never abstains" was wrong.
It abstains correctly on 40% of eligibility flips and 5% of amount-moving cases, and the
*unevenness* is the actual finding.

### The fix

A scored program's value may be `null` **only** when that program appears in
`cannot_determine`, enforced by a model validator on `T1Answer`. A null without a matching
abstention is still rejected, so this cannot be used to skip an answer silently.

The scorers exclude an abstained program from the amount and eligibility denominators rather
than counting it wrong — whether the abstention was correct is `score_abstention`'s job, and
scoring it twice would penalise a correct abstention in two places. `score_antihack` also
had to stop comparing `None < 0`, which raised a `TypeError` that the scorer guard converted
into a `scorer_error` — meaning a single correct abstention would have made an entire run
unpublishable.

### A different failure mode: the signal was present and read wrong

Every failure catalogued elsewhere in this file is an **absent** guard — a check that did not
exist, a path nothing traversed, a fixture that could not express the defect. This one is
not, and it is worth naming separately.

**The diagnostic was in every report from the first live run onward.** `schema_invalid: 10`
on the first 10-task probe. `47` on the full 1,200-task run, printed in the headline table,
committed to the results files, and quoted in the README as a caveat: *"47 of 1,200 responses
(3.9%) failed schema validation and are scored as incorrect, not excluded. Some are casing."*

The number was correct. The **interpretation** was wrong, and it was wrong in the same
direction for two days by both the author and the reviewer. When a 10-task probe showed one
residual failure, it was called *"a genuine model slip, not a harness bug"* — a reading that
sounded careful, cited real evidence, and was false. `schema_invalid` was treated throughout
as a **format-compliance cost borne by the model**, when it was a **correctness cost imposed
by us**. Nobody opened one of the rejected replies until the fixture work for §25 required
it. Every one of them was a correct abstention.

Three properties made it durable:

- **It was quantified, so it looked examined.** A number in a table reads as a thing someone
  looked at. "47 schema_invalid" was carried forward, re-quoted, and never re-derived.
- **The explanation offered was partially true.** Some replies *did* use `"EITC"` against a
  lowercase enum. A plausible partial cause is more effective at closing an investigation
  than no cause at all.
- **It pointed at the model, not at us.** A metric that attributes a fault outward invites
  much less scrutiny than one that attributes a fault inward. The rejected replies were
  filed as evidence of model sloppiness, which is exactly the shape of finding this project
  was set up to produce — so it fit the expected story and was not questioned.

**The rule this adds** to "every green signal must be checked for what it is not measuring":
a *non-zero* diagnostic is a signal too, and a plausible explanation for it is not the same
as a verified one. When a failure count is attributed to the system under test rather than to
the harness, that attribution is a claim requiring evidence — and the evidence is cheap:
**open one of the failures and read it.** Two days and a corrupted headline separated us from
a single `cat` of a rejected reply.

### Why it survived in the code

The same reason as §25, and found by fixing §25. Every test, baseline and scripted agent
constructed a `T1Answer` **in Python**, and Python code does not write `null` into a required
float — it cannot construct the invalid object at all. Only a real model, reading the
instruction to abstain and taking it seriously, produced this shape. The first time the suite
was pointed at genuine model output, it surfaced immediately.

**The lesson is narrower and sharper than "test your parser".** The schema encoded an
assumption — *every answer states a number for every program* — that the task's own
instructions contradicted. Nothing in a test suite built from that same schema could
represent the contradiction, because the schema was both the fixture generator and the thing
under test. A contradiction between a specification and its instructions is only visible to
something outside both.

## 28. Recorded cost figures understate actual spend

**Status: cause fixed 2026-09-04; the already-committed numbers are left as recorded.**

`eval/run_eval.py::run()` called `LEDGER.reset()` at its start. `prewarm()` does the actual
buying — it fetches every response concurrently into the cache — and `run()` then scores from
that cache. Resetting the ledger between the two discarded the entire purchase, so a
prewarmed run reported the cost of its *scoring* pass, which is always **zero** because by
then every response is a cache hit.

Consequence: **the `run.usage` block in every results file committed before 2026-09-04
understates spend, in several cases to `$0.00` for runs that cost real money.** The
per-response `usage` inside the cache entries was always correct — only the aggregate was
wrong — so nothing had to be re-fetched to establish the true figures.

**Use these measured per-task rates rather than any recorded total:**

| run | measured $/task | source |
|---|---:|---|
| `tool_less` (1,200-task dev split, Claude Opus 5) | 0.0480 | 1,200 cached responses |
| `tool_equipped` (n=300) | 0.0398 | 299 cached responses |
| `tool_equipped_unknowns` (n=15 probe) | 0.0591 | 15 cached responses |

Totals actually spent: about **$59.66** for the full dev-split run, about **$12.78** for the
partial tool conditions, and about **$70** across the project to date.

The figures are left in place rather than back-edited, because a results file is a record of
what a run produced and rewriting it later is how provenance is lost. This section is the
correction, and `redtape/eval/cache.py` recomputes cost from stored usage on demand, so any
figure can be re-derived from the cache without spending anything.

**The general point, which is the same one as §27:** a number that is present, printed, and
carried forward reads as a number somebody checked. `$0.00` on a run that cost $12 was on
screen for days. It was not questioned because it was in the "cost" field of a results file,
which is exactly where a cost is supposed to be.

## 29. The seed detector was tightened rather than annotated, and given a positive control

This is the first time in this project that §27's lesson was applied **before** the mistake
rather than after it, so it is worth separating from the sections that record failures.

Auditing the built wheel before publication, the secret scan fired on three 18-digit
literals — the shape of a held-out seed. All three turned out to be digit runs sitting
*inside* 64-character hex `task_hash` values. A SHA-256 has about 47 windows of 18
characters, each with roughly a (10/16)^18 chance of being all digits, so across 1,200 task
hashes a handful of them is not a surprise — it is the expected outcome.

There were two ways to close it:

1. Annotate the three as known false positives and move on.
2. Change the detector so it does not consider digit runs inside a hex digest at all.

We took the second. The reason is precisely §27: **a detector that is wrong most of the
times it fires is one people learn to wave through.** The `schema_invalid` count in §27 was
not hidden — it was printed in every report for two days — and it was dismissed because it
had a plausible-sounding explanation attached. An annotated exception list is a plausible
explanation stapled to a warning, which is the same failure with better manners.

The part that matters more than the tightening is the **positive control**. The audit now
asserts that the *public* dev seed IS present in the packaged data, and fails if it is not.
Without it, every way the audit could break silently — wrong artifact, empty file list, a
regex that matches nothing, a path that no longer exists — produces the same output as a
genuinely clean artifact: no findings. A scan that cannot distinguish "found nothing" from
"looked nowhere" is not evidence. **A silent audit now reads as untrustworthy rather than
clean**, which is the property the check was missing.

The general form, and it applies to any check in this repo: a test that can only fail is
half a test. It needs something it is required to find.

## 30. The wheel audit was auditing the wrong artifact for the publication question

Found while preparing the Environments Hub submission, and only because the packaging
artifact was inspected instead of assumed.

`scripts/audit_wheel.py` scans the built wheel, and it was clean: 22 entries, no secret
paths, no credentials, no held-out identifiers, positive control fired. That result was
true, and it was being used to answer a question it does not answer.

**`prime env push` does not upload the wheel.** It uploads a *source tree*, selected by
`prime_cli.commands.env._collect_archive_files` and filtered by a third-party
`gitignore_parser` reading of the root `.gitignore` — not by git itself, and not by the
`[tool.hatch.build]` configuration that decides what goes in the wheel. Two artifacts, two
different selection mechanisms, and the audit only covered one of them.

Running the CLI's own collector against the repo before pushing — importing the real
function rather than reimplementing its rules, so the answer cannot drift from what the CLI
actually does — the upload set was **1,685 files**, of which **1,584 were
`cache/responses/dev/**`.

Two findings, and they are different in kind:

- **Not a leak.** `.env` is excluded twice over (the root level ships only `README.md`,
  `pyproject.toml` and top-level `*.py`; and hidden files are skipped anywhere in the tree),
  `data/heldout/` is excluded by `.gitignore`, and the held-out response cache does not
  exist on disk at all. The hard requirement held.
- **But 1,584 files of cached model responses would have been published as the environment's
  source.** Not secret — that partition derives from the public dev seed and is committed
  deliberately, because it is paid for and losing it costs money (see the `.gitignore`
  comment). Publishing it is simply wrong for the artifact: it is our evaluation record, not
  the environment.

The CLI was behaving correctly and mirroring a deliberate repository decision. The fault was
in assuming a green result on artifact A transferred to artifact B. This is the same shape as
CLAUDE.md's standing principle — *every green signal must be checked for what it is NOT
measuring* — with a specific corollary worth keeping: **an audit is scoped to an artifact,
and publishing a different artifact voids it.**

The submission is therefore pushed from a staging tree containing only what an environment
should contain, and `scripts/preflight_push.py` audits *that*, using the CLI's own collector.
