# Redtape v0

> ## Retraction (2026-09-21): the category/quantity finding was an artifact
>
> This project's headline claim — that a frontier model notices a missing fact far more
> reliably when its absence changes a **category** than when it changes a **quantity**,
> measured at 0.396 against 0.050 — **is retracted.** It does not hold on the corrected
> corpus, on either model tested.
>
> **The deepest reason is a design defect, not a measurement error.** The generator routes
> each withheld fact to whichever stream can produce a given class, so **class and withheld
> fact are entangled by construction**: 55% of the eligibility-flip class is a single fact
> (`p1.employment_income`, 53 of 96), and that fact has **one** task of 180 in the
> indeterminate class. The
> comparison was never between "category" and "quantity". It was between two different
> mixtures of facts. **No model run on this design can answer the question**, and the
> original 0.396/0.050 is plausibly the same confound rather than a real effect.
>
> Hold the withheld fact constant and the difference reverses: the flip class does *worse*
> in five strata of six (Mantel-Haenszel pooled difference −0.153). The aggregate that
> appeared to support the claim on Opus 5 is Simpson's paradox.
>
> **What the corrected corpus does support**, across two labs' models and reported in full
> below: GPT-5.6 Sol abstains correctly more often than Claude Opus 5 (0.793 against 0.624,
> intervals disjoint), while Opus 5 names the missing fact in the exact form asked for 100%
> of the time and never names a fact that was not withheld, against GPT-5.6 Sol's 7.6%
> confabulation rate. **Format compliance and calibration are independent.**
>
> **A third model, Claude Opus 5.5** (1,200 of 1,200, same corpus and configuration), closed
> most of the abstention gap: 0.745 [0.701, 0.785], up 0.121 on Opus 5 (paired 95% CI
> [+0.084, +0.159]) and 0.048 below GPT-5.6 Sol (paired [−0.095, −0.001], McNemar p = 0.059;
> the unpaired interval includes zero). That suggests the Opus 5 vs GPT-5.6 Sol difference was
> mostly generational. **Whether a lab-level residual remains is unresolved.** Its exact-match
> fell, and that drop is a temporal rule-version error, described below.
>
> Corpus defects found and fixed along the way (two-adult households keyed as married
> couples, undocumented filers keyed as holding SSNs, students keyed at zero work hours) are
> recorded in [`docs/LIMITS.md`](docs/LIMITS.md) §35–§36. The corrected corpus states every
> premise its answer keys depend on, and a test fails if that stops being true.
>
> The superseded numbers are kept below under *Superseded results*, labelled. **Do not cite
> them.** Full account, including the pre-registered decision rule and the disclosed
> deviation from it: [`docs/LIMITS.md`](docs/LIMITS.md) §43.

Verifiable training-and-evaluation environments for US public-benefits work.

**Published on the Prime Intellect Environments Hub:
[`jakpotvin/redtape`](https://app.primeintellect.ai/dashboard/environments/jakpotvin/redtape)**

```bash
prime env install jakpotvin/redtape@latest
```

```python
from redtape import T1Taskset
from redtape.envs.t1_eligibility import T1Config

taskset = T1Taskset(T1Config())   # 1,200 tasks ship with the package
```

It installs in seconds and needs no API key to load tasks or score an answer. **Evaluation
never touches PolicyEngine** — answer keys are computed once at generation time and baked
into the split, so nothing on the scoring path imports the engine. `policyengine-us` is an
optional `[generate]` extra, needed only to build a *new* split; the runtime dependencies
are `verifiers` and `pydantic`, and that is the whole list. (The five baselines are the one
exception: they read the federal poverty line from the engine, so running them from a
checkout needs `pip install -e ".[dev,generate]"`.)

**Status: the category/quantity claim is RETRACTED (2026-09-21) after both models were
measured on the rebuilt corpus. The original Opus 5 results were withdrawn on 2026-09-19
and the splits rebuilt on 2026-09-20; see the notice above and `docs/LIMITS.md` §43.** The withdrawn Opus 5 numbers
were measured on a corpus whose answer keys rest on premises the case files never state, and
in several cases get the law wrong:

- **Two adults are keyed as a married couple filing jointly**: 393 of 1,200 dev tasks, 163
  of them adults 18+ years apart (a parent and adult child keyed as spouses). The narratives
  never state a relationship.
- **Students are keyed as working zero hours** even when the case file states their earnings
  (81 tasks). The SNAP student exemption turns on hours, and the eligibility-flip class is
  built on student status.
- **Undocumented people are keyed as holding a citizen's SSN card**, so the key credits them
  EITC and CTC (97 and 110 tasks with an undocumented person present).

A model that asked for the missing relationship was scored as abstaining needlessly. The
benchmark penalised the behaviour it exists to reward. See
[`docs/LIMITS.md`](docs/LIMITS.md) §35–§36. The previous numbers are kept below under
*Superseded results*, for the record only. **Do not cite them.**

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
wearing a different hat. The withdrawn Opus 5 result was offered as the answer to that
objection, and it is retracted. What the corrected corpus shows instead is that the two
abilities come apart between models: on the same 1,194 tasks, Claude Opus 5 and GPT-5.6 Sol
compute amounts equally well (0.621 against 0.627) and judge answerability very
differently (0.624 against 0.793). That is evidence the second question is not the first
one restated — but it is a two-model observation, not a law.

## Results: the claim is retracted, and three findings survive

The previous headline ("it notices a missing *category*, not a missing *quantity*": Opus 5
abstention 0.396 on eligibility flips against 0.050 on amount changes) was measured on the
contaminated corpus described above. **It is retracted.**

Two models have now been measured on the corrected corpus: **GPT-5.6 Sol** (1,198 of 1,200
tasks) and **Claude Opus 5** (1,196 of 1,200). Every comparison below is computed on the
**1,194 tasks both models answered**, so model-to-model differences do not depend on which
tasks were reached. The pre-registered decision rule required the full 1,200-task split,
and that precondition is now met.

### Why the claim cannot be tested on this design

The generator picks, for each task, a fact to withhold and a class to produce — and it
routes each fact to the stream where that fact *can* produce that class. The result is that
class and fact are not independent:

| withheld fact | eligibility-flip | indeterminate | difference |
|---|---|---|---:|
| `p1.immigration_status` | 0.000 (0/14) | 0.340 (17/50) | −0.340 |
| **`p1.employment_income`** | **0.774 (41/53)** | **0.000 (0/1)** | **+0.774** |
| `p1.is_higher_ed_student` | 0.071 (1/14) | 0.079 (3/38) | −0.008 |
| `dependent_care_cost` | 0.000 (0/4) | 0.147 (5/34) | −0.147 |
| `housing_cost` | 0.778 (7/9) | 1.000 (29/29) | −0.222 |
| `p1.age` | 0.500 (1/2) | 0.607 (17/28) | −0.107 |

*(Claude Opus 5, abstention accuracy, full split.)* `p1.employment_income` is **55% of the
flip class** (53 of 96) and has **one task of 180** in the indeterminate class. So for the
fact that dominates one side of
the comparison, the other side barely exists. Aggregating across this table compares two
different mixtures of facts and calls the difference a class effect.

**Fixing it requires a design where each fact appears in both classes in balanced
proportions.** That is a v1 item, scoped in [`docs/LIMITS.md`](docs/LIMITS.md) §44 and not
built.

### The pre-registered result, and the disclosed deviation

The decision rule was fixed before the deciding run
([`docs/CORRECTION_DRAFT.md`](docs/CORRECTION_DRAFT.md)). **Reported exactly as it
dictates:** on Claude Opus 5 the flip class exceeds the indeterminate class, **0.521
(50/96) against 0.394 (71/180), difference +0.126, 95% CI [+0.004, +0.245], Fisher exact
p = 0.056**. On GPT-5.6 Sol it does not: −0.008, CI [−0.123, +0.101], p = 0.891. The
interval excludes zero by 0.004 while the p-value does not clear 0.05, so by the letter of
the rule this is ambiguous — it was cleanly "reproduces on one model" at 73% of the split,
and completing the split moved it toward the null.

**We are not publishing that conclusion, and this paragraph is why.** Stratifying by
withheld fact — a check required by a standing rule that predates the experiment — reverses
the sign: pooled difference **−0.153**, flip worse in five strata of six, and **−0.187,
CI [−0.307, −0.029]** with the dominant fact removed. The deviation is disclosed rather than
silent because pre-registration forbids undisclosed deviation, not deviation. The full
justification is written out in [`docs/LIMITS.md`](docs/LIMITS.md) §43: the sign reverses
rather than a magnitude shrinking; p = 0.056 across two models is fragile to a single
reclassified task, which this run demonstrated rather than predicted when the last 20
flip and indeterminate tasks moved it from 0.041; the stratification was required by a
standing rule that predates the experiment; and the deviation runs *against* the more
publishable conclusion, so the motivated reasoning pre-registration guards against pushes
the other way.

### What survives, across two labs

| | Claude Opus 5 | GPT-5.6 Sol |
|---|---|---|
| abstention accuracy | 0.624 (262/420) [0.577, 0.669] | **0.793** (333/420) [0.752, 0.829] |
| exact-match (determinate) | 0.621 (481/774) [0.587, 0.655] | 0.627 (485/774) [0.592, 0.660] |
| pair-consistency | 0.636 (n=198) | 0.675 (n=200) |
| named the fact in exact form | **156/156 (100%)** | 220/236 (93.2%) |
| named a fact never withheld | **0 (0%)** | 18 (7.6%) |
| truncated responses | 0 / 1,198 | 1 / 1,228 |

1. **GPT-5.6 Sol is better calibrated about when it cannot answer** — 0.793 against 0.624,
   intervals disjoint.
2. **Claude Opus 5 is better at saying *which* fact is missing** — perfect compliance with
   the requested identifier format, and it never invents a missing fact. GPT-5.6 Sol
   confabulates one in 7.6% of its abstentions, mostly on tasks where nothing was missing.
3. **Those two abilities are independent.** The model that always names the fact correctly
   is the worse judge of whether a fact is missing at all. A benchmark that measured only
   format compliance would rank these models the opposite way round.

These are cross-lab results on a corpus whose premises are all stated, and they do not
depend on the retracted claim.

### A third model: Claude Opus 5.5, and a rule-version error in both generations

Claude Opus 5.5 was run on the same 1,200 tasks with every configuration field identical to
the Opus 5 run, so the model is the only variable. The question it answers is whether the
abstention gap between Opus 5 and GPT-5.6 Sol belongs to the labs or to the generations.

| | Claude Opus 5.5 |
|---|---|
| abstention accuracy | 0.745 (313/420) [0.701, 0.785] |
| exact-match (determinate) | 0.563 (439/780) [0.528, 0.597] |
| pair-consistency | 0.705 (n=200) |
| named the fact in exact form | 187/187 (100%) |
| named a fact never withheld | 0 (0%) |
| truncated responses | 0 / 1,200 |

On identical tasks, Opus 5.5's abstention is **+0.121** against Opus 5 (paired 95% CI
[+0.084, +0.159]) and **−0.048** against GPT-5.6 Sol (paired CI [−0.095, −0.001], McNemar
p = 0.059; the unpaired Newcombe interval, [−0.104, +0.009], includes zero). All of the gain
is on tasks where abstaining is correct; on tasks where answering is correct it stays at
0.979. So it is not abstaining more across the board.

**It closed most of the gap, which suggests the Opus 5 vs GPT-5.6 Sol difference was mostly
generational.** Whether a lab-level residual remains is unresolved: the remaining difference
excludes zero by 0.001 on one method and not on the other, and is read as neither.

**Its exact-match fell**, to 0.563 against Opus 5's 0.621 on identical tasks (paired CI
[−0.093, −0.018]). Before attributing that to the model we checked the scorer and the corpus.
It is not abstention (Opus 5.5 abstained on no determinate task), not format (no period-label
errors, one parse failure), and not eligibility. It is SNAP amounts, and only in January to
September 2025. **89 answers sit exactly $10 above the key**: for two-person households the
key reads $536 and Opus 5.5 reads $546. Those are the FFY2025 and FFY2026 maximum allotments,
both taken from the externally sourced table `tests/test_parameter_drift.py` checks, so the
key is right. **Opus 5.5 applies next fiscal year's maximums to this fiscal year's months.**

Opus 5 made the same kind of error the other way round. **54 of its CTC misses are exactly
$200 short per qualifying child**: the pre-2025 $2,000 credit, where PL 119-21 sets $2,200.
Opus 5.5 has none of those. One model applied last year's law and the other next year's
figures. A system that knows a rule's value but not the dates it is in force answers
confidently and is off by exactly the size of the rule change. **This is the clearest
evidence so far for rulebooks versioned by date.**

No corrected exact-match figure is given. Removing one error after the fact is a
decomposition, not a result. Full account: [`docs/LIMITS.md`](docs/LIMITS.md) §45.
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
# --extra generate is required: policyengine-us is an OPTIONAL extra as of the Hub
# packaging, and without it the oracle tests and the five baselines cannot run.
uv sync --extra dev --extra generate

# 255 tests. Run as a module or bare `pytest`; both work, and CI runs the bare form.
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

**We also reported HR 1's immigrant-eligibility restrictions as unmodelled. That half was
wrong, and it is retracted.** The restrictions *are* implemented: federally from
2025-07-01, with California delaying to 2026-04-01 per CDSS ACL 25-92, applied by
`ca_snap_immigration_status_eligible`. Our probe hardcoded `state_name: CA` and swept only
months of 2025 — the one state and the one year in which a correctly modelled state delay
is indistinguishable from a federal omission. The same probe pointed at any non-delaying
state would have shown the change at 2025-07 immediately. **Consequence adopted:** the
corpus restriction is removed and refugee and asylee households are generated again; the
corpus scope (`CORPUS_STATE`, `CORPUS_TAX_YEAR`) is now declared in code, and a test fails
if either moves while the gate still depends on California's delay. The one real gap that
survives is that COFA status has no enum value — already tracked upstream as
[#8296](https://github.com/PolicyEngine/policyengine-us/issues/8296), so not our finding.
`docs/LIMITS.md` §16 records the failure mode: a correct measurement generalised past the
scope it was taken at.

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

## Superseded results: withdrawn 2026-09-19, do not cite

Kept verbatim for the record. Every number below was measured on the corpus described at the
top of this README (unstated household relationships keyed as marriages, students keyed at
zero hours, undocumented filers keyed with a citizen's SSN) and scored by an abstention scorer
that ignored the named fact. The scripted-agent rows are consistent with those same answer
keys, so their *mechanics* hold, but their values are not re-verified.

### Result: it notices a missing category, not a missing quantity

**Claude Opus 5, 1,200-task dev split, no tools.**

> **It recognises a missing fact when the fact's absence would change a *category*, and
> largely misses one whose absence would change a *quantity*.**
>
> | the withheld fact would change… | correct abstention rate |
> |---|---:|
> | SNAP eligibility — a category | **0.396** (38 / 96) |
> | a benefit amount — a quantity | **0.050** (9 / 180) |
>
> Both classes are cases where a required fact is absent and abstention is the correct
> answer. They differ in one respect: whether the missing fact moves a yes/no or a number.
> **An eight-fold gap.**

That is the result. Two things frame it.

It is not incapacity at the task: the model computes benefits far better than any trivial
strategy, **0.514 exact-match against a 0.205 best baseline**.

And it is not blanket caution: where a fact is missing but does *not* decide the outcome — so
answering is correct — it answers **95%** of the time (0.951). It is not abstaining
indiscriminately and getting lucky; it is discriminating, on the wrong axis.

Across the whole split it volunteers `cannot_determine` in **5.3%** of responses (64 of
1,200). This line previously said 6.3%, a hand count that no definition over the stored
responses reproduces; see `docs/LIMITS.md` §34.

#### Three headline metrics

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

#### The aggregate hides the split

| class | correct | n | accuracy | correct behaviour |
|---|---:|---:|---:|---|
| indeterminate | 9 | 180 | **0.050** | abstain — the fact moves an amount past tolerance |
| eligibility-flip | 38 | 96 | **0.396** | abstain — the fact flips SNAP eligibility |
| incomplete-determinate | 137 | 144 | **0.951** | answer anyway — the fact does not decide |

An eight-fold gap between the two classes where abstention is required. Both are cases where
a required fact is absent; they differ only in whether its absence changes a *category* or a
*quantity*. The incomplete-determinate class is what stops the benchmark being won by always
abstaining — and 0.951 there confirms the model is not simply cautious.

#### Which missing facts go unnoticed

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

This is the category/quantity split seen fact by fact, and it suggests a mechanism for it.
A case file has an obvious slot for income; immigration status and age are background
premises a reader has to notice are *absent* rather than find blank. The facts the model
flags best are the ones with a slot; the ones it misses are the ones that must be inferred to
be missing.

The data is consistent with that and does not establish it. Separating "premise vs line item"
from "category vs quantity" needs a split that varies the same fact between the two
presentations — which this one does not do, because every fact appears in exactly one form.
It is the next experiment, not a conclusion.

#### The obvious objection, tested before publication

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

#### What this does and does not claim

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

**Read [`docs/LIMITS.md`](docs/LIMITS.md) before citing any number here.** 44 sections,
written as the work happened rather than retrofitted, stating what is *not* validated at
least as carefully as what is — including several sections retracting our own errors, of
which §43–§44 retract this project's headline claim.

**Reproducing it:** every model response is cached in `cache/responses/dev/` and committed,
so the scored artifact can be re-derived without spending anything. The runs on the
corrected corpus cost $32.92 (GPT-5.6 Sol), $60.43 (Claude Opus 5) and $36.22 (Claude
Opus 5.5, including $0.50 of probe responses reused from cache).

### The metric measures judgment, not arithmetic (scripted upper bound)

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

### Giving the model the tool: a calculator improves its abstention, unexpectedly

The section above measures what the tool offers a *perfect extractor*. This one asks the
question that matters for the finding: hand the tool to the model, and does anything change?

**This experiment is INCOMPLETE.** Two of three conditions ran before the API budget was
exhausted. The third — the one that would settle whether the model can act on an explicit
determinability signal — has not run, and the row below says so rather than being omitted.

| condition | exact-match | abstention |
|---|---:|---:|
| `tool_less` | 0.553 (n=150) | 0.453 (n=150) |
| `tool_equipped` | **0.927** (n=150) | **0.678** (n=149) |
| `tool_equipped_unknowns` | — **NOT RUN** — | — **NOT RUN** — |

*(A 15-task probe of the unrun condition gave 0.750 exact-match and 1.000 abstention on
n=8 / n=7. That is 7 tasks. It is recorded for transparency and is not a result.)*

#### Two findings, one of them unexpected

**A calculator nearly closes the arithmetic gap.** Exact-match goes 0.553 → 0.927, against a
ceiling of 1.000. Whatever the model gets wrong on determinate cases is almost entirely
computation, not comprehension of the case file — it knows what to compute and mis-computes
it.

**A calculator also improves abstention, 0.453 → 0.678.** This one was not predicted, and it
is the more interesting of the two.

It did not happen for the scripted extractor. Under the identical condition the scripted
agent's abstention was flat — 0.327 → 0.333, a change of 0.006 on the same 300-task sample.
So this is not a property of the tool. **Something about a model calling the tool makes it
likelier to notice that it cannot answer.**

The plausible mechanism, offered as a hypothesis: invoking the calculator requires naming
every required field explicitly. A fact that is absent has to be confronted at the point of
constructing the call, rather than glossed while writing prose. That would make the tool an
*attention* aid rather than an arithmetic one — and it is the same line-item-versus-premise
story the per-fact table suggests, reached from a different direction.

It is a hypothesis. The experiment that would test it is the unrun third condition, plus a
variant that requires the model to enumerate the fields it used without giving it a
calculator at all — separating "had to name the fields" from "had a tool".

#### What is not claimed here

- **Two conditions, not three.** The headline comparison this section was designed for —
  explicit `"unknown"` marking versus none — has not been run.
- `tool_equipped` abstention is n=149 rather than 150: one task's response was never
  fetched, and dropping it is preferable to scoring an absent reply as a failure.
- One model, one sample, one prompt. Same scope limits as the main result.

## License

Apache-2.0.
