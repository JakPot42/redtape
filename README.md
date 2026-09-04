# Redtape v0

Verifiable training-and-evaluation environments for US public-benefits work.

**Status: first frontier-model result in hand.** Two 1,200-task splits built, Claude Opus 5
evaluated end to end on the dev split, five baselines and three tool conditions run, three
headline metrics confirmed both discriminating and achievable. The held-out split has never
been evaluated against and stays that way.
**Nothing here is peer-reviewed**, and the validated surface is narrower than
the test count suggests — read [`docs/LIMITS.md`](docs/LIMITS.md) before citing any number
in this repo. It is written as the work happens rather than retrofitted, and it states what
is *not* validated at least as carefully as what is.

## What this is

Existing benchmarks (PolicyEngine's PolicyBench, Column Tax's TaxCalcBench, Stanford's
HealthAdminBench) score whether a model computes the right *amount*. Redtape adds the
harder half: whether an agent knows **which facts are required, which are missing, and
when it cannot answer** — which is where real filings fail.

The obvious objection is that "knows when it cannot answer" is just arithmetic competence
wearing a different hat. The result below is the answer to that objection.

## Result: the model computes well, and misses missing facts unevenly

**Claude Opus 5, 1,200-task dev split, no tools.** It computes benefits far better than any
trivial strategy — **0.514 exact-match against a 0.205 best baseline** — and its abstention
behaviour splits sharply by *what kind* of fact is missing.

Where a withheld fact flips SNAP eligibility outright, it abstains correctly **40% of the
time**. Where a withheld fact moves an amount past tolerance without changing eligibility,
it abstains correctly **5% of the time**. Where a fact is missing but does not decide the
outcome — so answering is correct — it answers **95%** of the time. Across the whole split it
volunteers `cannot_determine` in **6.3%** of responses.

So the failure is not uniform indifference to missing data. **It recognises a missing fact
when the fact would change a categorical answer, and largely misses one that would change a
number.**

### Three headline metrics

Reported separately. The weighted composite exists but is deliberately not the headline, so
that retuning a weight cannot move a published number.

| | exact-match<br>(determinate, n=780) | abstention<br>(T1b, n=420) | pair-consistency<br>(200 pairs) |
|---|---:|---:|---:|
| **Claude Opus 5** | **0.514** | **0.438** | **0.570** |
| baseline: always_abstain | 0.000 | 0.131 | 0.000 |
| baseline: never_abstain | 0.205 | 0.336 | 0.495 |
| baseline: always_eligible | 0.036 | 0.343 | 0.500 |
| baseline: never_eligible | 0.115 | 0.074 | 0.060 |
| baseline: rules_only | 0.205 | 0.326 | 0.495 |
| *ceiling: answers and abstains correctly* | *1.000* | *1.000* | *1.000* |

Gate pass rate 0.981; **0 malformed-JSON, 0 schema-invalid, 0 scorer errors**. The ceiling
row is a diagnostic agent that answers from the key **and** abstains on exactly the deciding
programs — it exists because a metric nobody can score 1.000 on is broken, and until it was
written nothing established that the abstention metric was reachable at all.

Abstention at 0.438 is 0.102 above the never-abstain baseline: better than the degenerate
strategy, and less than a third of the way from it to the ceiling.

### The aggregate hides the split

| class | correct | n | accuracy | correct behaviour |
|---|---:|---:|---:|---|
| indeterminate | 9 | 180 | **0.050** | abstain — the fact moves an amount past tolerance |
| eligibility-flip | 38 | 96 | **0.396** | abstain — the fact flips SNAP eligibility |
| incomplete-determinate | 137 | 144 | **0.951** | answer anyway — the fact does not decide |

An eight-fold gap between the two classes where abstention is required. Both are cases where
a required fact is absent; they differ only in whether its absence changes a *category* or a
*quantity*. The incomplete-determinate class is what stops the benchmark being won by always
abstaining — and 0.951 there confirms the model is not simply cautious.

### Which missing facts go unnoticed

| withheld fact | correct | n | accuracy |
|---|---:|---:|---:|
| `p1.employment_income` | 39 | 55 | 0.709 |
| `p1.is_higher_ed_student` | 80 | 147 | 0.544 |
| `housing_cost` | 26 | 50 | 0.520 |
| `dependent_care_cost` | 18 | 47 | 0.383 |
| `p1.age` | 13 | 61 | 0.213 |
| `p1.immigration_status` | 8 | 60 | **0.133** |

**Immigration status is noticed least often of all, at 0.133 — and it is the fact with the
starkest consequence**, determining outright whether a person is eligible for federal SNAP.
Age (0.213) is second-lowest and behaves similarly, setting elderly status and dependency.
Income, the fact most often *stated* as an explicit line item in a case file, is noticed most
(0.709).

One reading is that the model tracks absent *line items* better than absent *premises* —
income has an obvious slot in a case file, immigration status is background. The data is
consistent with that and does not establish it; separating the two would need a split that
varies the same fact between line-item and premise presentation, which this one does not.

### The obvious objection, tested before publication

The prompt names `cannot_determine` in three places, so this is not a measure of whether the
model knows the mechanism exists. But the prompt's closing clause was one-sided where the
scoring is symmetric: it warned that "a needless abstention is scored as wrong as a wrong
number" and never stated the converse. Publishing an abstention figure with that clause in
the prompt invites the charge that the result was written into the instructions.

So we A/B'd it. 60 tasks weighted toward the classes where abstention is correct; arm B
**balanced** the clause rather than deleting it (deleting would test silence-vs-deterrent, a
different question).

| | arm A (shipped) | arm B (balanced) | Fisher exact |
|---|---|---|---|
| replies containing any `cannot_determine` | **12 / 60** | **12 / 60** | p = 1.000 |
| abstention accuracy, all T1b | 19 / 54 = 0.352 | 18 / 54 = 0.333 | p = 1.000 |

The raw abstention rate is **identical, not similar**, and balancing the clause moved
accuracy slightly *down*. Instruction asymmetry is ruled out as the explanation.

### What this does and does not claim

The claim: **a frontier model recognises a missing fact far more reliably when its absence
would change a categorical outcome than when it would change a quantity, and volunteers
abstention rarely in absolute terms.** Narrower than "models cannot tell when they lack
information", and it is what the data supports.

- **One model, one state, one prompt pair.** Claude Opus 5, California only, tax year 2025.
- **The A/B excludes a large effect, not a modest one.** At a 12/60 base rate, 60 tasks per
  arm reliably detects roughly a doubling. A real 12 → 18 shift would have been missed.
- **These numbers are a correction.** An earlier version of this section reported abstention
  0.357 and an indeterminate rate of 0.006, because the schema demanded a number for a
  program the model had just declared undeterminable — so 47 correct abstentions were
  rejected as malformed and scored as failures. The benchmark was penalising the behaviour it
  exists to reward. Fixed, re-scored from cache, and recorded in `docs/LIMITS.md` §27.
- **Abstention labels are approximate.** A perturbation sweep can prove a fact is deciding
  but not that one is not (`docs/LIMITS.md` §4). Mislabelling would push the measured rate
  *up*, not down, so the direction survives; the size is unmeasured.
- **Medicaid is computed but not scored** — no external validation was obtainable, and a cell
  backed only by the engine agreeing with itself is the circularity this project exists to
  avoid.

**Read [`docs/LIMITS.md`](docs/LIMITS.md) before citing any number here.** 27 sections,
written as the work happened rather than retrofitted, stating what is *not* validated at
least as carefully as what is — including three sections retracting our own errors.

**Reproducing it:** every model response is cached in `cache/responses/dev/` and committed,
so the scored artifact can be re-derived without spending anything. The full run cost $59.66.

## The metric measures judgment, not arithmetic

Three conditions over the same tasks. `tool_equipped` gives the agent a calculator that
takes a structured household and returns the benefit. `tool_equipped_unknowns` gives it the
same calculator, except a fact may be passed as `"unknown"` — instead of defaulting it, the
tool sweeps that fact and reports which programs its value decides.

300 tasks, weighted toward T1b so neither cell is thin: 150 determinate and 150 T1b
(60 indeterminate, 50 incomplete-determinate, 40 eligibility-flip).

| condition | exact-match (n=150) | abstention (n=150) |
|---|---:|---:|
| `tool_less` | 0.247 | 0.327 |
| `tool_equipped` | **0.740** | 0.333 |
| `tool_equipped_unknowns` | 0.740 | **0.733** |

**The calculator moves exact-match by +0.493 and abstention by +0.006. Marking withheld
facts moves abstention by +0.400 and exact-match by exactly zero.**

The two axes separate cleanly, and each "no effect" arm really is flat rather than merely
small. Arithmetic help does not buy abstention accuracy; determinability help does not buy
arithmetic accuracy. That is direct evidence the abstention metric measures something the
amount-scoring benchmarks do not, which is the whole premise of the project — and the single
result that could have shown the premise was empty. It didn't.

Two things this is not. **No model is called** — all three conditions use scripted agents,
so this is an upper bound on what the *tool* offers a perfect extractor, not a measurement
of any model's behaviour; the model result is the section above. And pair rows are excluded
from the sample, because they are all determinate and partially sampling them would make
`pair_consistency` report a sampling artifact.

## Pair-consistency: both degenerate strategies fail, and they fail differently

Matched pairs of households identical except for whether p1 declares a qualifying
disability (`is_permanently_disabled_veteran`, which establishes elderly-or-disabled status
for SNAP under 7 CFR 271.2 without adding income).

Each split carries **200 pairs, exactly half of which ground truth separates** — a
`--pair-differ-fraction` recorded in the manifest as target *and* achieved, not whatever the
sampler happened to produce. A pair counts as consistent only when the model's difference
**pattern** matches ground truth's, so both giving identical answers to a pair that differs
and inventing a difference in a pair that does not are failures.

| strategy | pair consistency |
|---|---|
| never differ | **0.495** |
| always differ | **0.380** |
| ceiling | **1.000** |

Never-differ lands at chance, as a 50/50 split implies. **Always-differ scores *below*
chance, by design.** Matching the shape of the difference is required, not merely its
presence, so an invented difference fails on the half where truth does not move *and* on
much of the half where it does. Guessing "these should differ" is worse than never guessing.

This is reported over 200 pairs rather than the 40 used during development: 40 carries
roughly ±8pp, too coarse to separate 0.495 from 0.380. An earlier build selected pairs only
on "adult with shelter costs", ground truth differed in 4 of 40, and never-differ banked
0.900 — the metric's stated property was false as measured until the ratio became a target.

## The splits

| | dev | held-out |
|---|---|---|
| tasks | 1,200 | 1,200 |
| determinate | 780 (65.0%) | 780 (65.0%) |
| indeterminate | 180 (15.0%) | 180 (15.0%) |
| incomplete-determinate | 144 (12.0%) | 144 (12.0%) |
| eligibility-flip | 96 (8.0%) | 96 (8.0%) |
| matched pairs | 200 (100 differ / 100 same) | 200 (100 differ / 100 same) |
| seed | `20260828`, **public** | private, fingerprint `b80dea37628d57fe` |

Class mix is a construction, not an observation: candidates are generated in index order,
probed, and accepted into whichever bucket they land in until it is full. The manifest
records how many candidates were consumed to reach it.

**Contamination control.** Every task carries `Task.hash`, the library's content hash of its
wire data — the hashes are safe to publish, the task data is not. Dev and held-out share
**zero** task hashes. The held-out split is generated from a seed in a gitignored `.env`,
is never committed, and its manifest records the seed's fingerprint rather than the seed.
Publishing a held-out number goes through a redaction step that strips the seed and every
seed-derived identifier, checked by scanning the serialised text rather than a field
checklist.

## What exists

| component | what it does |
|---|---|
| `redtape/schemas.py` | Households and answers. Every answer field tags its period; a fact is present or explicitly withheld, never absent. |
| `redtape/oracle/policyengine_oracle.py` | The only code that touches PolicyEngine. Refuses to answer a household with a withheld fact rather than let the engine substitute a default. Attaches provenance to every value. |
| `redtape/oracle/determinability.py` | Perturbation prober. Sweeps a withheld fact across a declared range and labels the case determinate / indeterminate / incomplete-but-determinate. |
| `redtape/oracle/takeup.py` | Suppresses imputed programme take-up while passing declared receipt through. The invariant, not the declared list, is the guard. |
| `redtape/generator/` | Seeded generator and narrative renderer. Reproducible from `(seed, index)` alone. |
| `redtape/envs/t1_eligibility.py` | The `verifiers.v1` environment. Format compliance and degenerate-answer detection are a pass/fail **gate** in front of scoring, not a weighted component. |
| `redtape/scoring/` | Scorers, plus a build-time invariant asserting no answer key expects something outside `SCORED_PROGRAMS`. |
| `redtape/config.py` | Seed policy. The held-out path **fails closed**: no default, no override, separate variable name from the public dev seed. |
| `eval/` | Five baselines, two pair diagnostics, three tool conditions, the three headline metrics, and a redacting results writer. |
| `scripts/build_split.py` | Builds a split to a targeted class mix and pair ratio, bakes answer keys once, records `Task.hash` per task. |
| `rules/verification_requirements.yaml` | **Ten-rule seed set only**, to prove the format. Not the finished table. |

## Reproduce

Requires **WSL2 / Linux** and **Python 3.13** — `verifiers.v1` cannot import on Windows at
all (unguarded `import fcntl`), and the determinism claims depend on the pinned interpreter.

```bash
uv sync --extra dev

# 242 tests. Run as a module or bare `pytest`; both work, and CI runs the bare form.
./.venv/bin/python -m pytest

./.venv/bin/python scripts/smoke.py                     # one household, with provenance
./.venv/bin/python scripts/external_validation.py       # published-table comparisons
./.venv/bin/python scripts/describe_split.py data/dev/t1.jsonl

# The eval harness. Run it as a MODULE - `python eval/run_eval.py` puts eval/ itself on
# sys.path instead of the repo root and dies on ModuleNotFoundError.
./.venv/bin/python -m eval.run_eval baselines  --split data/dev/t1.jsonl
./.venv/bin/python -m eval.run_eval conditions --split data/dev/t1.jsonl --sample 60
./.venv/bin/python -m eval.run_eval perfect    --split data/dev/t1.jsonl   # ceiling check

./.venv/bin/python -m redtape.scoring.rules_lint rules/verification_requirements.yaml
```

CI (`.github/workflows/tests.yml`) runs lint and the full suite on Linux/3.13 on every push,
with `uv sync --frozen` so a lockfile disagreement fails rather than silently re-resolving.
It is currently green at **242 passed, 0 skipped** — the skip count is quoted deliberately,
because an earlier green run was "202 passed, 5 skipped" and the skips were invisible.

## Why the oracle needs continuous external checking

The methodological claim of this project is that a benchmark built on a policy engine is
only as good as its independent verification of that engine, and that the verification has
to be continuous rather than a one-time audit. Two findings are the evidence, and they are
the reason to trust the rest of the numbers rather than caveats on them.

**PolicyEngine does not implement HR 1's 2025 SUA changes for California.** The parameter
`gov.usda.snap.income.deductions.utility.always_standard` is `True` at every instant tested
across both the 2025-07-04 and 2025-10-31 effective dates, so every California household is
granted the full Standard Utility Allowance unconditionally — including the non-elderly,
non-disabled households the published rules withdraw it from. The over-statement is bounded
by 30% of the allowance, so roughly $194–$199/month. Reported upstream in
[`docs/HR1_SUA_DIVERGENCE.md`](docs/HR1_SUA_DIVERGENCE.md), with evidence, affected month
ranges and a proposed fix, and **filed upstream as
[PolicyEngine/policyengine-us#9374](https://github.com/PolicyEngine/policyengine-us/issues/9374)**. **Consequence adopted:** formula-validation cases are restricted
to months before 2025-07-04, and no T1b case is generated that turns on SUA entitlement in
the affected window. `docs/LIMITS.md` §11.

**HR 1's immigrant-eligibility restrictions are not modelled either, and that one changes
eligibility rather than amounts.** Refugees, asylees, people with deportation withheld,
conditional entrants and one-year parolees are still modelled as fully eligible, identical
to citizens, with no change at the statutory boundary. This one had already touched
generated answer keys. **Consequence adopted:** the corpus is *restricted*, not annotated —
generation and the determinability sweep are limited to statuses where engine and published
rules agree, and previously-generated refugee and asylee households were removed. A test
asserts the current known-wrong engine behaviour so that an upstream fix notifies us to
re-widen. `docs/LIMITS.md` §16.

Neither is a criticism of PolicyEngine, and the report says so at length. The engine is
accurate, current and well-sourced nearly everywhere we looked — HR 1's ABAWD provisions
are implemented in detail, down to citing the specific CDSS letter for California's delayed
adoption. That is exactly what makes a narrow, undocumented gap expensive: a downstream user
has every reason to trust it and no signal telling them where not to. An oracle that is
right 99% of the time and silent about the other 1% is the failure mode this project is
built to detect, and we found it in our own oracle first.

Continuous rather than one-time: `tests/test_parameter_drift.py` asserts engine parameters
against externally published figures and fails the build on divergence, and it deliberately
asserts the *known-wrong* HR 1 behaviour so that an upstream fix breaks the test.

## Four more things worth knowing before reading the code

1. **An annual query on a monthly `stock` variable returns December alone.**
   `is_snap_eligible` is such a variable, so asking it for "2025" silently reports December
   — not any month, not all months. SNAP is therefore always queried at an explicit month
   and always scored monthly. Locked by a regression test. `docs/LIMITS.md` §1.

2. **PolicyEngine has no representation of "unknown."** Every omitted fact silently becomes
   a plausible default (`employment_income`→0, `age`→40, `state_name`→CA,
   `immigration_status`→CITIZEN). Omitting a fact and stating it as zero are
   indistinguishable to the engine. This is why abstention labels come from a perturbation
   prober rather than from the oracle — and the labels are an under-approximation, able to
   prove a fact is deciding but not that it isn't. `docs/LIMITS.md` §3, §4.

3. **Only externally validated programs are scored.** `SCORED_PROGRAMS` is
   `("snap", "eitc", "ctc")`. **Medicaid is computed and recorded but NOT scored** — no
   external validation was obtainable, and scoring a cell backed only by the engine agreeing
   with itself is the circularity this project exists to avoid. That costs T1 its only
   per-person eligibility output, stated plainly rather than papered over. A related
   correction: `ctc` is the **gross** credit, not what the household receives — a zero-income
   family with two children has `ctc = 4,400` and `ctc_value = 0`. The scored answer uses
   `ctc_value`. `docs/LIMITS.md` §20, §21.

4. **SNAP validation is narrower than any headline count suggests.** 22 comparisons against
   published CalFresh tables match exactly, but only **9 exercise calculation logic**; 8 test
   parameter loading and 5 are direct parameter reads. Reported by kind for that reason.
   Medicaid and every eligibility boolean have had no external comparison of any kind.
   `docs/LIMITS.md` §7.

## Rules table confidence

10 rules · high **0** · medium **6** · low **4** · scored (excludes `low`) **6**

Three of the ten seed citations were wrong — a 30% error rate — and only one had been
flagged as doubtful in advance. The other two were believed correct and were not; reading
the actual section text found them. Consequently every rule now requires its primary source
opened and read before the rule is written, and `medium` no longer means "probably right"
but "the cited section has been read and matches". `docs/LIMITS.md` §10.

No rule is at `high`. Claude drafts rules and never promotes their confidence; only the
human reviewer does, via `rules/REVIEW_CHECKLIST.md`.

## License

Apache-2.0.
