"""Standing invariants over generated task data. Checked at BUILD time, loudly.

**The invariant: anything an answer key expects must be in `SCORED_PROGRAMS`.**

This exists because of a real defect found on the first end-to-end run (Milestone 1). A
model abstained on exactly the right *scored* programs and was marked wrong, because the
answer key expected an abstention on Medicaid - a program the scorer cannot score. The
Medicaid scope decision (`SCORED_PROGRAMS = ("snap", "eitc", "ctc")`, LIMITS 20) had been
made in one place, and the data pipeline still remembered the old world.

That is a *class* of error, not one bug: it recurs whenever scope changes in one place and
a pipeline downstream keeps producing data shaped for the previous scope. The defence is
not care, it is an assertion that fails the build. A scope change made after this file
exists breaks generation immediately and visibly, instead of silently mis-scoring every
task that touches the removed program.

Two independent lines of defence, deliberately:

1. **Structural** - `oracle.determinability.probe` labels against scored programs only,
   so a Medicaid-only flip cannot become an `INDETERMINATE` label in the first place.
2. **Assertive** - this module, called on every row `scripts/build_split.py` emits. It
   catches a future code path that builds task data without going through `probe`.

The structural fix protects today's single producer; the assertion protects producers
nobody has written yet.
"""

from __future__ import annotations

from redtape.schemas import SCORED_PROGRAMS, Determinability


class ScopeLeak(AssertionError):
    """An answer key expects something the scorer cannot score.

    An `AssertionError` rather than a `ValueError`: this is a broken invariant in our own
    pipeline, never bad input from outside it.
    """


def assert_expectations_scorable(
    *,
    task_id: str,
    determinability: str,
    deciding_programs,
    expected_abstentions=(),
) -> None:
    """Raise `ScopeLeak` unless every expectation lands inside `SCORED_PROGRAMS`.

    `deciding_programs` is what the answer key says the model must abstain on;
    `expected_abstentions` is any explicitly baked `cannot_determine` list. Both are
    checked, because both are "things the answer key expects".
    """
    scored = set(SCORED_PROGRAMS)
    deciding = tuple(deciding_programs)

    leaked = [p for p in deciding if p not in scored]
    if leaked:
        raise ScopeLeak(
            f"{task_id}: answer key expects an abstention on {leaked}, which is not in "
            f"SCORED_PROGRAMS={SCORED_PROGRAMS}. The scorer cannot score it, so the task "
            f"is unscoreable. Either add the program to SCORED_PROGRAMS with external "
            f"validation, or label the task against scored programs only."
        )

    leaked = [p for p in expected_abstentions if p not in scored]
    if leaked:
        raise ScopeLeak(
            f"{task_id}: answer key bakes a cannot_determine on {leaked}, which is not "
            f"in SCORED_PROGRAMS={SCORED_PROGRAMS}."
        )

    label = Determinability(determinability)
    if label is Determinability.INDETERMINATE and not deciding:
        raise ScopeLeak(
            f"{task_id}: labelled INDETERMINATE with no deciding program in "
            f"SCORED_PROGRAMS. The task demands an abstention the scorer cannot attribute "
            f"to anything, so every possible answer scores badly. This is the exact shape "
            f"of the Milestone 1 Medicaid defect."
        )
    if label is not Determinability.INDETERMINATE and deciding:
        raise ScopeLeak(
            f"{task_id}: labelled {label.value} but names deciding programs {deciding}. "
            f"A task that is not indeterminate must not expect an abstention."
        )


def assert_split_scorable(rows) -> int:
    """Check every row of a built split. Returns the number of rows checked."""
    n = 0
    for row in rows:
        assert_expectations_scorable(
            task_id=row.get("household_id", row.get("name", "<unknown>")),
            determinability=row["determinability"],
            deciding_programs=row.get("deciding_programs", ()),
        )
        n += 1
    return n
