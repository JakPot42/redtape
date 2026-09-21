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
`tests/test_phase1.py::test_monthly_stock_variable_annual_query_returns_december` locks the
behaviour (this line previously cited `tests/test_period_semantics.py`, a file that never
existed; see §33) so a version bump that changes it
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

## 16. RETRACTED — HR 1 immigrant eligibility *is* modelled; we measured one state and generalised

**Status: our error, corrected 2026-09-15. There is no engine gap here.** The corpus
restriction this section used to justify has been removed. Probe:
`scripts/probe_immigration_state_scope.py`. The superseded probe,
`scripts/probe_immigration.py`, is kept with a banner because the mis-scoped measurement
is the artifact this failure mode is recorded against.

### What this section used to claim

That `policyengine-us==1.821.4` did not implement PL 119-21's immigrant restrictions at
all; that five statuses HR 1 made ineligible (`REFUGEE`, `ASYLEE`,
`DEPORTATION_WITHHELD`, `CONDITIONAL_ENTRANT`, `PAROLED_ONE_YEAR`) were still modelled as
fully eligible; and that `REFUGEE` and `ASYLEE` therefore had to be removed from corpus
generation. It was filed to PolicyEngine as part of issue #9374.

### What is actually true

Max Ghenis pointed this out on #9374 and we verified every part of it against the pinned
wheel before changing anything. The restriction is encoded in **two layers**:

| file | date layer | statuses |
|---|---|---|
| `parameters/gov/usda/snap/eligibility/eligible_immigration_statuses.yaml` | `2025-07-01` (comment: "Real effective date July 4th") | CITIZEN, LEGAL_PERMANENT_RESIDENT, CUBAN_HAITIAN_ENTRANT |
| `parameters/gov/states/ca/cdss/snap/eligibility/eligible_immigration_statuses.yaml` | `2026-04-01` | CITIZEN, LEGAL_PERMANENT_RESIDENT, CUBAN_HAITIAN_ENTRANT |

The federal file cites P.L. 119-21 §10108 and the FNS OBBB alien-eligibility page. The
California file cites **CDSS All County Letter 25-92**, which delays implementation to
2026-04-01. They are applied by `ca_snap_immigration_status_eligible`
(`defined_for = StateCode.CA`, reference ACL 25-92), which
`is_snap_immigration_status_eligible` combines as:

```python
return federal_eligible | ca_eligible
```

So `is_snap_immigration_status_eligible` by state, status and month — measured, not
inferred:

| status | CA 2025-06 | CA 2025-07 | CA 2026-03 | CA 2026-04 | TX 2025-06 | TX 2025-07 |
|---|---|---|---|---|---|---|
| CITIZEN / LPR / CUBAN_HAITIAN_ENTRANT | True | True | True | True | True | True |
| **REFUGEE** | True | **True** | True | **False** | True | **False** |
| **ASYLEE** | True | **True** | True | **False** | True | **False** |
| **DEPORTATION_WITHHELD** | True | **True** | True | **False** | True | **False** |
| **CONDITIONAL_ENTRANT** | True | **True** | True | **False** | True | **False** |
| **PAROLED_ONE_YEAR** | True | **True** | True | **False** | True | **False** |
| UNDOCUMENTED / DACA / TPS | False | False | False | False | False | False |

And the SNAP dollars, same household as the old table ($1,200/mo earned, 2-person):
a California `REFUGEE` goes 522 → 522 → 543 → **298** across those four months; a Texas
`REFUGEE` goes 522 → **292**. The old table's "nothing changes in July" was correct —
about California, and *only* about California.

### The failure mode: a correct measurement at the wrong scope

This is a **new** entry class for this file. It is not an absent guard (§27, §25) and not
a misread diagnostic (§27's second half). Every number in the old §16 table was right.
The probe was deterministic, reproducible and honest. What was wrong was the **scope it
was generalised to**.

`scripts/probe_immigration.py` hardcoded `state_name: CA` in its situation dict and swept
only months of 2025. Both choices were reasonable in isolation — the corpus is
California-only and 2025-only, so probing that scope is *exactly* right for validating
answer keys. The error was reporting the result as a **federal** omission when the
measurement could not distinguish "the engine does not implement this rule" from "the
engine implements this rule and this state has an override". Those two hypotheses are
observationally identical in California in 2025, and we never ran the one cell that
separates them.

**The same probe pointed at any non-delaying state would have shown the change at
2025-07 immediately.** One extra value in one dict.

**Generalised lesson — a one-cell probe cannot support a claim about the whole model.**
When probing a federal rule in a federalised program, vary the jurisdiction, or state the
finding at the scope actually measured ("California sees no change in July 2025", not
"the engine does not implement §10108"). Before filing any divergence upstream, check
whether a state-level override parameter exists for the thing you think is missing —
`parameters/gov/states/<st>/` mirrors the federal tree and is where a delay will live.
This generalises past immigration: §14 already records that states implement HR 1
differently, and we did not apply our own finding.

### Action taken — the restriction is removed, and the scope is now pinned

`SAFE_IMMIGRATION_STATUSES` (`redtape/schemas.py`) is **re-widened to all 11 engine
statuses**, because for the corpus scope every one of them carries a correct answer key.
`REFUGEE` (0.03) and `ASYLEE` (0.02) are restored to `_STATUS_WEIGHTS` at their original
pre-restriction weights; `CUBAN_HAITIAN_ENTRANT` (0.02), added while the corpus was
restricted, is retained. The determinability sweep widens with the set.

`UNSAFE_IMMIGRATION_STATUSES` is kept as a **deliberately empty mechanism**, not deleted,
so the disjoint/complete test still forces any new engine status to be classified.

**The committed splits were NOT regenerated.** They predate this re-widening and therefore
under-sample the immigration fact space; their labels remain correct. See §31 for the
measurement, the decision and the drift guard that now exists — and for the renderer defect
this change introduced.

The safe set is now **scope-conditional and says so in code**. `CORPUS_STATE = "CA"` and
`CORPUS_TAX_YEAR = 2025` are declared in `redtape/schemas.py`, and
`tests/test_immigration_scope.py::test_safe_set_is_justified_only_for_the_declared_scope`
fails if either moves while the HR 1-removed statuses are still in the safe set. That is
the guard the original failure lacked: the gate can no longer be silently generalised
past the cell it was derived from.

`tests/test_immigration_scope.py` now pins the **actual** behaviour — all five HR
1-removed statuses × four boundary months × both a delaying and a non-delaying state,
plus every month of 2025 in California, plus the retained and never-eligible statuses —
so any upstream change to either date layer fails a test rather than silently altering
answer keys. It also asserts the California eligibility we rely on is federal SNAP and
not a `ca_cfap` substitution.

### One more claim in the old §16 that was also wrong

- **"The engine has no date-of-entry input."** It has one: `years_since_us_entry`,
  `default_value = 5`, which its own comment documents as a PolicyEngine modelling choice
  rather than a statutory value. The five-year LPR bar is still not applied to SNAP, but
  for a different reason — `is_snap_immigration_status_eligible` never reads it (only
  WA RCA/TANF and Medicaid do). The practical caveat is unchanged: LPR answer keys are
  correct only for the long-resident case and narratives must not imply recent arrival.
  A test pins the non-dependency.

### What survives as a real gap: COFA

**COFA is still not representable.** The engine's `immigration_status` enum has 11 values
and none is a Compact of Free Association status, so one of the categories HR 1 leaves
*eligible* cannot be expressed as an input. The federal `2025-07-01` layer carries it as a
YAML comment only:

```yaml
    - CUBAN_HAITIAN_ENTRANT
    # Compacts of Free Association (COFA) citizens
```

This was **already known upstream** and is tracked as
**PolicyEngine/policyengine-us#8296**. It is not a redtape discovery and must not be
presented as one. It does not affect the corpus: the generator cannot emit a status that
has no enum value, so no answer key depends on it.

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
correction, and `eval/cache.py` recomputes cost from stored usage on demand, so any
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

## 31. The committed splits are stale relative to the generator, deliberately — and nothing detected it

**Status: known scope limit, accepted 2026-09-16. Labels remain correct.** The important
half of this entry is not the staleness; it is that **300 tests passed while the corpus and
the generator disagreed, because not one test read `data/dev/t1.jsonl`.**

### What happened

Re-widening `SAFE_IMMIGRATION_STATUSES` and restoring `REFUGEE`/`ASYLEE` to
`_STATUS_WEIGHTS` (§16) changed what the generator produces. The committed dev and held-out
splits were built under the narrowed configuration, so they instantly stopped being
reproducible from their own recorded `(seed, index)` provenance — the reproducibility
property CLAUDE.md states as a project invariant. The whole suite stayed green.

### Exactly how far the drift goes — measured, not assumed

`_weighted()` consumes exactly one `rng.random()` call regardless of how many weights it
holds, so re-weighting does not shift the RNG stream; it only changes which status a given
draw maps to. Verified by generating 1,200 households under the old and new weights and
diffing the objects field by field:

| difference | count |
|---|---|
| household shape (person count) | **0** |
| housing cost / dependent care / benefit month | **0** |
| person-level age, income, disability, student status | **0** |
| person-level `immigration_status` | **300 of 3,107 people (9.7%)** |

So the drift is confined **exactly** to `immigration_status`. Every other field is identical
across all 1,200 households. Status counts, old → new: `CITIZEN` 2468→2396,
`LEGAL_PERMANENT_RESIDENT` 401→340, `CUBAN_HAITIAN_ENTRANT` 53→71, `UNDOCUMENTED` 185→143,
`REFUGEE` 0→89, `ASYLEE` 0→68.

A first attempt at this measurement compared regenerated households against the committed
**narratives** and reported that the RNG stream had shifted — 60 of 120 cases apparently
diverging on age or housing. That was wrong: T1b cases *withhold* facts, so a withheld age
or housing cost is simply absent from the prose and read as a mismatch. **Compare the
objects, not the rendering.** The prediction was right; the first check of it was not.

### The decision: do not regenerate

**TRIGGERED 2026-09-19.** The independent reason this section waited for now exists: the answer keys rest on unstated and in places legally wrong premises (section 35, section 36). Regeneration goes ahead once the generator and oracle pass tests/test_unstated_premises.py.

Rebuilding costs ~92 minutes per split (the dev manifest records 5,534s) and would
invalidate every committed result file, including paid live Opus 5 runs. Against that, the
committed splits are **not wrong** — every task in them is correctly labelled, because
California genuinely kept refugees and asylees eligible through 2026-03 (ACL 25-92), which
is precisely the scope the corpus occupies. They are *narrower* than the current generator
would produce: they under-sample the immigration fact space, containing zero refugee and
zero asylee households where the generator would now yield roughly 3% and 2% of persons.

**So the limitation is under-coverage, not mislabelling**, and it is accepted rather than
fixed. Regenerate only when there is an independent reason to — an engine bump, a schema
change, or a finding that needs the wider sampling.

### The gap that mattered more, now closed

`redtape/generator/fingerprint.py` computes `generator_fingerprint()`, a sha256 over every
constant that determines corpus content (age ranges, all four bucket tables, status
weights, the safe-status set, `CORPUS_STATE`, `CORPUS_TAX_YEAR`). `build_split.py` writes it
into every manifest. `tests/test_corpus_drift.py` pins the live value
(`5af23f90feb737e1`), records the configuration the committed splits were built under
(`ae3b3a8d7a85b5e6`) as a documented staleness, and fails the build on any *undocumented*
divergence. It carries a **positive control** — a test that perturbs `_STATUS_WEIGHTS` and
asserts the fingerprint moves — because a fingerprint nothing can change would leave every
other test in the file passing forever (the lesson from §29). The fingerprint contains no
seed material, unlike `seed_fingerprint()`.

Also added: tests that read the committed split and assert its provenance fields, that its
staleness has the documented *shape* (no refugee/asylee, but `UNDOCUMENTED` and LPR still
present — a narrowing, not an emptying), and that its narratives and answer keys remain
mutually consistent.

### A second defect the re-widening introduced, found only by writing those tests

`narratives._STATUS_PHRASE` held prose for the original six statuses only. The lookup is a
strict `dict[...]` access, so the five restored statuses raised **`KeyError` on 12% of
generated households** — the split build would have crashed. All 300 tests passed anyway,
because the generator tests never called `render()`.

Fixed by adding prose for all five, and guarded by
`test_every_safe_status_has_narrative_prose` (keys ⊇ `SAFE_IMMIGRATION_STATUSES`),
`test_generated_households_actually_render` and
`test_refugee_and_asylee_appear_in_rendered_narratives`. **The lookup was deliberately left
strict.** A permissive `.get()` fallback would render a person with no status clause, and an
absent clause means *withheld* everywhere else in that renderer — so the silent fallback
would make a stated status indistinguishable from a withheld one, reproducing §25's
pathology for the third time. Crashing is the correct behaviour; the guard is a test, not a
default.

**Standing principle, instance seven** (CLAUDE.md table, row 7; this line originally said
"nine", a miscount corrected 2026-09-18): *every green signal must be checked for what it is
NOT measuring.* Here one change produced two undetected defects — a stale artifact and a
crashing renderer — and the suite reported 300 passed for both, because the tests exercised
the generator's outputs but never the published corpus and never the rendering path.


## 32. The budget cap existed only as prose (instance 8); cache hits were double-counted

**Status: both fixed 2026-09-18 (`eval/budget.py`, `eval/run_eval.py::_Ledger`). Committed
results files are left as recorded.**

**The cap.** CLAUDE.md has required since 2026-09-04 that every paid run take a cap "checked
after every API call". Nothing implemented it: `run_eval.py` had no cap of any kind. It is the
same failure as the determinism "CI" of §5, a documented control that did not exist.
`eval/budget.py` now reserves each request's worst case *before* sending (every input byte
counted as a token, plus `max_tokens` at the output price) and refuses any request that could
carry spent + in-flight past the cap. So the total is ≤ the cap under any concurrency,
provided the provider honours `max_tokens`. A request that raises is charged its full
reservation, not refunded. `--max-usd` is required whenever any request would bill, and there
is no default. A fully cached re-score needs neither a cap nor a credential.

A test that looked like it covered this did not. The threaded test passed with in-flight
reservations **ignored**, because each thread settled immediately and they never overlapped.
`test_in_flight_reservations_count_against_the_cap` holds two reservations open
deterministically and goes red under that mutation. All four mutations (Opus params drift,
double-count restored, cap removed, in-flight ignored) were run and observed failing.

**The double count.** `prewarm()` counted every response once, and the sequential scoring pass
re-read each one from the cache and counted it again. `results/t1_live300.live.tool_less.json`
records **600 cache hits for 300 tasks** (`usd_if_uncached` $29.03 against ~$14.40 actual). The
1,200-task Opus file is unaffected: it was scored without prewarm and records 1,200 hits,
$59.18. Fixed by counting hits only once `prewarm` has run. The CLI test
`test_cached_rescore_counts_each_hit_once_through_the_cli` asserts it.

**Provider abstraction.** `eval/providers.py` puts the model behind a registry key. Opus's
cache identity is byte-identical to the pre-refactor harness, proven two ways: all 1,200
committed Opus responses still hit (`test_opus_cache_keys_unchanged_for_every_committed_dev_task`;
replaced 2026-09-19 by `test_no_committed_response_is_served_for_the_changed_prompt` when the
prompt deliberately changed and every committed response became, correctly, a miss),
and `python -m eval.run_eval live --model claude-opus-5` with **no credential and no cap**
re-scores to 0.514 / 0.438 / 0.570, exactly the committed headlines.

## 33. Audit: controls asserted in CLAUDE.md and LIMITS that nothing verifies

**Status: audited 2026-09-18 after §32 (instance 8). Two citation errors fixed in place. All five control gaps are now CLOSED: items 1-4 on 2026-09-19, item 5 on 2026-09-18. The audit table is in CLAUDE.md under "A documented
control needs a test that fails when it is absent".**

The method: search both files for claims phrased as active guarantees ("enforces", "always",
"never", "fails the build", "in CI", "locks"). Check each against the code. Then run a script
over every test file, test function and source path named in CLAUDE.md, LIMITS.md, README.md
and the CI workflow, and confirm each exists. The script found three dangling references that
reading had passed over many times.

Dispositions:

1. **Dependency pinning.** CLAUDE.md says "pin every dependency exactly (`==`, never `>=`)".
   `pyproject.toml` declares `verifiers>=0.3.1`, `pydantic>=2.12` and `pyyaml>=6.0`, relaxed
   when the package became engine-free for the Hub. `uv.lock` pins CI, so CI results are
   reproducible. A Hub install does not use the lock, though, so it resolves whatever
   `verifiers` is current, and v1's API is the churn CLAUDE.md fences into `redtape/envs/`.
   **CLOSED 2026-09-19:** pinned exactly (verifiers==0.3.1, pydantic==2.12.3, pyyaml==6.0.3, pytest==8.4.2, ruff==0.9.0), `uv.lock` refreshed, and `tests/test_audit_controls.py` fails on any non-`==` specifier in any table of `pyproject.toml`. Teeth verified by loosening a pin.
2. **Cross-platform determinism.** The Platform section still says the check "is verified …
   re-run after any dependency bump". The Determinism section of the same file abandons it.
   Nothing can test it (`verifiers.v1` cannot import on Windows). **CLOSED 2026-09-19:** the claim is deleted from CLAUDE.md, replaced by a note saying why it cannot be re-established.
3. **Engine-free evaluation / oracle never called at rollout.** No test. CI installs the
   `generate` extra, so an accidental `policyengine_us` import on the load → prompt → parse →
   score path would pass CI and fail for a Hub user. **CLOSED 2026-09-19:** `tests/test_audit_controls.py` blocks every `policyengine` import with a `meta_path` hook, then runs load -> prompt -> parse -> score over 25 dev tasks. The engine IS installed here, so its absence would prove nothing; a positive control asserts the blocker really blocks.
4. **`verifiers` isolation.** True today (`grep` finds no import outside `redtape/envs/`);
   no test. **CLOSED 2026-09-19:** an AST scan over `redtape/` outside `redtape/envs/`, with a planted-import positive control. Teeth verified by planting an import in two real modules.
5. **Held-out cache and results never committed.** Protected only by `.gitignore` lines.
   No test runs `git check-ignore` on `cache/responses/heldout/…`, `data/heldout/…` or
   `results/…heldout….public.json`. **Highest-consequence gap in the audit**, and the
   cheapest to close. **CLOSED 2026-09-18** by `tests/test_gitignore.py`, which was red
   before the fix. It found a worse gap than the one reported: `results/*heldout*` matched one
   folder deep only, so a held-out `.public.json` in a subfolder was committable. The patterns
   are now `results/**/…`.
6. **Period lock citation (§1)**: pointed at `tests/test_period_semantics.py`, which never
   existed in any commit. The control is real, under another name. Citation fixed in place.
   Recorded because a reader checking the citation would have concluded the control was
   missing. That is the reverse error, and nearly the one made during this audit.
7. **"CI green means 202 of 207"** (CLAUDE.md) and the workflow comment saying
   `t1_smoke.jsonl` "never exists here": `t1_smoke.jsonl` is now committed. These are stale,
   in the conservative direction. Re-check against the next CI run's skip count before
   rewriting.
8. **§28 cited `redtape/eval/cache.py`**; the module is `eval/cache.py`. Fixed in place.
9. **§31 called itself "instance nine"** of the standing principle. It is row 7 of the
   CLAUDE.md table. Fixed in place.

## 34. A PREDICTED mechanism that was already in our data: reasoning tokens can exhaust `max_tokens`

**Status: the mechanism was predicted and instrumented 2026-09-18, before the first
non-Anthropic run. The claim first written here, "not yet observed", was WRONG. It had already
happened twice in the published Opus run, recorded as `no_json_found` parse failures and
scored as model failures (see the correction below). The method predicted the mechanism and
missed that it was already in our own data. That is a weaker claim than the one first made
here, and the weaker one is the true one.**

Every previous defect in this file was discovered after it had produced a wrong number. This
one was predicted from the structure of the harness before a single GPT request was sent.

**The mechanism.** OpenAI reasoning models draw reasoning tokens and visible output from the
**same** `max_tokens` budget. At "high" effort, the reasoning can consume the whole budget,
and the response then ends with `finish_reason: "length"` and no JSON at all. OpenRouter's
documentation says so directly ("Reasoning tokens count against `max_tokens`"). Scored
naively, that response parses as `no_json_found`: a *model failure*, counted against the
model's exact-match and abstention scores. What actually happened is that the harness gave the
model too small a budget.

**Why it is the same defect as §25/§27.** The schema bug that nearly published 0.006 had
exactly this shape. The harness imposed a constraint the model could not see or satisfy (field
names it was never told; a number demanded after abstaining), and the scorer converted the
harness's failure into the model's score. A token budget spent on reasoning is one more
constraint of that kind, and a comparison between two labs would have read it as a capability gap.

**What was done before the first run:**
- `eval/providers.py` normalises every provider's stop reason and keeps the raw value.
  `length` is a distinct outcome, never folded into an empty answer.
- Every cache entry records `stop`, `raw_stop`, `served_by` and the provider-reported cost.
  The results file's `usage` block tallies `stop_reasons`.
- `scripts/model_report.py` prints stop reasons and parse failures **before** any accuracy,
  so a run in which truncations dominate is visibly degenerate on the first screen.
- `test_token_limit_is_recorded_as_length_not_as_an_empty_answer` feeds a raw
  `finish_reason: "length"` response through the real OpenAI SDK.

**What was deliberately NOT done:** raising `max_tokens` pre-emptively. The Opus run used
8,000, and changing it for one model would make the comparison unequal. The probe measures
whether truncation happens. If it does, the budget is a decision to take on evidence, and any
change applies to both models.

**Why it is recorded separately.** The standing principle has only ever been applied after
the fact: a green signal fails, then we ask what it was not measuring. Here the question was
asked of a signal that did not exist yet. Before the first run it was asked in this form:
"what would still score as a model failure if the harness were the thing broken?". The
answer was on the page of provider documentation already being read for other reasons.

### §34 correction, 2026-09-18 (after the probe): the trap had ALREADY fired, in the Opus run

"Not yet observed" above was wrong. The committed Opus 5 run has **2 of 1,200 responses at
exactly 8,000 output tokens**, both on determinate tasks, both scored `no_json_found`, and
both counted against exact-match. That's a harness budget recorded as model failure, the
exact mechanism described above, sitting in a published run. The effect is 2/780 on
exact-match, and no headline moves at three decimals beyond ±0.003. The README's
"0 malformed-JSON, 0 schema-invalid" is true and omits the third parse-failure class, which
is where these two were. So the prediction was right about the mechanism, and the evidence
for it was already in our own data, unread. Output-token distribution for Opus (cached
usage): mean 1,798, median 1,599, p90 3,285, p99 5,499, max 8,000.

GPT-5.6 Sol probe (10 tasks, the same stratified sample): mean **3,987** output tokens,
reasoning 92–97% of each; max **7,379**, on a determinate task. No truncation in 10, but the
distribution sits far closer to the ceiling than Opus's.

### §33 addendum: the abstention scorer never reads `missing_fact`

CLAUDE.md (T1b section) says a correct abstention "must name the affected program and the
missing fact". `score_abstention` compares programs only; `missing_fact` is never read. This
is the rule of §33 violated in the scorer itself. Measured impact on the published Opus run,
with a keyword heuristic over free-text fact names (models invent names such as
`p1.college_enrollment`): **46 of 47 credited abstentions named the withheld fact.** So the
category/quantity split does not rest on the leniency. It is still an absent control. In the
GPT probe, the one credited abstention named an invented immigration waiting-period fact when
student status was withheld. **Decision needed:** implement fact matching (it needs a mapping
from free text to canonical facts, which is itself a judgement), or rewrite the CLAUDE.md
sentence to say what is actually scored.

### Reconciled: the README's "6.3% volunteer cannot_determine" was irreproducible; it is 5.3%

The 6.3% entered the README in `571d308` (the schema-fix correction), with no script behind
it. Every candidate definition was computed over the 1,200 committed Opus replies:

| definition | count |
|---|---:|
| parsed by `parse_answer`, non-empty `cannot_determine`, any program | **64** |
| … restricted to scored programs | 64 |
| raw text contains a non-empty `cannot_determine` list | 64 |
| raw text mentions the key at all | 65 |
| non-empty, on T1b tasks only | 54 |
| total `cannot_determine` entries (not responses) | 142 |
| non-empty but unparseable | 0 |

No definition gives ~76. **The README now says 5.3% (64 of 1,200)**, and this count comes from
`scripts/model_report.py`, which reads the cached replies through `parse_answer`. The likely
origin is a hand count during the schema-fix session, but that cannot be shown now. The
lesson is the §28 one: a number carried forward with no generating script cannot be
re-derived, so it cannot be checked.

## 35. The answer key assumes household relationships the narrative never states

**Status: FOUND 2026-09-18 while implementing fact-matching for the abstention scorer. OPEN,
and it blocks the GPT-5.6 Sol full run: fixing it changes the prompts, so every model would
have to be re-run.**

**What the oracle assumes.** `policyengine_oracle.py` puts every household member into ONE
tax unit and gives each adult a separate marital unit ("v0 does not model married couples").
So the answer key silently assumes that nobody is married, the whole household files as one
tax unit, and every child is a qualifying child of the filer. **None of this is in the
narrative.** A case file lists `Person p1 is 40 … Person p2 is 11 … Person p3 is 9` and never
says who is whose child, whether two adults are partners, or who files with whom. EITC and CTC
turn on exactly these facts. Nothing in LIMITS, SPEC or the README recorded the assumption
before this entry.

**Exposure in the dev split:** 393 of 1,200 tasks (33%) have two or more adults with no
stated relationship; 292 of those also contain a child. Opus 5 exact-match on determinate
tasks is **0.447 on 2+-adult households vs 0.550 on single-adult**. That is consistent with
the hidden assumption costing it, and does not establish it (household size confounds).

**How it surfaced.** Of Opus's 142 `cannot_determine` entries, 32 name something other than
the withheld fact. Read as "confabulated abstentions", they are mostly the reverse: the model
asking for facts the answer key *assumed* without stating. There are ~14 relationship or
filing-status requests (`p2.relationship_to_p1`, "which filer can claim p3"), several SNAP
student work-hours requests (the 20-hour exemption; hours are never stated), and LPR
entry-date requests (the five-year bar; entry date is never stated). On determinate tasks
those are scored as needless abstentions. **The benchmark penalised the model for noticing
the one kind of missing fact the generator never withholds on purpose.**

**Why nothing caught it.** The perturbation prober sweeps the six facts the generator
*withholds*. It cannot see a fact the generator never states in the first place, because to
the prober that fact is simply an engine default. This is PolicyEngine's "every missing input
becomes a plausible default" (CLAUDE.md, Other engine facts), one level up: our narratives
inherit the engine's defaults as unstated premises. It is also the take-up problem again (a
hidden assumption underneath the answer key), and it has the same fix: state it or remove it.

**Consequences:**
- "Confabulated abstention" cannot be measured until this is fixed. A named fact that was not
  withheld is either confabulated or a real unstated premise, and today the two cannot be
  told apart.
- The fact-matching fix to `score_abstention` cannot be implemented fairly on the current
  prompt. The prompt shows ONE example identifier (`p1.employment_income`) and no vocabulary,
  so exact matching would score guessing (GPT wrote `p1.college_enrollment` for
  `p1.is_higher_ed_student`). Also, `p1.employment_income` is the prompt's example AND the
  fact Opus flags most (0.709). The README's "income has a slot" explanation has an equally
  good competitor: the prompt names it.

**Candidate fix (decision needed):** narratives state household relationships and filing
structure explicitly, consistent with what the oracle builds, or the oracle builds what the
narrative states. The prompt enumerates the fact identifiers `missing_fact` must use (a closed
vocabulary, rendered from code like `_answer_shape()`, so it cannot drift). The scorer then
matches identifiers exactly. That changes every prompt, so Opus 5 must be re-run too, and a
prompt-only change of this kind is what the A/B machinery (§26) exists to cost.

## 36. Answer keys rest on engine defaults no case file states, and some are wrong in law

**Status: FOUND 2026-09-19 while implementing §35's fix. OPEN. All model results withdrawn from
the README headline. Regeneration is triggered (§31's "independent reason" now exists).
Acceptance test: `tests/test_unstated_premises.py`, xfail(strict=True) until the corpus
passes it.**

§35 framed the problem as unstated *relationships*. Tracing what the engine actually reads
shows it is larger, and in three places the answer key is not merely unstated but
**wrong**.

### Method: ask the engine what it read

For each representative household, the oracle's own situation is built and the scored
variables (`is_snap_eligible`, `snap`, `eitc`, `ctc_value`) are computed with
policyengine-core's tracer on. Every **input** variable (no formula) the computation touched,
minus those the oracle set, was read at the engine's default. **About 285 distinct defaulted
inputs per household.** Most are none-like (no alimony, no capital gains, no energy-credit
spending), which one explicit closure sentence can state honestly. The ones that cannot:

| default the engine read | what it means | measured effect (CA, 2025, engine output) |
|---|---|---|
| second adult in the single tax unit | **PolicyEngine makes them `is_tax_unit_spouse`** | two adults + child: EITC $1,207 as joint filers vs **$3,265** filing separately. A 45-year-old and a 20-year-old were keyed as **spouses** |
| `weekly_hours_worked_before_lsr = 0` | everyone works zero hours, including people with stated earnings | student earning $18,000: SNAP **ineligible** at 0 hrs, **eligible, $206/mo** at 25 hrs/week |
| `ssn_card_type = CITIZEN` | everyone holds a citizen's SSN card, **including undocumented people** | undocumented parent, $22,000: keyed EITC **$4,328** and CTC **$1,700**; with no SSN, both $0 |
| `is_related_to_head_or_spouse = True` | every member is family | no effect in the case measured; unstated all the same |
| `is_full_time_college_student = False` | no one is a full-time student | narratives say "enrolled **full-time** at a community college", so this contradicts rather than omits |
| `takes_up_eitc`, `takes_up_snap_if_eligible`, `would_file_if_eligible_for_refundable_credit = True` | the household claims and files | reasonable, and unstated |
| `has_heating_cooling_expense = True` | **set by the oracle** for every household | never narrated |

The comment in `build_situation` reads "v0 does not model married couples". The engine does:
with one tax unit, the second adult becomes the spouse. **The code's stated assumption was
the opposite of what the engine did.** Nothing checked it, because nothing asked the engine
which role each person had.

### Exposure in the committed dev split (from the stored answer keys)

| defect | tasks |
|---|---:|
| 2+ adults, keyed as joint filers | 393 |
| … adults 18+ years apart (likely parent and adult child keyed as spouses) | 163 |
| a stated student with earnings, keyed at 0 hours | 81 |
| an undocumented person present, EITC > 0 in the key | 97 (35 with the undocumented person as head) |
| an undocumented person present, CTC > 0 in the key | 110 |

These overlap and are counted from narrative text, so they are exposure bounds, not
per-task verdicts. The held-out split has the same generator and the same defects.

### Legal confidence

The engine measurements are exact (engine output, reproducible). The claims that the keys are
**wrong in law** are MEDIUM, not high. The SSN requirement for EITC (IRC §32(m)) and the SNAP
student work exemption (7 CFR 273.5(b)) are well known, but neither primary source was read
in this session. Per the standing rule, they are not promoted until read.

### What the acceptance test checks

`tests/test_unstated_premises.py` traces representative households (single adult, two
adults with a child, student earner, undocumented) through the real generator, oracle and
`render()`. It requires every defaulted input to be either covered by a closure sentence
present in the rendered narrative (none-like defaults only), or proved moot **by perturbation
in that household**. Anything in `MUST_STATE` fails until the oracle sets it and the narrative
states it. So does any oracle input the narrative does not state, and any engine filing role
(head, spouse, dependent) not matched by a stated filing structure. Today each shape fails on
the same nine problems. Before the xfail marker went on, the problem lists were printed to
confirm the failures come from premises, not an exception in the test (an xfail absorbs both).

### Decisions this needs (see the session report)

The instruction was to make narratives "state what the oracle builds". For two-adult
households that cannot be done honestly: the oracle builds parents married to their adult
children. The generator has to produce explicit relationships and filing structure, the
oracle has to build from them, and the narrative has to state them. That is a
household-modelling decision, and it also touches SNAP household composition (who purchases
and prepares food together), which the oracle likewise puts in one unit unconditionally.

### §36 resolution, 2026-09-19: fixed, verified, and both splits rebuilt

**The fix** (commit `1debd8b`, details in its message and docs/PRIMARY_SOURCES_2026-09.md):
two explicit household shapes; oracle-set roles, SSN, hours, enrolment intensity and take-up;
narratives that state all of it; a closed fact vocabulary in the prompt; exact identifier
matching in `score_abstention`. The primary sources were read first, and the engine matches
the statute on every rule read. Each wrong key came from a default our oracle left unset, so
there is no upstream report.

**The acceptance test passes for the right reason.** `tests/test_unstated_premises.py`
passes on seven representative shapes, and ten deliberate mutations each turn it red:
dropping the SSN, hours or closure clause; a wrong structure sentence; the oracle omitting
hours or SSN; the oracle letting the engine infer the spouse; swapping the ITIN wording or the
undocumented wording for citizen wording; a blind tracer. Its first version let two
mutations survive. It imported its expectations from the renderer it was checking, and it
could not see roles the engine inferred correctly by coincidence. Both are fixed; the test
now owns its expectations and requires every must-state premise to be SET, not merely absent
from the defaults. It also produced two false failures on correct code (a second phrasing of
"no disability"; "work" matching "is not working"), both caught by running it on correct code
before trusting it.

**The determinism fixture could never have caught §36.** Before re-capturing, its five
households were run through the new oracle and gave identical answers, because all five were
single-adult with no undocumented adult. It now holds eight: a married couple, an
undocumented adult, a mixed-status couple, and student earners under and over 20 hours.

**Both splits rebuilt** on generator fingerprint `93c3b90c3f0cae06`, in 34 minutes each
(the previous build took 92):

| | dev | held-out |
|---|---|---|
| class mix 780 / 96 / 144 / 180 | exact | exact |
| pairs, differ / same | 100 / 100 | 100 / 100 |
| distinct task hashes | 1,200 | 1,200 |
| overlap | 0 with the old dev corpus | 0 with the new dev split |

The held-out manifest records `seed: None` and fingerprint `9a608a27bead4c03` only.
§31's staleness is resolved: the corpus contains refugee and asylee households again, and
`test_committed_dev_corpus_samples_every_generated_status` now asserts it.

**The eligibility-flip rate fell** from the earlier corpus to about 5% (3 of 60 in a probe).
Stated hours exempt students working 20+ hours (7 CFR 273.5(b)(5)), so they no longer flip.
That is the correct result: the old flips included students whose "ineligibility" was the
0-hours default. The 96-flip quota still filled before 1,000 candidates.

**Metrics on the rebuilt dev split** (real `run_eval` entry point, $0):

| | exact-match | abstention | pair |
|---|---:|---:|---:|
| ceiling (perfect agent) | **1.000** | **1.000** | **1.000** |
| always_abstain | 0.000 | **0.000** (was 0.131) | 0.000 |
| never_abstain | 0.194 | 0.340 | 0.480 |
| always_eligible | 0.041 | 0.343 | 0.500 |
| never_eligible | 0.123 | 0.083 | 0.085 |
| rules_only | 0.194 | 0.376 | 0.480 |
| pair_always_differ | 0.158 | 0.340 | 0.370 |

The ceiling holds under exact fact matching, so the metric is reachable. `always_abstain`
falling to 0.000 is intended: it abstains without naming the withheld fact, so it no longer
earns credit it did not reason for. The pair diagnostics stay discriminating and asymmetric
(0.480 vs 0.370, was 0.495 vs 0.380).

## 37. First run on the corrected corpus: the category/quantity split does NOT reproduce

**Status: PARTIAL run, 155 of 1,200 tasks, 2026-09-20. The headline finding of this project
does not reproduce on GPT-5.6 Sol on the corrected corpus. Two things changed at once, so
this does not yet say which.**

### What was run

GPT-5.6 Sol, corrected dev split, `max_tokens` 16,000, hard cap $40. The run stopped when
**OpenRouter refused further requests: 403 "Key limit exceeded (total limit)"** after 155
billed requests and **$4.4770**. Our cap did not bind. The harness fell back to
`--cached-only` scoring, which is what it is for: 155 tasks scored, 0 incomplete pairs, no
pair-consistency (0 complete pairs in the fetched subset).

**The 155 are not a random sample.** They are the tasks the pre-warm reached first, in split
order. Every number below is over that subset, and the class n's are 18-59.

### Fact-format compliance (checked BEFORE the headlines)

53 `cannot_determine` entries across 30 replies:

| what the model wrote | n | credited? |
|---|---:|---|
| the withheld identifier, exactly | 40 | yes |
| `p1.ssn_status` where `p1.immigration_status` was withheld | 10 | yes - coupled (§36) |
| `p1.student_full_time` where `p1.is_higher_ed_student` was withheld | 1 | yes - coupled |
| `p1.declared_benefits` on an incomplete-determinate task | 1 | no - a needless abstention |
| `other: p2 date lawful permanent residence` | 1 | no - a needless abstention |

**51 of 53 named the withheld fact or its coupled half.** No near-misses, no free text, no
invented identifiers: the closed vocabulary in the prompt works. The two that did not are
both needless abstentions on incomplete-determinate tasks, which is a model judgement, not a
format failure. The `other:` one asks for an LPR's date of entry - a real five-year-bar fact
that the engine's SNAP path does not read, so it cannot change the key (LIMITS §16).

**Truncation: 0 of 155** (`stop: end` for every response). At `max_tokens` 8,000 the 20-task
probe had 1. Raising it to 16,000 removed the mechanism, at a cost: mean output rose from
1,953 to 2,702 tokens, so $0.0213 -> $0.0289 per task.

Also: 2 schema-invalid of 155 (gate 0.974), 0 scorer errors.

### The headline

| | GPT-5.6 Sol, corrected corpus (n=155) | Opus 5, superseded corpus (n=1,200) |
|---|---:|---:|
| exact-match, determinate | 0.724 (42/58) | 0.514 |
| abstention, all T1b | 0.825 (80/97) | 0.438 |
| **eligibility-flip (a category)** | **0.667 (12/18)** | **0.396** |
| **indeterminate (a quantity)** | **0.600 (12/20)** | **0.050** |
| incomplete-determinate (answer anyway) | 0.949 (56/59) | 0.951 |
| ratio, category : quantity | **1.11x** | **7.9x** |

**The eight-fold gap is gone.** On this model, on this corpus, noticing a missing fact whose
absence moves a *quantity* is almost as reliable as noticing one that moves a *category*:
0.600 against 0.667, well inside the noise at these n's.

### What this does and does not establish

**It does not yet say the original finding was wrong.** Two variables moved together:

1. **The model.** Opus 5 -> GPT-5.6 Sol, a different lab.
2. **The corpus.** The old corpus's answer keys rested on unstated premises, and the
   indeterminate class was where a model that reasoned carefully about a missing amount was
   most likely to be scored wrong anyway. 0.050 was measured under a scorer that also
   ignored the named fact and a key that assumed marriages nobody stated.

Opus 5 on the corrected corpus is the missing arm, and it is the one that separates these:
same corpus, same scorer, different model. It is blocked on a dead Anthropic key.

**The honest reading today:** the 8x gap is a property of one model measured on a defective
corpus, and it has not been reproduced. Nothing in the README claims it any more (withdrawn
2026-09-19), and it must not be reinstated on the strength of the old numbers.

### A harness defect this run exposed

`Budget.charge_failed` charged every failed request its worst case, including the 1,045
requests OpenRouter **refused** with 403 and never billed. The cap therefore read
**$39.84 of $40** while actual spend was **$4.48**. Nothing was overspent - the error is in
the conservative direction - but a cap exhausted by unbilled refusals would stop the next
legitimate run early on a number that is wrong. Fixed: `providers.is_unbilled_error`
classifies statuses that cannot have been billed (401/403/429/400/404/422 and a connection
that never opened) and `Budget.release_unbilled` returns those reservations; a timeout stays
chargeable, because billing is genuinely unknown there. Results files now record
`provider_refusals_unbilled`. Tested through `live_agent` with a provider that refuses.

### The uncomfortable version, stated plainly

**The finding this project was built on may not survive.** The published 0.050 on the
indeterminate class — the denominator of the eight-fold gap — was measured under three
conditions, each of which could independently have produced it as an artifact:

1. **A third of the corpus rested on unstated premises.** Two-adult households were keyed as
   married couples, undocumented filers were keyed with a citizen's SSN, and students with
   stated earnings were keyed at zero hours (§36). A model reasoning correctly about a
   missing amount was competing against a key that was itself wrong on those tasks.
2. **The scorer ignored the named fact.** It compared programs only, so an abstention was
   credited or denied without reference to whether the model had identified the right hole
   (§36). It also rejected correct abstentions outright until §27.
3. **The flip class was inflated by a default.** Students whose "ineligibility" came from
   `weekly_hours = 0` were counted as eligibility flips. With hours stated, a student working
   20+ hours is exempt (7 CFR 273.5(b)(5)) and does not flip; the measured flip rate fell to
   about 5%. So the *numerator* class of the ratio was partly an artifact too.

Any one of these could move 0.050. Together they are enough that **"the gap was real" is not
currently the most likely explanation**. The first measurement on a corrected corpus found
1.11x where the original found 7.9x.

What would settle it is one experiment: **Opus 5 on the corrected corpus**, same scorer, same
tasks. If the gap returns for Opus and not for GPT-5.6 Sol, it is a model property and the
original finding stands, narrowed to one model. If it returns for neither, the original was an
artifact of the corpus and scorer, and it is retracted at the top of the README, on the Hub
listing, and anywhere else it was claimed. The correction is drafted in advance
(`docs/CORRECTION_DRAFT.md`) so it is not written under the temptation of a result we prefer.

Until that run exists, the honest state is **"not reproduced"** — not "refuted", and
certainly not "holds".

### Fact-format compliance is a clean methodological result

Separate from the model finding, and it survives whatever the Opus run says: **the closed
vocabulary works.** 51 of 53 `cannot_determine` entries named the withheld identifier exactly
or its coupled half, with **zero near-misses and zero invented identifiers**. The two
exceptions were needless abstentions on incomplete-determinate tasks, i.e. model judgement,
not format failure.

That matters because exact identifier matching is only fair if the model is told the
identifiers. Before the change, GPT-5.6 Sol wrote `p1.college_enrollment` for
`p1.is_higher_ed_student` and Opus wrote `p1.housing_costs` for `housing_cost`; both are
right in meaning and unscoreable. Rendering the vocabulary from the schema removed that
class of failure entirely rather than papering over it with fuzzy matching, which would have
made the scorer's judgement unauditable.

### The employment_income confound is retired

The README used to explain the per-fact table by "income has an obvious slot in a case file".
The competing explanation was that `p1.employment_income` was the prompt's only example
identifier. The prompt now lists every identifier and its example names none of them, so the
question is answerable. Per-fact abstention, GPT-5.6 Sol, corrected corpus, partial run
(cells are 1-11 tasks; abstention is only REQUIRED in the first two columns):

| withheld fact | flip | indeterminate | incomplete-determinate | all T1b |
|---|---|---|---|---|
| `p1.is_higher_ed_student` | 1.000 (1/1) | 0.800 (4/5) | 0.970 (32/33) | 0.949 (37/39) |
| `housing_cost` | 1.000 (2/2) | 1.000 (3/3) | 0.833 (5/6) | 0.909 (10/11) |
| `dependent_care_cost` | 0.000 (0/1) | 0.500 (2/4) | 1.000 (7/7) | 0.750 (9/12) |
| `p1.immigration_status` | 0.667 (2/3) | 0.600 (3/5) | 1.000 (3/3) | 0.727 (8/11) |
| `p1.employment_income` | 0.636 (7/11) | — | 1.000 (1/1) | 0.667 (8/12) |
| `p1.age` | — | 0.000 (0/3) | 0.889 (8/9) | 0.667 (8/12) |

**Employment income is no longer the best-noticed fact** (0.667, mid-table, against 0.709 and
top of the table before). Immigration status went from **worst** (0.133) to 0.727. Both moves
are in the direction the prompt-example hypothesis predicts, and **neither is established by
this**: the corpus changed too, and it now states an SSN clause alongside immigration status,
which is a second textual cue for exactly that fact. The cells are also tiny. What can be
said is that the confound is gone from the *design*: every identifier is now named equally,
so the next measurement is interpretable where the old one was not.

## 38. GPT-5.6 Sol, complete run on the corrected corpus: the classes are indistinguishable

**Status: complete (1,198 of 1,200 tasks), 2026-09-21. On this model the category/quantity
split does not merely fail to reproduce - the two classes are identical to three decimal
places. The Opus 5 arm is still required before the finding is retracted or narrowed.**

1,198 tasks scored; 2 were lost to transient connection errors and are simply absent (the
harness scores what it fetched and says how many it missed). $32.9163 billed against a $40
cap, $0.0312 per task, `max_tokens` 16,000.

### 1. Fact format and truncation

| | n | share |
|---|---:|---:|
| named the withheld identifier EXACTLY | 298 | 65.9% |
| named its COUPLED half (credited, §36) | 135 | 29.9% |
| **credited total** | **433 of 452** | **95.8%** |
| `other:` escape, not credited | 16 | 3.5% |
| a different stated fact, not credited | 3 | 0.7% |

**Zero invented identifiers, zero near-misses, zero free-text identifiers.** The closed
vocabulary holds at scale: every one of 452 entries was either a listed identifier or the
explicit `other:` escape, which is what the escape is for.

**Truncation: 0 of 1,198** (`stop: end` on every response) at `max_tokens` 16,000, against
1 of 20 at 8,000. The §34 mechanism is closed for this model at this limit.

Also: 8 schema-invalid of 1,198 (gate 0.970), 0 scorer errors, every response served by
OpenAI under the pinned routing.

**A pattern in the 16 `other:` escapes.** Thirteen ask, in different words, for how long a
lawful permanent resident has held that status - the SNAP five-year bar. That fact is real,
is not stated in our case files, and **the engine's SNAP path does not read it**
(`years_since_us_entry` exists and the status test ignores it, LIMITS §16). So the model is
repeatedly identifying a premise that the answer key genuinely does not depend on. It is not
credited, and it should not be, but it is the clearest signal in this run that the corpus
still has a legal gap the engine papers over.

### 2. Headlines, with intervals

| | k / n | value | Wilson 95% CI |
|---|---|---:|---|
| T1 exact-match (determinate) | 489 / 778 | **0.629** | [0.594, 0.662] |
| T1b abstention (all) | 322 / 420 | **0.767** | [0.724, 0.805] |
| pair consistency | 200 pairs | **0.655** | — |

### 3. Verdict against the decision rule in `docs/CORRECTION_DRAFT.md`

The rule: *"reproduces" means the flip class exceeds the indeterminate class by a margin that
survives the n's actually collected, reported with the interval, on the full 1,200-task
split.*

| class | k / n | value | Wilson 95% CI |
|---|---|---:|---|
| eligibility-flip (a **category**) | 66 / 96 | **0.688** | [0.589, 0.771] |
| indeterminate (a **quantity**) | 124 / 180 | **0.689** | [0.618, 0.752] |
| incomplete-determinate (answer anyway) | 132 / 144 | 0.917 | [0.860, 0.952] |

**Difference: -0.001, 95% CI [-0.118, +0.109], Fisher exact p = 1.000. Ratio 1.00x, against
the withdrawn 7.9x.**

**Verdict: does NOT reproduce on GPT-5.6 Sol.** Not "reduced", not "weaker" - the two classes
land on the same number, and the interval is symmetric about zero. This is as clean a
negative as the design can produce at these n's: with 96 and 180 tasks the comparison
resolves a difference of roughly 0.12 or larger, and the withdrawn claim was 0.346
(0.396 vs 0.050), which would have been unmissable.

**Which branch applies on GPT alone: B, provisionally.** Branch B (retraction) is the reading
this run supports, and it is not yet executed, because one model cannot distinguish
"the original was an artifact of the corpus and scorer" from "the effect is specific to Opus".

**What Opus 5 on the corrected corpus would need to show to change it:** its flip class
exceeding its indeterminate class with the Newcombe interval excluding zero on the same
1,200 tasks - in practice a gap of about 0.12 or more. That would put Branch A in force
(model-specific, old magnitudes not reinstated). Anything smaller, or an interval spanning
zero, puts Branch B in force and the finding is retracted.

### 4. Per-fact, split by class

Abstention is REQUIRED only in the first two columns; in incomplete-determinate the correct
behaviour is to ANSWER, so a high number there means "did not abstain".

| withheld fact | flip | indeterminate | incomplete-determinate | all T1b |
|---|---|---|---|---|
| `p1.is_higher_ed_student` | 0.714 (10/14) | 0.789 (30/38) | 0.960 (72/75) | 0.882 (112/127) |
| `p1.immigration_status` | 0.857 (12/14) | 0.700 (35/50) | 0.750 (6/8) | 0.736 (53/72) |
| `housing_cost` | 1.000 (9/9) | 0.966 (28/29) | 0.789 (15/19) | 0.912 (52/57) |
| `p1.age` | 0.500 (1/2) | 0.536 (15/28) | 0.909 (20/22) | 0.692 (36/52) |
| `dependent_care_cost` | 0.500 (2/4) | 0.471 (16/34) | 0.941 (16/17) | 0.618 (34/55) |
| `p1.employment_income` | 0.604 (32/53) | 0.000 (0/1) | 1.000 (3/3) | 0.614 (35/57) |

**The withdrawn per-fact story is gone too.** On the old corpus, income was the best-noticed
fact (0.709) and immigration status the worst (0.133), and the README explained that as
"line item versus background premise". Here immigration status is **second best** (0.736) and
**income is last** (0.614). The reversal is consistent with the confound the old table had -
`p1.employment_income` was the prompt's only example identifier - but three things changed at
once (corpus, scorer, model), so this is a fact about the new measurement, not a proof about
the old one.

Two cells are thin and should not be read as facts about those cells:
`p1.employment_income` has 1 indeterminate task and `p1.age` has 2 flips, because the
generator routes each fact through the stream where it can actually produce that class.

## 39. The SNAP five-year bar: a real fact the case files omit and the engine ignores

**Status: OPEN corpus gap, found 2026-09-21 by reading what GPT-5.6 Sol asked for. The model
was more correct than the benchmark.**

Of the 16 `other:` escapes in the completed GPT-5.6 Sol run (§38), **13 ask the same
question in different words**: how long the lawful permanent resident has held that status.
Examples, verbatim: *"other: p1 SNAP five-year-bar status"*, *"other: p1 duration in
qualified immigration status"*, *"other: p2 date lawful permanent residence began"*.

**The model is right, and it is asking for something the benchmark cannot score.**

- **The fact is real.** Most qualified non-citizens must complete a five-year waiting period
  before federal SNAP eligibility (8 U.S.C. §1613; the exceptions include refugees, asylees,
  children and veterans). For an LPR adult it is genuinely load-bearing.
- **Our case files do not state it.** The narrative gives immigration status and SSN status;
  it says nothing about when the status began. The generator has no such field.
- **The engine does not read it.** `years_since_us_entry` exists in `policyengine-us` and
  defaults to 5, but SNAP's status test never consults it (LIMITS §16). So the answer key is
  computed as though the bar does not exist.

So the escape is correctly not credited — `score_abstention` credits the *withheld* fact, and
this fact was never withheld because it is never present. But the reason it cannot be
credited is that **our corpus is silent on a premise that the law makes decisive**, not that
the model was wrong. That is the same class of defect as §36, one layer out: §36 was about
premises the *engine* reads at a default; this is about a premise the engine ignores and the
law does not.

**Why `tests/test_unstated_premises.py` cannot catch it.** That test asks the engine which
inputs it read, and the engine never reads this one. A fact the oracle is blind to is
invisible to a tracer over the oracle. The test closes "the key depends on something unstated";
it cannot close "the law depends on something the key ignores". Those are different holes and
this one needs a different instrument — the rules table, which is exactly what it is for
(SPEC.md §6, and the reason every citation gets read before the rule is written).

**Options, none taken yet (a decision, and it interacts with scope):**

1. **State it.** Add a duration-of-status fact to the generator and narrative ("has held
   lawful permanent resident status since 2016"), so the case file is complete even though
   the engine ignores it. Cheapest, and it removes the ambiguity from the corpus — but the
   answer key still would not reflect the bar, so a model that reasoned *from* the stated
   date would be scored wrong for being right.
2. **Exclude the shape.** Generate LPR adults only where the bar cannot bite, which in
   practice means refugees, asylees, entrants and citizens. Narrows the immigration fact
   space that §16 was just re-widened to cover.
3. **Record it as a scope limit and score around it.** State in LIMITS and the README that
   SNAP answers for LPR adults assume the five-year bar is satisfied, and keep the tasks.
   Honest, and it leaves a known-wrong cell in the corpus.

Option 3 is what the corpus does *today*, implicitly and undocumented; this section makes it
explicit until a decision is taken. Whichever is chosen, it belongs in the same pass as any
future work on the rules table, and it is a reminder that the benchmark's ground truth is
only as complete as the engine beneath it — the standing risk recorded in CLAUDE.md,
"Oracle freshness is a structural risk", arriving from a direction nobody had checked.

## 40. Why the five-year-bar fix waits for the cross-model comparison

**Status: decided 2026-09-21. `lpr-five-year-bar` (branch `925e195`) stays unmerged until
Claude Opus 5 has run on the CURRENT corpus. Then it is merged and both splits are
regenerated once.**

§39 found a real gap: the SNAP five-year bar (8 U.S.C. 1613) turns on how long a qualified
non-citizen has held status, our case files do not state it, and the engine never reads it.
The fix is built and tested. It is deliberately **not** applied yet, and the ordering is
recorded here so it is a decision rather than an accident of scheduling.

**1. The fix changes every prompt.** `status_since` joins the closed fact vocabulary, so
`SYSTEM_PROMPT` changes, so every response-cache key changes. The 1,198 cached GPT-5.6 Sol
responses — $32.9163 of paid work — would stop matching, and the completed §38 run could no
longer be compared with anything measured afterwards.

**2. The deciding experiment requires identical tasks.** The open question is whether the
withdrawn 0.396/0.050 split was an artifact of the old corpus and scorer, or an effect
specific to one model. Only a same-corpus, same-scorer, different-model comparison answers
it. Regenerating between the two runs would confound exactly the variable the experiment
isolates, and would leave us having paid twice for an answer to a different question.

**3. The current keys are unstated, not wrong.** This is what makes waiting safe rather than
merely convenient. `years_since_us_entry` defaults to **5** in `policyengine-us`, and SNAP's
status test never consults it, so every answer key in the current corpus already means
"the bar is satisfied". The fix does not correct a wrong number; it states out loud a premise
the key already assumes. Nothing in the corpus becomes more correct by regenerating first.

**4. Both models face identical silence.** GPT-5.6 Sol was not told the duration, and Opus 5
will not be either. Whatever the omission costs a model, it costs both the same way, so the
comparison between them is unaffected. What the omission does affect is the *absolute*
abstention numbers, which is why §39 stays open and the fix is queued rather than dropped.

Measured on the GPT run, tasks containing an LPR adult versus the rest: abstention
**0.660** [0.517, 0.778] against **0.780** [0.735, 0.819]; exact-match 0.616 against 0.630.
The intervals overlap, so the cost of the silence is suggested and not established. The same
subset will be reported for Opus, which is the cheapest available check on whether it is a
property of the corpus or of one model.

**Order, fixed in advance:** Opus 5 on the current corpus at an $85 cap → report against the
decision rule in `docs/CORRECTION_DRAFT.md` → merge `lpr-five-year-bar` → regenerate both
splits once → re-run whatever the corrected numbers then require, priced and approved
separately.
