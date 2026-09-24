# Redtape — verifiable abstention for public-benefits determinations

> ## Retraction (2026-09-21): the category/quantity finding was an artifact
>
> This environment's headline claim — that a frontier model notices a missing fact far more
> reliably when its absence changes a **category** than when it changes a **quantity**,
> measured at 0.396 against 0.050 — **is retracted.** It does not hold on the corrected
> corpus, on either model tested.
>
> **The reason is a design defect, not a measurement error.** The generator routes each
> withheld fact to whichever stream can produce a given class, so **class and withheld fact
> are entangled by construction**: 55% of the eligibility-flip class is a single fact
> (`p1.employment_income`, 53 of 96), and that fact has **one task of 180** in the
> indeterminate class. The comparison was never between "category" and "quantity" — it was
> between two different mixtures of facts. **No model run on this design can answer the
> question**, and the original 0.396/0.050 is plausibly the same confound. Holding the fact
> constant, the flip class does *worse* in five strata of six (pooled difference −0.153).
>
> A design that could test the claim needs each fact to appear in both classes in balanced
> proportions. That is scoped in `docs/LIMITS.md` §44 and **not built**.
>
> **What the corrected corpus does support, across two labs' models** — both measured on the
> same 1,194 tasks, the full split:
>
> | | Claude Opus 5 | GPT-5.6 Sol |
> |---|---|---|
> | abstention accuracy | 0.624 [0.577, 0.669] | **0.793** [0.752, 0.829] |
> | exact-match (determinate) | 0.621 [0.587, 0.655] | 0.627 [0.592, 0.660] |
> | pair-consistency | 0.636 (n=198) | 0.675 (n=200) |
> | named the missing fact in exact form | **100%** (156/156) | 93.2% (220/236) |
> | named a fact that was never withheld | **0%** | 7.6% (18) |
>
> GPT-5.6 Sol is better calibrated about when it cannot answer. Claude Opus 5 is better at
> saying *which* fact is missing and never invents one. **The two abilities are
> independent** — a benchmark measuring only format compliance would rank these models the
> other way round.
>
> **A third model, Claude Opus 5.5** (1,200 of 1,200, same corpus and configuration), closed
> most of the abstention gap: **0.745** [0.701, 0.785], up 0.121 on Opus 5 (paired 95% CI
> [+0.084, +0.159]) and 0.048 below GPT-5.6 Sol (paired [−0.095, −0.001], McNemar p = 0.059;
> the unpaired interval includes zero). That suggests the Opus 5 vs GPT-5.6 Sol difference was
> mostly generational. **Whether a lab-level residual remains is unresolved.** It still names
> the missing fact in exact form every time (187/187) and never invents one.
>
> Its exact-match fell to 0.563, and the drop is a **temporal rule-version error**: 89 SNAP
> answers sit exactly $10 above the key because Opus 5.5 applies the FFY2026 maximum
> allotments to FFY2025 months. Opus 5 made the same kind of error the other way round,
> applying the pre-2025 $2,000 child tax credit where PL 119-21 sets $2,200 (exactly $200 short
> per child, 54 times). The keys are right in both cases. A model that knows a rule's value but
> not the dates it is in force is off by exactly the size of the rule change. It is the
> clearest evidence so far for rulebooks versioned by date. No corrected exact-match figure is
> claimed; the account is in `docs/LIMITS.md` §45.
>
> **This version ships the corrected corpus.** Every premise an answer key depends on is
> stated in the case file — relationships and filing structure, weekly hours, Social
> Security status, heating and cooling costs — and a test fails if that stops being true.
> The v0.1.0 corpus defects (two-adult households keyed as married couples, undocumented
> filers keyed as holding SSNs, students keyed at zero work hours) are recorded in
> `docs/LIMITS.md` §35–§36. **Every result measured on v0.1.0 is superseded. Please do not
> cite it.**
>
> Nothing here is a PolicyEngine defect. The statutes were read (IRC §32(c)(1)(E) and §32(m),
> §24(h)(7) as amended by PL 119-21, 7 CFR 273.5(b)); the engine matches them. Each wrong key
> came from an input our own oracle left unset, so the engine supplied a default.
>
> The pre-registered decision rule, the result reported exactly as that rule dictates
> (+0.126, CI [+0.004, +0.245], p = 0.056, on the full 1,200-task split the rule required),
> and the disclosed reasons for departing from it are in `docs/LIMITS.md` §43.

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
  class is what stops "always abstain" from winning — it scores 0.000.
- **Paired tasks.** 200 pairs differ in exactly one attribute, half of which should change
  the answer and half of which should not, so a model cannot score well by being uniformly
  cautious or uniformly confident.

## What the metrics do

Three headlines, reported separately. The weighted composite exists but is deliberately
**not** the headline, so retuning a weight cannot move a published number.

Corrected corpus, full dev split. Each model row is that model's own run (exact-match n is
776, 780 and 778 respectively, from the tasks each answered); every baseline covers all 780.

| | exact-match | abstention (n=420) | pair-consistency |
|---|---:|---:|---:|
| **Claude Opus 5** | **0.621** | **0.624** | **0.636** (198 pairs) |
| **Claude Opus 5.5** | **0.563** | **0.745** | **0.705** (200 pairs) |
| **GPT-5.6 Sol** | **0.629** | **0.793** | **0.675** (200 pairs) |
| always_abstain | 0.000 | 0.000 | 0.000 |
| never_abstain | 0.194 | 0.340 | 0.480 |
| always_eligible | 0.041 | 0.343 | 0.500 |
| never_eligible | 0.123 | 0.083 | 0.085 |
| rules_only | 0.194 | 0.376 | 0.480 |
| *ceiling agent* | *1.000* | *1.000* | *1.000* |

GPT-5.6 Sol's exact-match is 0.629 here, over the 778 determinate tasks its own run answered.
The notice above gives 0.627, computed on the 774 determinate tasks that both it and Claude
Opus 5 answered, so the two models are compared on identical tasks. Both are correct for
their task sets.

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

## Superseded result (v0.1.0 corpus): retracted, do not cite

**Kept for the record only. This is the finding the notice at the top retracts.** It was
measured on the v0.1.0 corpus and does not hold on the corrected one. The current results
are in the notice above.

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
