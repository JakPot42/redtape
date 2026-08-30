"""Take-up suppression: the answer key must never depend on an unstated assumption.

The decisive test is `test_invariant_has_teeth`: it removes the suppression and asserts
the invariant FAILS. Without it, the other tests could all pass because the invariant is
vacuous rather than because the leak is closed.
"""

from __future__ import annotations

import pytest

from policyengine_us import Simulation

from redtape.generator.households import generate_many
from redtape.oracle.policyengine_oracle import build_situation, compute
from redtape.oracle.takeup import (
    SUPPRESSED_PROGRAMS,
    TakeUpLeakError,
    apply_suppression,
    assert_no_unstated_income,
)
from redtape.schemas import Household, ImmigrationStatus, Person

SEED = 20260828
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]


def _household(ages, disabled=None, earned=0.0, housing=10_800.0, month="2025-04") -> Household:
    disabled = disabled or [False] * len(ages)
    people = tuple(
        Person(
            person_id=f"p{i+1}",
            age=a,
            employment_income=earned if i == 0 else 0.0,
            immigration_status=ImmigrationStatus.CITIZEN,
            is_disabled=disabled[i],
        )
        for i, a in enumerate(ages)
    )
    return Household(
        household_id="test-hh", seed=0, index=0, month=month,
        people=people, housing_cost=housing,
    )


# Shapes chosen because each leaks a DIFFERENT programme when unsuppressed:
# parent+child leaks CalWORKs, a senior leaks SSI. The leak is shape-dependent, which
# is why a declared list alone is not sufficient.
SHAPES = [
    ([30, 8], None, "parent and child - leaks CalWORKs"),
    ([67], None, "senior alone - leaks SSI"),
    ([67, 40], [False, True], "senior plus disabled adult"),
    ([45], [True], "disabled adult alone"),
    ([30], None, "single adult"),
    ([30, 8, 5], None, "parent and two children"),
    ([70, 68], None, "two seniors"),
]


@pytest.mark.parametrize("ages,disabled,label", SHAPES)
def test_no_unstated_income_for_any_shape(ages, disabled, label):
    """compute() raises if any modelled programme leaks income in."""
    hh = _household(ages, disabled)
    compute(hh)  # asserts the invariant internally


@pytest.mark.parametrize("ages,disabled,label", SHAPES)
def test_suppressed_programs_are_actually_zero(ages, disabled, label):
    hh = _household(ages, disabled)
    sim = Simulation(situation=build_situation(hh))
    assert float(sim.calculate("snap_unearned_income", hh.month)[0]) == pytest.approx(0.0, abs=0.01)


def test_invariant_has_teeth():
    """Remove the suppression and the invariant must FAIL.

    A parent with a child and no earnings is modelled as receiving CalWORKs. If this
    test ever passes without raising, the invariant has stopped guarding anything.
    """
    hh = _household([30, 8])
    situation = build_situation(hh)
    # Undo suppression for the CalWORKs path only.
    for unit in situation["spm_units"].values():
        unit.pop("tanf", None)
        unit.pop("ca_tanf", None)
    sim = Simulation(situation=situation)
    with pytest.raises(TakeUpLeakError, match="unearned income"):
        assert_no_unstated_income(sim, hh.month, 0.0)


def test_invariant_has_teeth_for_ssi_path():
    """Same, for the elderly shape, which leaks a different programme."""
    hh = _household([67])
    situation = build_situation(hh)
    for person in situation["people"].values():
        person.pop("ssi", None)
        person.pop("ca_state_supplement", None)
        person.pop("social_security", None)
    sim = Simulation(situation=situation)
    with pytest.raises(TakeUpLeakError):
        assert_no_unstated_income(sim, hh.month, 0.0)


def test_stated_earnings_still_reach_the_engine():
    """Suppression must not zero the income the narrative DOES state."""
    hh = _household([30], earned=24_000.0)
    sim = Simulation(situation=build_situation(hh))
    assert float(sim.calculate("snap_earned_income", hh.month)[0]) == pytest.approx(2000.0, abs=1.0)


def test_child_earnings_exclusion_is_not_treated_as_a_leak():
    """SNAP excludes a student child's earnings (7 CFR 273.9(c)(7)).

    The engine reporting LESS earned income than stated is correct behaviour. The
    invariant is one-sided for exactly this reason - a two-sided version produced a
    false positive the moment a determinability sweep aged an earner into childhood.
    """
    compute(_household([10], earned=18_000.0))  # must not raise


def test_invented_earnings_would_be_caught():
    """The one-sided invariant still fires if the engine ADDS earnings."""
    hh = _household([30], earned=24_000.0)
    sim = Simulation(situation=build_situation(hh))
    with pytest.raises(TakeUpLeakError, match="EXCEEDS"):
        assert_no_unstated_income(sim, hh.month, 0.0)


@pytest.mark.parametrize("hh", generate_many(SEED, 12))
def test_generated_households_have_no_unstated_income(hh):
    """Every household the generator produces must satisfy the invariant."""
    compute(hh)


def test_suppression_is_applied_to_every_declared_program():
    hh = _household([30, 8])
    situation = build_situation(hh)
    from policyengine_us import CountryTaxBenefitSystem

    vs = CountryTaxBenefitSystem().variables
    for var in SUPPRESSED_PROGRAMS:
        v = vs.get(var)
        if v is None:
            continue
        if v.entity.key == "person":
            for person in situation["people"].values():
                assert var in person, f"{var} not suppressed at person level"
        elif v.entity.key == "spm_unit":
            for unit in situation["spm_units"].values():
                assert var in unit, f"{var} not suppressed at spm_unit level"


def test_apply_suppression_tolerates_a_renamed_program():
    """An upstream rename must not crash the oracle; the invariant is the real guard."""
    situation = {
        "people": {"p1": {"age": {"2025": 30}}},
        "spm_units": {"s": {"members": ["p1"]}},
    }
    import redtape.oracle.takeup as t

    original = t.SUPPRESSED_PROGRAMS
    try:
        t.SUPPRESSED_PROGRAMS = original + ("a_program_that_does_not_exist",)
        apply_suppression(situation, 2025)  # must not raise
    finally:
        t.SUPPRESSED_PROGRAMS = original
