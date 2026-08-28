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
