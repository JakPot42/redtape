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
    """Per-program amount match within tolerance. SNAP monthly, EITC/CTC annual.

    A program the answer left `null` is EXCLUDED from the denominator rather than counted
    wrong. Null is only reachable when the answer also abstained on that program (the schema
    enforces it), and whether that abstention was right is `score_abstention`'s job - scoring
    it here too would penalise a correct abstention twice. Answer keys never contain null, so
    only the given side can be excluded.

    If every scored program was abstained on, there is nothing to measure and the value is
    0.0. That deliberately does not reward blanket abstention, which the gate catches anyway.
    """
    checks = {
        "snap": (given.snap.benefit, truth.snap.benefit),
        "eitc": (given.eitc.amount, truth.eitc.amount),
        "ctc": (given.ctc.amount, truth.ctc.amount),
    }
    hits = {k: _close(g, t, tol) for k, (g, t) in checks.items()
            if k in SCORED_PROGRAMS and g is not None}
    return Scored(
        value=sum(hits.values()) / len(hits) if hits else 0.0,
        detail={"per_program": hits, "given": {k: v[0] for k, v in checks.items()},
                "truth": {k: v[1] for k, v in checks.items()}},
    )


@_guard
def score_eligibility(given: T1Answer, truth: T1Answer) -> Scored:
    """Eligibility booleans, exact. Medicaid is excluded - it is not scored (LIMITS 20).

    A null `eligible` is excluded for the same reason as a null amount: it is only reachable
    alongside an abstention on SNAP, and the abstention scorer already grades that decision.
    """
    hits = ({} if given.snap.eligible is None
            else {"snap": given.snap.eligible == truth.snap.eligible})
    return Scored(value=sum(hits.values()) / len(hits) if hits else 0.0,
                  detail={"per_program": hits,
                          "abstained_on_snap": given.snap.eligible is None})


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
    # Both sides are restricted to SCORED_PROGRAMS. A model that abstains on Medicaid is
    # neither credited nor punished for it: v0 does not score Medicaid, so an opinion
    # about it - in either direction - is outside what this benchmark measures.
    claimed = {c.program for c in given.cannot_determine} & set(SCORED_PROGRAMS)
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
def score_exact_match(given: T1Answer, truth: T1Answer, tol: float = TOLERANCE) -> Scored:
    """All-correct: every scored amount within tolerance, eligibility exact, periods exact.

    This is headline metric (a) and it is deliberately unforgiving. SPEC.md 4 says partial
    credit is *reported* but the headline is all-correct; a per-component average is the
    number that flatters, so the headline must not be one.
    """
    amounts = score_amounts(given, truth, tol)
    elig = score_eligibility(given, truth)
    periods = score_periods(given, truth)
    for part in (amounts, elig, periods):
        if part.error:
            return part
    parts = {"amounts": amounts.value, "eligibility": elig.value, "periods": periods.value}
    return Scored(
        value=1.0 if all(v == 1.0 for v in parts.values()) else 0.0,
        detail={"components": parts},
    )


# ------------------------------------------------------------------ the antihack GATE
#
# Antihack is a GATE, not a weighted component (CLAUDE.md, "Antihack is a gate"). A
# response that fails a structural check has not made a scoreable attempt, and letting it
# collect 95% of a weighted sum for the parts that happen to look fine rewards exactly the
# degenerate behaviour the check exists to catch. Pass/fail is also the honest shape of
# the thing being measured: "is this a real answer" is a yes/no question, not 5% of one.


@_guard
def score_antihack(
    given: T1Answer, truth: T1Answer, deciding_programs: tuple[str, ...] = ()
) -> Scored:
    """Structural gate against degenerate answers. 1.0 = pass, 0.0 = gate failed.

    Not a substitute for the trivial baselines - those measure whether the benchmark is
    gameable, this only catches an individual answer that is obviously not an attempt.
    """
    # Abstaining on every scored program is CORRECT when every scored program genuinely
    # turns on the withheld fact. It is degenerate only when nothing is deciding.
    claimed = {c.program for c in given.cannot_determine} & set(SCORED_PROGRAMS)
    amounts = {
        "snap": (given.snap.benefit, truth.snap.benefit),
        "eitc": (given.eitc.amount, truth.eitc.amount),
        "ctc": (given.ctc.amount, truth.ctc.amount),
    }
    # A program the response abstained on has no meaningful amount, so its zero is not
    # evidence of a degenerate answer. Without this exemption the gate would fail every
    # correct abstention (whose amount fields are necessarily empty), which would make the
    # abstention headline unmeasurable for exactly the answers it exists to measure.
    answered = {k: v for k, v in amounts.items() if k not in claimed}
    flags = {
        "abstained_on_everything": (
            claimed >= set(SCORED_PROGRAMS) and not set(deciding_programs)
        ),
        "all_amounts_zero": bool(answered)
        and all(g == 0.0 for g, _ in answered.values() if g is not None)
        and any(g is not None for g, _ in answered.values())
        and not all(t == 0.0 for _, t in answered.values()),
        # `None` means abstained, not negative. Comparing it would raise, and the guard
        # would turn a correct abstention into a scorer_error.
        "negative_amount": any(g is not None and g < 0 for g, _ in amounts.values()),
    }
    failed = sorted(k for k, v in flags.items() if v)
    return Scored(
        value=0.0 if failed else 1.0,
        detail={"flags": flags, "failed": failed, "gate": "pass" if not failed else "fail"},
    )
