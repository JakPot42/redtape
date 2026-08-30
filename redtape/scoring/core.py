"""Scoring functions. No `verifiers` import - these take plain data and return floats.

Every scorer wraps its own body and returns a `Scored` carrying an explicit `error`
field. The rubric's own exception handling must never be what catches a bug here: a
crashed scorer that silently returns 0.0 is indistinguishable from a model that answered
wrong, and it fails *toward* "the model is bad" (CLAUDE.md).
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field

from redtape.schemas import SCORED_PROGRAMS, Determinability, T1Answer

TOLERANCE = 1.0


@dataclass(frozen=True)
class Scored:
    value: float
    detail: dict = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _guard(fn):
    """Any exception becomes an explicit scorer_error, never a silent zero."""

    def wrapper(*args, **kwargs) -> Scored:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return Scored(
                value=0.0,
                error=f"{type(exc).__name__}: {exc}",
                detail={"traceback": traceback.format_exc(limit=4)},
            )

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    return abs(a - b) <= tol


@_guard
def score_amounts(given: T1Answer, truth: T1Answer, tol: float = TOLERANCE) -> Scored:
    """Per-program amount match within tolerance. SNAP monthly, EITC/CTC annual."""
    checks = {
        "snap": (given.snap.benefit, truth.snap.benefit),
        "eitc": (given.eitc.amount, truth.eitc.amount),
        "ctc": (given.ctc.amount, truth.ctc.amount),
    }
    hits = {k: _close(g, t, tol) for k, (g, t) in checks.items() if k in SCORED_PROGRAMS}
    return Scored(
        value=sum(hits.values()) / len(hits) if hits else 0.0,
        detail={"per_program": hits, "given": {k: v[0] for k, v in checks.items()},
                "truth": {k: v[1] for k, v in checks.items()}},
    )


@_guard
def score_eligibility(given: T1Answer, truth: T1Answer) -> Scored:
    """Eligibility booleans, exact. Medicaid is excluded - it is not scored (LIMITS 20)."""
    hits = {"snap": given.snap.eligible == truth.snap.eligible}
    return Scored(value=sum(hits.values()) / len(hits), detail={"per_program": hits})


@_guard
def score_periods(given: T1Answer, truth: T1Answer) -> Scored:
    """The period tags must match. A right number for the wrong period is wrong."""
    hits = {
        "snap": given.snap.period_label == truth.snap.period_label,
        "eitc": given.eitc.period_label == truth.eitc.period_label,
        "ctc": given.ctc.period_label == truth.ctc.period_label,
    }
    return Scored(value=sum(hits.values()) / len(hits), detail={"per_field": hits})


@_guard
def score_abstention(
    given: T1Answer, truth_label: Determinability, deciding_programs: tuple[str, ...]
) -> Scored:
    """The three-class T1b scorer.

    * DETERMINATE            - any `cannot_determine` is a needless abstention -> 0.
    * INDETERMINATE          - must abstain, naming the affected program(s).
    * INCOMPLETE_DETERMINATE - a fact is missing but the outcome does not turn on it,
                               so the model should answer anyway. Abstaining -> 0.

    Class 3 is what stops a model scoring well by always abstaining, so it is scored as
    strictly as class 2.
    """
    claimed = {c.program for c in given.cannot_determine}
    expected = set(deciding_programs) & set(SCORED_PROGRAMS)

    if truth_label is Determinability.INDETERMINATE:
        if not claimed:
            return Scored(0.0, {"reason": "failed to abstain", "expected": sorted(expected)})
        correct = claimed == expected
        return Scored(
            1.0 if correct else 0.5,
            {"reason": "abstained" + ("" if correct else ", wrong programs"),
             "claimed": sorted(claimed), "expected": sorted(expected)},
        )

    # Both remaining classes require a confident answer.
    if claimed:
        return Scored(
            0.0,
            {"reason": "needless abstention", "class": truth_label.value,
             "claimed": sorted(claimed)},
        )
    return Scored(1.0, {"reason": "answered, correctly did not abstain"})


@_guard
def score_antihack(
    given: T1Answer, truth: T1Answer, deciding_programs: tuple[str, ...] = ()
) -> Scored:
    """Cheap structural checks against degenerate answers.

    Not a substitute for the trivial baselines - those measure whether the benchmark is
    gameable, this only catches an individual answer that is obviously not an attempt.
    """
    # Abstaining on every scored program is CORRECT when every scored program genuinely
    # turns on the withheld fact. It is degenerate only when nothing is deciding.
    claimed = {c.program for c in given.cannot_determine}
    flags = {
        "abstained_on_everything": (
            claimed >= set(SCORED_PROGRAMS) and not set(deciding_programs)
        ),
        "all_amounts_zero": all(
            v == 0.0 for v in (given.snap.benefit, given.eitc.amount, given.ctc.amount)
        )
        and not all(
            v == 0.0 for v in (truth.snap.benefit, truth.eitc.amount, truth.ctc.amount)
        ),
        "negative_amount": any(
            v < 0 for v in (given.snap.benefit, given.eitc.amount, given.ctc.amount)
        ),
    }
    return Scored(value=0.0 if any(flags.values()) else 1.0, detail=flags)
