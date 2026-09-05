# Redtape — verifiable abstention for public-benefits determinations

**Does the agent know when a required fact is missing?** Most benefit-calculation evals score
whether the number is right. This one scores whether the agent notices it *cannot* produce a
number, and says so.

Each task is a household narrative that withholds exactly one fact. Ground truth comes from
[PolicyEngine](https://github.com/PolicyEngine/policyengine-us), a real microsimulation
engine used for actual policy analysis — not from another model's opinion.

## Installs in seconds — no microsimulation engine required

This is worth stating before anything else, because it is the difference between an
environment people try and one they mean to try.

**Evaluation never touches PolicyEngine.** Answer keys are computed once, at generation time,
and baked into the split; nothing on the scoring path imports the engine. So installing this
environment does not pull a microsimulation engine, its dependency tree, or its parameter
database. `policyengine-us` is an optional `[generate]` extra, needed only if you want to
build a *new* split from a different seed or tax year.

The runtime dependencies are `verifiers` and `pydantic`. That is the whole list.

```bash
prime env install jakpotvin/redtape@latest
```

```python
from redtape import T1Taskset
from redtape.envs.t1_eligibility import T1Config

taskset = T1Taskset(T1Config())   # 1,200 tasks ship with the package
```

No API key is needed to load the tasks or to score an answer. The installed package is the
taskset, oracle and scoring; the baselines and test suite ship in the environment *source*
(`prime env pull`, or the GitHub repo) and also need no key.

## What makes it different

- **Deterministic ground truth, no LLM judge.** Answer keys trace to named PolicyEngine
  variables. Rewards are computed, not rated, so the same response scores the same way
  every time — enforced by a golden-master determinism test in CI.
- **Abstention is scored in both directions.** Every task withholds one fact, labelled into
  three classes: the fact decides the outcome (abstaining is correct), the fact is missing
  but does *not* decide it (**answering** is correct), or nothing is withheld. That middle
  class is what stops "always abstain" from winning — it scores 0.131.
- **Paired tasks.** 200 pairs differ in exactly one attribute, half of which should change
  the answer and half of which should not, so a model cannot score well by being uniformly
  cautious or uniformly confident.

## What the metrics do

Three headlines, reported separately. The weighted composite exists but is deliberately
**not** the headline, so retuning a weight cannot move a published number.

| | exact-match (n=780) | abstention (n=420) | pair-consistency (200 pairs) |
|---|---:|---:|---:|
| **Claude Opus 5** | **0.514** | **0.438** | **0.570** |
| always_abstain | 0.000 | 0.131 | 0.000 |
| never_abstain | 0.205 | 0.336 | 0.495 |
| always_eligible | 0.036 | 0.343 | 0.500 |
| never_eligible | 0.115 | 0.074 | 0.060 |
| rules_only | 0.205 | 0.326 | 0.495 |
| *ceiling agent* | *1.000* | *1.000* | *1.000* |

No trivial strategy exceeds 0.50 on any headline, and the ceiling agent proves every metric
is actually achievable — until it was written, nothing established that the abstention metric
was reachable at all. All six are scripted rather than model-driven, so they cost nothing and
need no API key, and they reproduce the numbers above exactly.

One caveat, since the point of this README is not to overstate the engine-free claim: the
*baselines* do read the federal poverty line from PolicyEngine, so running them needs
`pip install -e ".[dev,generate]"` from the source tree. The engine-free property belongs to
the **evaluation path** — loading tasks, prompting, parsing and scoring — which is what an
installed `prime env install` gives you, and which was verified in a clean environment with
`policyengine_us` absent.

## The result

Claude Opus 5, 1,200 tasks, no tools:

| the withheld fact would change… | correct abstention |
|---|---:|
| SNAP eligibility — a **category** | **0.396** (38 / 96) |
| a benefit amount — a **quantity** | **0.050** (9 / 180) |

An eight-fold gap between two classes that differ in one respect: whether the missing fact
moves a yes/no or a number.

It is not incapacity at the task — exact-match is 0.514 against a 0.205 best baseline. And it
is not blanket caution — where the missing fact does *not* decide the outcome, so answering
is correct, it answers 95.1% of the time. It is discriminating, on the wrong axis.

## Scope, and what is not validated

One model, one state (California), one tax year, one prompt. Abstention labels come from a
perturbation sweep, which can prove a fact is deciding but cannot prove one is not.
Medicaid is computed but deliberately **unscored** — no external validation was obtainable
for it. A held-out split exists, has never been evaluated against, and stays that way.

**Read [`docs/LIMITS.md`](docs/LIMITS.md) before citing any number from this environment.**
It is 30 sections, written as the work happened rather than retrofitted, and three of them
retract errors found in my own published results — including a schema bug that was
penalising exactly the behaviour the benchmark exists to reward, and which sat visible in
every report for two days before it was read correctly.

---

Source, full README, and the raw results files:
**https://github.com/JakPot42/redtape** · Apache-2.0 · 255 tests, CI green
