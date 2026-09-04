"""Redtape: verifiable abstention evaluation for US public-benefits determinations.

The environment entry point is `T1Taskset`, exported here so `verifiers` can discover it.

What this measures that amount-scoring benchmarks do not: whether an agent notices that a
fact it needs is absent from the case file, and says so, instead of answering confidently
from incomplete information. Each task withholds exactly one fact and is labelled into one
of three classes -

  * the withheld fact decides the outcome        -> abstaining is correct
  * the withheld fact does NOT decide it         -> ANSWERING is correct
  * nothing is withheld                          -> answering is correct

The middle class is what stops "always abstain" being a winning strategy; it scores 0.131
on the abstention metric.

Ground truth comes from PolicyEngine, a real microsimulation engine, so answer keys are
deterministic rather than model-judged. There is no LLM judge anywhere in the scoring path.
Keys are baked at generation time and never recomputed during a rollout, which is why
evaluating needs no engine installed.
"""

from redtape.envs.t1_eligibility import T1Config, T1Data, T1Task, T1TaskConfig, T1Taskset

__all__ = ["T1Taskset", "T1Task", "T1Data", "T1Config", "T1TaskConfig"]
__version__ = "0.1.0"
