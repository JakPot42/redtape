# Redtape v0

Verifiable training-and-evaluation environments for US public-benefits work.

**Status: Checkpoint 2 complete.** Two 1,200-task splits built, five baselines and three
tool conditions run, three headline metrics confirmed both discriminating and achievable.
**Nothing here is published or peer-reviewed**, and the validated surface is narrower than
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

## The result: abstention measures judgment, not arithmetic

Three conditions over the same tasks. `tool_equipped` gives the agent a calculator that
takes a structured household and returns the benefit. `tool_equipped_unknowns` gives it the
same calculator, except a fact may be passed as `"unknown"` — instead of defaulting it, the
tool sweeps that fact and reports which programs its value decides.

| condition | exact-match (n=34) | abstention (n=26) |
|---|---|---|
| `tool_less` | 0.176 | 0.385 |
| `tool_equipped` | **0.618** | 0.346 |
| `tool_equipped_unknowns` | 0.618 | **0.846** |

**The calculator moves exact-match by +0.44 and leaves abstention where it was. Marking
withheld facts moves abstention by +0.50 and leaves exact-match exactly where it was.**

The two axes separate cleanly. Arithmetic help does not buy abstention accuracy, and
determinability help does not buy arithmetic accuracy. That is direct evidence the
abstention metric measures something the amount-scoring benchmarks do not, which is the
whole premise of the project — and it is the single finding that could have shown the
premise was empty. It didn't.

Caveats, because the number is small: this is a 60-task stratified sample, so n=34 and
n=26, and one task is worth 2.9 and 3.8 points respectively. The 0.385 → 0.346 dip on
`tool_equipped` is one task and should be read as flat, not as a decline. The agent is a
scripted stand-in, not a language model — it reads the case file and calls the tool, which
is what makes it an upper bound on the *tool* rather than a measurement of any model.

## Baselines: no trivial strategy exceeds 0.50

Full 1,200-task dev split. Three headline metrics, reported separately; the weighted
composite exists but is explicitly **not** the headline, so that retuning a weight cannot
move a published number.

| baseline | exact-match | abstention | pair-consistency |
|---|---|---|---|
| `always_abstain` | 0.000 | **0.131** | 0.000 |
| `never_abstain` | 0.205 | 0.336 | 0.495 |
| `always_eligible` | 0.036 | 0.343 | 0.500 |
| `never_eligible` | 0.115 | 0.074 | 0.060 |
| `rules_only` | 0.205 | 0.326 | 0.495 |
| *ceiling (answers and abstains correctly)* | *1.000* | *1.000* | *1.000* |

`always_abstain` scoring **0.131** is the design working: a benchmark about knowing when to
abstain is worthless if abstaining always is a winning strategy. The
incomplete-but-determinate class — a fact is missing, but the outcome does not turn on it,
so the model should answer anyway — is what punishes it.

The ceiling row matters as much as the baselines. It is a diagnostic agent that answers
from the key *and* abstains on exactly the deciding programs, and it exists because a metric
nobody can score 1.000 on is broken. Until it was written, nothing established that the
abstention metric was reachable at all.

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
