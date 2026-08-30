"""The corpus must not contain households whose answer key is known to be wrong.

HR 1 (PL 119-21, enacted 2025-07-04) removed federal SNAP eligibility from refugees,
asylees, people with deportation withheld, conditional entrants and one-year parolees.
`policyengine-us==1.821.4` still models all of them as fully eligible in every month of
2025 (docs/LIMITS.md 16), so any generated household carrying one of those statuses has a
known-wrong answer key.

These tests keep them out, and will start failing when upstream implements the rule -
which is the signal to widen the corpus again.
"""

from __future__ import annotations

import pytest

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.generator.households import generate_many
from redtape.oracle.determinability import SWEEPS
from redtape.oracle.takeup import apply_suppression
from redtape.schemas import SAFE_IMMIGRATION_STATUSES, UNSAFE_IMMIGRATION_STATUSES

SEED = 20260828


def test_generator_emits_only_safe_statuses():
    for hh in generate_many(SEED, 60):
        for p in hh.people:
            assert p.immigration_status.value in SAFE_IMMIGRATION_STATUSES, (
                f"{hh.household_id}/{p.person_id} has {p.immigration_status.value}, which "
                "has a known-wrong answer key under HR 1"
            )


def test_prober_sweep_contains_only_safe_statuses():
    """Sweeping an unsafe status would probe determinability against a wrong answer key."""
    for v in SWEEPS["immigration_status"]:
        assert v.value in SAFE_IMMIGRATION_STATUSES


def test_safe_and_unsafe_sets_are_disjoint_and_complete():
    engine = {
        e.name
        for e in CountryTaxBenefitSystem().variables["immigration_status"].possible_values
    }
    assert not (set(SAFE_IMMIGRATION_STATUSES) & set(UNSAFE_IMMIGRATION_STATUSES))
    covered = set(SAFE_IMMIGRATION_STATUSES) | set(UNSAFE_IMMIGRATION_STATUSES)
    missing = engine - covered
    assert not missing, f"engine statuses not classified as safe or unsafe: {sorted(missing)}"


def _snap(status: str, month: str) -> float:
    ids = ["p1", "p2"]
    sit = {
        "people": {
            "p1": {"age": {"2025": 35}, "employment_income": {"2025": 14_400},
                   "immigration_status": {"2025": status}},
            "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0},
                   "immigration_status": {"2025": "CITIZEN"}},
        },
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return float(Simulation(situation=apply_suppression(sit, 2025)).calculate("snap", month)[0])


@pytest.mark.parametrize("status", sorted(UNSAFE_IMMIGRATION_STATUSES))
def test_known_divergence_unsafe_statuses_still_treated_as_eligible(status):
    """Asserts the CURRENT, KNOWN-WRONG behaviour on purpose.

    Under HR 1 these statuses should lose eligibility from 2025-07-04. The engine treats
    them identically to a citizen before and after. When that changes, this test fails -
    which is the notification to re-widen SAFE_IMMIGRATION_STATUSES and regenerate.
    Do not "fix" it by changing the assertion.
    """
    citizen_before, citizen_after = _snap("CITIZEN", "2025-06"), _snap("CITIZEN", "2025-08")
    assert _snap(status, "2025-06") == pytest.approx(citizen_before, abs=1.0)
    assert _snap(status, "2025-08") == pytest.approx(citizen_after, abs=1.0), (
        f"UPSTREAM CHANGE: {status} is no longer treated as a citizen after 2025-07-04. "
        "PolicyEngine may have implemented the HR 1 immigrant restrictions. Re-read "
        "docs/LIMITS.md 16 and widen SAFE_IMMIGRATION_STATUSES."
    )


def test_cofa_status_is_not_representable():
    """COFA residents remain eligible under HR 1 but the engine has no such enum value.

    Recorded so that if one appears upstream we know the category became expressible.
    """
    names = {
        e.name
        for e in CountryTaxBenefitSystem().variables["immigration_status"].possible_values
    }
    assert not any(
        t in n for n in names for t in ("COFA", "COMPACT", "MICRONESIA", "MARSHALL", "PALAU")
    )


def test_dependent_care_is_exercised_by_the_generator():
    """The dependent care deduction must actually appear in generated households."""
    hhs = generate_many(SEED, 40)
    with_care = [h for h in hhs if (h.dependent_care_cost or 0) > 0]
    assert with_care, "no generated household has a dependent care cost"
    assert len(with_care) >= 4, f"only {len(with_care)}/40 households exercise dependent care"
