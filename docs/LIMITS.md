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

### Track (b) - published CalFresh tables: 10 / 10 exact matches

Ten comparisons against externally published figures. **Zero discrepancies**, so there
is nothing to classify by cause — stated plainly rather than padded.

| # | kind | case | month | FFY | published | oracle | delta |
|---|---|---|---|---|---|---|---|
| 1 | formula | 2p, $1,200 earned, $900 rent | 2025-04 | FFY2025 | 522.00 | 522.00 | 0.00 |
| 2 | formula | 2p, $0 earned, $900 rent | 2025-04 | FFY2025 | 536.00 | 536.00 | 0.00 |
| 3 | formula | 2p, $2,000 earned, $1,200 rent | 2025-04 | FFY2025 | 330.00 | 330.00 | 0.00 |
| 4 | formula | 2p, $800 earned, $0 rent | 2025-04 | FFY2025 | 533.00 | 533.00 | 0.00 |
| 5 | allotment | 1p, zero income | 2025-11 | FFY2026 | 298.00 | 298.00 | 0.00 |
| 6 | allotment | 2p, zero income | 2025-11 | FFY2026 | 546.00 | 546.00 | 0.00 |
| 7 | allotment | 3p, zero income | 2025-11 | FFY2026 | 785.00 | 785.00 | 0.00 |
| 8 | allotment | 4p, zero income | 2025-11 | FFY2026 | 994.00 | 994.00 | 0.00 |
| 9 | allotment | 5p, zero income | 2025-11 | FFY2026 | 1,183.00 | 1,183.00 | 0.00 |
| 10 | allotment | 6p, zero income | 2025-11 | FFY2026 | 1,421.00 | 1,421.00 | 0.00 |

**`formula`** cases are a full hand calculation from published constants and the
published CalFresh formula: net = gross − standard deduction − 20% of earned income −
excess shelter; benefit = max allotment − ⌈0.30 × net⌉.
**`allotment`** cases exploit the fact that a zero-income household has zero net income,
so its benefit must equal the published maximum allotment for its size — checking one
published cell directly without needing deduction constants.

`test_hand_calculation_is_reproducible_from_published_constants` re-derives the four
formula expectations from the published constants, so they cannot silently drift into
being copied from the engine.

**Sources**, all retrieved 2026-08-29:

- **[A]** LSNC *Guide to CalFresh Benefits*, "Maximum CalFresh deductions",
  https://calfresh.guide/maximum-calfresh-deductions/ — FFY2025, effective
  10/01/2024–09/30/2025. Standard deduction 1–3 $204 / 4 $217 / 5 $254 / 6+ $291;
  earned income deduction 20%; SUA $645; LUA $166; telephone $19; max excess shelter $712.
- **[B]** Santa Clara County DEBS, "CalFresh Program Monthly Allotment and Income
  Eligibility Standards Charts" — FFY2026, effective 10/01/2025–09/30/2026.
  Max allotment 1–8: 298 / 546 / 785 / 994 / 1,183 / 1,421 / 1,571 / 1,789.
  Gross limit (130% FPL) 1–4: 1,696 / 2,292 / 2,888 / 3,483.
  Net limit (100% FPL) 1–4: 1,305 / 1,763 / 2,221 / 2,680.
- **[C]** Santa Clara County DEBS Update 24-07, "CalFresh COLA for FFY 2025" — confirms
  the FFY2025 shelter cap $712, SUA $645, LUA $166, and the 2-person max allotment $536.

Parameter cells additionally confirmed against the engine's own parameter tree:
FFY2026 max allotment (all 8 sizes), FFY2025 standard deduction (all 6 brackets),
earned income deduction 20%, gross limit 1.3 × FPL, net limit 1.0 × FPL.

**Two effects that would have produced spurious discrepancies, both controlled for:**

1. **The federal fiscal year boundary falls inside our tax year.** FFY2025 runs
   2024-10-01 to 2025-09-30; FFY2026 begins 2025-10-01. A tax-year-2025 household in
   July is on FFY2025 standards, one in November on FFY2026. The engine switches
   correctly at October — a 1-person zero-income household is paid $292 in September and
   $298 in October, matching the two published tables. `test_fiscal_year_boundary_falls_at_october`
   locks this. Comparing a November household against an "FY2025" published example
   would be a **tax-year mismatch**, not an engine error.
2. **Modelling scope.** PolicyEngine models a zero-earnings California household as
   receiving CalWORKs cash aid, which counts as unearned income for SNAP and reduces the
   allotment. A published SNAP worked example takes gross income as given. The
   comparison suppresses `tanf`/`ca_tanf` so the two are like-for-like. Not doing so
   produces a discrepancy whose cause is **modelling scope**, not an engine error — the
   engine is arguably more correct about the household's real circumstances.

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

- SNAP benefit amount, California, **FFY2025** (2024-10-01 – 2025-09-30), household
  sizes 1–2, against published deduction tables and the published formula.
- SNAP maximum allotment, California, **FFY2026** (2025-10-01 – 2025-09-30), household
  sizes **1–6**, against the published allotment table.
- SNAP structural parameters: standard deduction (FFY2025), earned income deduction
  rate, gross/net income limit multipliers.

**Wired but NOT externally validated — everything else**, specifically:

- **Medicaid** — no external comparison of any kind was performed. Treat every Medicaid
  output as unvalidated.
- **EITC and CTC** — no external comparison performed.
- SNAP for household sizes 7+, for FFY2025 sizes 3+, and any month outside the two
  sampled.
- All eligibility *booleans* (`is_snap_eligible`, `is_medicaid_eligible`) — only benefit
  *amounts* were checked.

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
