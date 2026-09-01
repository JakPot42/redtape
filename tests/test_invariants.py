"""The standing scope invariant, and the gate.

Both of these exist because of defects found on the first end-to-end run, so both are
tested for *teeth*: a test that the invariant fires, not merely that it can be called.
"""

from __future__ import annotations

import pytest

from redtape.scoring.core import score_abstention, score_antihack, score_exact_match
from redtape.scoring.invariants import ScopeLeak, assert_expectations_scorable, assert_split_scorable
from redtape.schemas import (
    SCORED_PROGRAMS,
    AnnualAmount,
    CannotDetermine,
    Determinability,
    MedicaidAnswer,
    SnapAnswer,
    T1Answer,
)


def _answer(snap=500.0, eligible=True, eitc=1000.0, ctc=2200.0, cd=(), month="2025-08"):
    return T1Answer(
        snap=SnapAnswer(period_label=month, eligible=eligible, benefit=snap),
        medicaid=MedicaidAnswer(period_label="2025", person_eligible={"p1": True}),
        eitc=AnnualAmount(period_label="2025", amount=eitc),
        ctc=AnnualAmount(period_label="2025", amount=ctc),
        cannot_determine=tuple(CannotDetermine(program=p, missing_fact=f) for p, f in cd),
    )


# ---------------------------------------------------------------- the invariant
def test_medicaid_in_deciding_programs_is_a_scope_leak():
    """The Milestone 1 defect, reproduced. It must now fail the build.

    The answer key demanded an abstention on Medicaid, which SCORED_PROGRAMS excludes, so
    a model abstaining on exactly the right scored programs was marked wrong.
    """
    with pytest.raises(ScopeLeak) as exc:
        assert_expectations_scorable(
            task_id="hh-1-00007",
            determinability=Determinability.INDETERMINATE.value,
            deciding_programs=("medicaid",),
        )
    assert "medicaid" in str(exc.value)
    assert "SCORED_PROGRAMS" in str(exc.value)


def test_indeterminate_with_no_scored_deciding_program_is_a_scope_leak():
    """The subtler form: filter medicaid out and the label is left demanding nothing."""
    with pytest.raises(ScopeLeak, match="INDETERMINATE"):
        assert_expectations_scorable(
            task_id="hh-1-00008",
            determinability=Determinability.INDETERMINATE.value,
            deciding_programs=(),
        )


def test_determinate_task_naming_a_deciding_program_is_a_scope_leak():
    with pytest.raises(ScopeLeak):
        assert_expectations_scorable(
            task_id="hh-1-00009",
            determinability=Determinability.DETERMINATE.value,
            deciding_programs=("snap",),
        )


def test_a_baked_cannot_determine_on_an_unscored_program_is_a_scope_leak():
    with pytest.raises(ScopeLeak):
        assert_expectations_scorable(
            task_id="hh-1-00010",
            determinability=Determinability.INDETERMINATE.value,
            deciding_programs=("snap",),
            expected_abstentions=("medicaid",),
        )


def test_well_formed_expectations_pass():
    for label, deciding in (
        (Determinability.DETERMINATE, ()),
        (Determinability.INCOMPLETE_DETERMINATE, ()),
        (Determinability.INDETERMINATE, ("snap",)),
        (Determinability.INDETERMINATE, ("snap", "eitc", "ctc")),
    ):
        assert_expectations_scorable(
            task_id="ok", determinability=label.value, deciding_programs=deciding
        )


def test_assert_split_scorable_checks_every_row_not_just_the_first():
    rows = [
        {"household_id": "a", "determinability": "determinate", "deciding_programs": []},
        {"household_id": "b", "determinability": "indeterminate", "deciding_programs": ["snap"]},
        {"household_id": "c", "determinability": "indeterminate", "deciding_programs": ["medicaid"]},
    ]
    with pytest.raises(ScopeLeak, match="^c:"):
        assert_split_scorable(rows)
    assert assert_split_scorable(rows[:2]) == 2


def test_the_prober_cannot_produce_a_medicaid_only_indeterminate_label():
    """Structural half of the defence: the label is derived from scored programs only.

    Constructed rather than probed, so it does not need the engine: the invariant under
    test is the mapping from per-program verdicts to a label.
    """
    from redtape.oracle.determinability import DeterminabilityLabel, ProgramVerdict

    verdicts = [
        ProgramVerdict(program="snap", deciding=False, observed=("a", "a")),
        ProgramVerdict(program="medicaid", deciding=True, observed=("a", "b")),
        ProgramVerdict(program="eitc", deciding=False, observed=("a", "a")),
        ProgramVerdict(program="ctc", deciding=False, observed=("a", "a")),
    ]
    all_deciding = tuple(v.program for v in verdicts if v.deciding)
    scored = tuple(p for p in all_deciding if p in SCORED_PROGRAMS)
    label = DeterminabilityLabel(
        household_id="hh",
        withheld_fact="p1.age",
        sweep_values=("2", "40"),
        label=Determinability.INDETERMINATE if scored else Determinability.INCOMPLETE_DETERMINATE,
        per_program=tuple(verdicts),
        deciding_programs=scored,
        unscored_deciding_programs=tuple(p for p in all_deciding if p not in SCORED_PROGRAMS),
    )
    assert label.label is Determinability.INCOMPLETE_DETERMINATE
    assert label.deciding_programs == ()
    assert label.unscored_deciding_programs == ("medicaid",)
    # And what the prober produces must satisfy the assertion.
    assert_expectations_scorable(
        task_id=label.household_id,
        determinability=label.label.value,
        deciding_programs=label.deciding_programs,
    )


# ---------------------------------------------------------------- scoring changes
def test_abstaining_on_medicaid_is_neither_credited_nor_punished():
    """Medicaid is unscored, so an opinion about it is outside what we measure."""
    given = _answer(cd=(("medicaid", "p1.age"),))
    assert score_abstention(given, Determinability.DETERMINATE, ()).value == 1.0
    assert score_abstention(given, Determinability.INCOMPLETE_DETERMINATE, ()).value == 1.0

    both = _answer(cd=(("snap", "x"), ("medicaid", "x")))
    assert score_abstention(both, Determinability.INDETERMINATE, ("snap",)).value == 1.0


def test_exact_match_is_all_or_nothing():
    truth = _answer()
    assert score_exact_match(_answer(), truth).value == 1.0
    assert score_exact_match(_answer(snap=500.9), truth).value == 1.0  # within tolerance
    assert score_exact_match(_answer(snap=520.0), truth).value == 0.0  # one amount off
    assert score_exact_match(_answer(eligible=False), truth).value == 0.0  # flag off
    assert score_exact_match(_answer(month="2025-07"), truth).value == 0.0  # period off


def test_exact_match_surfaces_a_scorer_error_rather_than_scoring_zero():
    r = score_exact_match(None, _answer())  # type: ignore[arg-type]
    assert r.value == 0.0 and r.error and not r.ok


def test_antihack_reports_which_check_failed():
    given = _answer(snap=0.0, eitc=0.0, ctc=0.0)
    r = score_antihack(given, _answer())
    assert r.value == 0.0
    assert r.detail["failed"] == ["all_amounts_zero"]
    assert r.detail["gate"] == "fail"


def test_antihack_ignores_a_medicaid_only_abstention():
    given = _answer(cd=(("medicaid", "x"),))
    assert score_antihack(given, _answer(), ()).value == 1.0
