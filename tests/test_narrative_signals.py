"""Withholding a fact must always leave a visible trace in the narrative.

**Why this file exists.** `is_higher_ed_student` and `is_disabled` used to render only when
true, so a withheld student status and a stated non-student produced byte-identical text.
That made the eligibility-flip class unanswerable: the model had no way to know a fact was
missing, so correct abstention could not be reached from the narrative, and the only
strategy that scored was abstaining on every case that failed to mention a student.

It is the pathology in `docs/LIMITS.md` §3 - *omitting a fact and stating it as zero are
indistinguishable* - reproduced in our own generator, for the one fact confirmed to flip
eligibility (§22). Age, income and immigration status never had it, because for those an
absent clause is itself the signal. Booleans rendered only-when-true had no such signal.

**The invariant, stated generally:** for every fact the generator can withhold, the
narrative with the fact must differ from the narrative without it. If it does not, the task
is unanswerable and no scoring change can rescue it.
"""

from __future__ import annotations

import pytest

from redtape.config import DEV_SEED
from redtape.generator.households import generate, withhold
from redtape.generator.narratives import render

# Every fact the T1b machinery can withhold: scripts/build_split.py T1B_FACTS + FLIP_FACT.
WITHHOLDABLE = (
    "housing_cost",
    "p1.employment_income",
    "p1.age",
    "p1.immigration_status",
    "p1.is_disabled",
    "p1.is_higher_ed_student",
)

INDICES = range(12)


@pytest.mark.parametrize("fact", WITHHOLDABLE)
def test_withholding_a_fact_changes_the_narrative(fact):
    """The general invariant. A fact whose removal is invisible cannot be abstained on."""
    invisible = []
    for index in INDICES:
        hh = generate(DEV_SEED, index)
        before = render(hh)
        after = render(withhold(hh, fact))
        if before == after:
            invisible.append(index)

    assert not invisible, (
        f"withholding {fact} left the narrative COMPLETELY unchanged for households "
        f"{invisible}. A reader cannot know the fact is missing, so abstention is "
        f"unreachable and every one of these tasks is unanswerable. See "
        f"docs/LIMITS.md 3 and the module docstring."
    )


def test_a_known_false_boolean_is_stated_not_silent():
    """The specific regression. Silence must mean 'withheld', never 'false'.

    This is the half that makes the general test above meaningful: a boolean could satisfy
    'the narrative changed' while still being ambiguous, if false and withheld were both
    rendered as silence and only TRUE differed.
    """
    student_phrases = ("enrolled", "student", "university", "degree programme")

    def p1_line(text: str) -> str:
        """Only p1's sentence. `withhold` removes p1's fact and leaves everyone else's
        stated, so a whole-narrative search matches a sibling's clause and proves nothing.
        """
        return next(ln for ln in text.splitlines() if ln.startswith("Person p1 "))

    found_known_false = False
    for index in INDICES:
        hh = generate(DEV_SEED, index)
        p1 = hh.people[0]
        if p1.is_higher_ed_student is not False:
            continue
        found_known_false = True

        known = p1_line(render(hh))
        assert any(p in known for p in student_phrases), (
            f"household {index}: p1 is known NOT to be a student, but the narrative says "
            f"nothing about it. Silence then means both 'not a student' and 'we did not "
            f"ask', which is precisely the ambiguity this must not have."
        )

        withheld = p1_line(render(withhold(hh, "p1.is_higher_ed_student")))
        assert not any(p in withheld for p in student_phrases), (
            f"household {index}: the fact was withheld but the narrative still discusses "
            f"student status"
        )

    assert found_known_false, "no household had a known-false student status to check"


def test_the_flip_fact_specifically_is_never_silent_when_known():
    """`is_higher_ed_student` is the only confirmed eligibility-flipping fact (LIMITS 22),
    so it carries the scarcest T1b class and gets its own assertion."""
    for index in INDICES:
        hh = generate(DEV_SEED, index)
        if hh.people[0].is_higher_ed_student is None:
            continue
        before = render(hh)
        after = render(withhold(hh, "p1.is_higher_ed_student"))
        assert before != after, (
            f"household {index}: withholding the eligibility-flipping fact is invisible"
        )
