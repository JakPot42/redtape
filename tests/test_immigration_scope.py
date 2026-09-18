"""The corpus must not contain households whose answer key is known to be wrong.

History, because it is the point of this file
---------------------------------------------
This module used to assert that policyengine-us had NOT implemented the HR 1 immigrant
restrictions, and the generator was narrowed to exclude REFUGEE/ASYLEE on that basis.
That was wrong. The engine implements PL 119-21 section 10108 in two layers, and
California delays it to 2026-04-01 per CDSS ACL 25-92. Our probe hardcoded
`state_name: CA` and swept only months of 2025, so it measured the one state and the one
year in which correct behaviour is indistinguishable from no behaviour at all.

So the tests below do two jobs:

1. Pin the engine's ACTUAL behaviour at both date boundaries in BOTH a delaying state
   (CA) and a non-delaying one (TX). If upstream changes any of it, we are told.
2. Pin the corpus SCOPE. `SAFE_IMMIGRATION_STATUSES` is only safe because the corpus is
   California-only and 2025-only. If either constant moves, the gate must be re-derived,
   and `test_safe_set_is_justified_only_for_the_declared_scope` fails until it is.

Job 2 is the guard the original failure mode lacked. The measurement was correct; the
scope it was generalised to was not.
"""

from __future__ import annotations

import inspect

import pytest

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.generator.households import TAX_YEAR, generate_many
from redtape.oracle.determinability import SWEEPS
from redtape.oracle.takeup import apply_suppression
from redtape.schemas import (
    CORPUS_STATE,
    CORPUS_TAX_YEAR,
    SAFE_IMMIGRATION_STATUSES,
    UNSAFE_IMMIGRATION_STATUSES,
)

SEED = 20260828

# Statuses HR 1 removed from federal SNAP eligibility. Eligible in CA through 2026-03
# (ACL 25-92), ineligible in a non-delaying state from 2025-07.
HR1_REMOVED = (
    "REFUGEE",
    "ASYLEE",
    "DEPORTATION_WITHHELD",
    "CONDITIONAL_ENTRANT",
    "PAROLED_ONE_YEAR",
)
HR1_RETAINED = ("CITIZEN", "LEGAL_PERMANENT_RESIDENT", "CUBAN_HAITIAN_ENTRANT")
NEVER_ELIGIBLE = ("UNDOCUMENTED", "DACA", "TPS")


# --------------------------------------------------------------------------------------
# Corpus containment
# --------------------------------------------------------------------------------------


def test_generator_emits_only_safe_statuses():
    for hh in generate_many(SEED, 60):
        for p in hh.people:
            assert p.immigration_status.value in SAFE_IMMIGRATION_STATUSES, (
                f"{hh.household_id}/{p.person_id} has {p.immigration_status.value}, which "
                "is not verified correct for the corpus scope"
            )


def test_prober_sweep_contains_only_safe_statuses():
    for v in SWEEPS["immigration_status"]:
        assert v.value in SAFE_IMMIGRATION_STATUSES


def test_safe_and_unsafe_sets_are_disjoint_and_complete():
    """Forces any NEW engine status to be classified rather than silently generated."""
    engine = {
        e.name
        for e in CountryTaxBenefitSystem().variables["immigration_status"].possible_values
    }
    assert not (set(SAFE_IMMIGRATION_STATUSES) & set(UNSAFE_IMMIGRATION_STATUSES))
    covered = set(SAFE_IMMIGRATION_STATUSES) | set(UNSAFE_IMMIGRATION_STATUSES)
    missing = engine - covered
    assert not missing, f"engine statuses not classified as safe or unsafe: {sorted(missing)}"


def test_every_safe_status_has_narrative_prose():
    """A generated status with no prose crashes the renderer, and nothing else caught it.

    Re-widening the corpus on 2026-09-16 added five statuses to the generator while
    `_STATUS_PHRASE` still held only the original six. 12% of generated households raised
    `KeyError` on render and all 300 tests stayed green, because the generator tests never
    rendered. This closes that.
    """
    from redtape.generator.narratives import _STATUS_PHRASE

    missing = sorted(set(SAFE_IMMIGRATION_STATUSES) - set(_STATUS_PHRASE))
    assert not missing, (
        f"statuses the generator may emit but the narrative renderer cannot express: "
        f"{missing}. Add prose to _STATUS_PHRASE in redtape/generator/narratives.py - do "
        "NOT make the lookup permissive, because a silent omission would make a stated "
        "status indistinguishable from a withheld one (docs/LIMITS.md 25)."
    )
    for status, phrases in _STATUS_PHRASE.items():
        assert phrases and all(p.strip() for p in phrases), status


def test_generated_households_actually_render():
    """End-to-end: every generated household must produce a narrative."""
    from redtape.generator.narratives import render

    for hh in generate_many(SEED, 400):
        text = render(hh)
        assert text and "Person" in text, hh.household_id


def test_refugee_and_asylee_appear_in_rendered_narratives():
    """Restoring the statuses is only real if they reach the prose a model reads."""
    from redtape.generator.narratives import render

    prose = " ".join(render(hh) for hh in generate_many(SEED, 400)).lower()
    assert "refugee" in prose, "no generated narrative mentions a refugee"
    assert "asylee" in prose or "asylum" in prose, "no generated narrative mentions an asylee"


def test_refugee_and_asylee_are_generated_again():
    """The restriction we removed must actually be gone, not merely allowed in principle."""
    statuses = {
        p.immigration_status.value for hh in generate_many(SEED, 400) for p in hh.people
    }
    for status in ("REFUGEE", "ASYLEE"):
        assert status in statuses, (
            f"{status} is in SAFE_IMMIGRATION_STATUSES but the generator never emits it; "
            "check _STATUS_WEIGHTS in redtape/generator/households.py"
        )


# --------------------------------------------------------------------------------------
# Scope guard - the structural fix for the failure mode that caused all of this
# --------------------------------------------------------------------------------------


def test_corpus_scope_constants_agree():
    assert TAX_YEAR == CORPUS_TAX_YEAR
    hhs = generate_many(SEED, 20)
    assert {hh.state for hh in hhs} == {CORPUS_STATE}
    assert {hh.tax_year for hh in hhs} == {CORPUS_TAX_YEAR}


def test_safe_set_is_justified_only_for_the_declared_scope():
    """`SAFE_IMMIGRATION_STATUSES` includes the HR 1-removed statuses ONLY because the
    corpus is California-only and 2025-only.

    In a non-delaying state they are ineligible from 2025-07; in California they are
    ineligible from 2026-04. If CORPUS_STATE or CORPUS_TAX_YEAR changes, the gate is no
    longer derived from anything and must be re-derived against the engine before the
    corpus is regenerated. Do not relax this assertion - change the safe set instead.
    """
    overlap = sorted(set(HR1_REMOVED) & set(SAFE_IMMIGRATION_STATUSES))
    if not overlap:
        pytest.skip("HR 1-removed statuses are already excluded; guard not needed")
    assert CORPUS_STATE == "CA", (
        f"CORPUS_STATE is {CORPUS_STATE!r}, but SAFE_IMMIGRATION_STATUSES still contains "
        f"{overlap}, which are only eligible under California's ACL 25-92 delay. "
        "Re-derive the gate (scripts/probe_immigration_state_scope.py) and "
        "docs/LIMITS.md 16."
    )
    assert CORPUS_TAX_YEAR == 2025, (
        f"CORPUS_TAX_YEAR is {CORPUS_TAX_YEAR}, but California's delay expires 2026-04-01, "
        f"so {overlap} are not eligible for the whole of any later year. Re-derive the "
        "gate and docs/LIMITS.md 16."
    )


# --------------------------------------------------------------------------------------
# Engine behaviour, pinned at both boundaries in both a delaying and non-delaying state
# --------------------------------------------------------------------------------------


def _sim(status: str, year: int, state: str):
    y = str(year)
    ids = ["p1", "p2"]
    sit = {
        "people": {
            "p1": {"age": {y: 35}, "employment_income": {y: 14_400},
                   "immigration_status": {y: status}},
            "p2": {"age": {y: 8}, "employment_income": {y: 0},
                   "immigration_status": {y: "CITIZEN"}},
        },
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {y: 12_000},
                            "has_heating_cooling_expense": {y: True}}},
        "households": {"h": {"members": ids, "state_name": {y: state}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, year))


def _status_eligible(status: str, month: str, state: str) -> bool:
    sim = _sim(status, int(month[:4]), state)
    return bool(sim.calculate("is_snap_immigration_status_eligible", month)[0])


# (month, CA expected, non-delaying-state expected). CA delays to 2026-04-01; TX takes
# the federal 2025-07-01 layer immediately.
_BOUNDARY_CELLS = (
    ("2025-06", True, True),
    ("2025-07", True, False),
    ("2026-03", True, False),
    ("2026-04", False, False),
)


@pytest.mark.parametrize("status", HR1_REMOVED)
@pytest.mark.parametrize(("month", "ca", "tx"), _BOUNDARY_CELLS)
def test_hr1_removed_statuses_follow_the_state_specific_dates(status, month, ca, tx):
    """This is the test the original probe should have been.

    If any cell moves, upstream changed something: re-read docs/LIMITS.md 16 and
    re-derive SAFE_IMMIGRATION_STATUSES before regenerating the corpus.
    """
    assert _status_eligible(status, month, "CA") is ca, (
        f"CA {status} {month}: expected {ca}. California delays HR 1 to 2026-04-01 per "
        "CDSS ACL 25-92 (gov/states/ca/cdss/snap/eligibility/"
        "eligible_immigration_statuses.yaml)."
    )
    assert _status_eligible(status, month, "TX") is tx, (
        f"TX {status} {month}: expected {tx}. The federal layer restricts from 2025-07-01 "
        "(gov/usda/snap/eligibility/eligible_immigration_statuses.yaml)."
    )


@pytest.mark.parametrize("status", HR1_RETAINED)
@pytest.mark.parametrize("month", [c[0] for c in _BOUNDARY_CELLS])
def test_hr1_retained_statuses_stay_eligible_everywhere(status, month):
    for state in ("CA", "TX"):
        assert _status_eligible(status, month, state) is True, (
            f"{state} {status} {month}: HR 1 retains this category; expected eligible"
        )


@pytest.mark.parametrize("status", NEVER_ELIGIBLE)
@pytest.mark.parametrize("month", [c[0] for c in _BOUNDARY_CELLS])
def test_never_eligible_statuses_stay_ineligible_everywhere(status, month):
    for state in ("CA", "TX"):
        assert _status_eligible(status, month, state) is False, (
            f"{state} {status} {month}: expected ineligible"
        )


def test_corpus_scope_cell_is_the_eligible_one():
    """The single cell the corpus actually occupies: CA, 2025, every month eligible.

    Guards the answer keys directly rather than by inference from the parameter files.
    """
    for month in (f"{CORPUS_TAX_YEAR}-{m:02d}" for m in range(1, 13)):
        for status in HR1_REMOVED:
            assert _status_eligible(status, month, CORPUS_STATE) is True, (
                f"{CORPUS_STATE} {status} {month} is not eligible, but the corpus "
                "generates this status for this scope"
            )


def test_california_delay_is_not_a_cfap_substitution():
    """`ca_cfap` is a separate state-funded programme. The CA eligibility we rely on must
    be federal SNAP eligibility, not CFAP standing in for it.
    """
    refugee = _sim("REFUGEE", 2025, "CA")
    citizen = _sim("CITIZEN", 2025, "CA")
    assert bool(refugee.calculate("is_snap_immigration_status_eligible", "2025-08")[0])
    assert float(refugee.calculate("snap", "2025-08")[0]) > 0
    assert float(refugee.calculate("snap", "2025-08")[0]) == pytest.approx(
        float(citizen.calculate("snap", "2025-08")[0]), abs=1.0
    )


# --------------------------------------------------------------------------------------
# Remaining real gaps
# --------------------------------------------------------------------------------------


def test_cofa_status_is_not_representable():
    """COFA residents remain eligible under HR 1 but the engine has no such enum value.

    The federal 2025-07-01 parameter layer carries COFA as a YAML comment only. Tracked
    upstream as PolicyEngine/policyengine-us#8296; recorded here so that if a value
    appears we know the category became expressible.
    """
    names = {
        e.name
        for e in CountryTaxBenefitSystem().variables["immigration_status"].possible_values
    }
    assert not any(
        t in n for n in names for t in ("COFA", "COMPACT", "MICRONESIA", "MARSHALL", "PALAU")
    )


def test_years_since_us_entry_exists_but_snap_does_not_read_it():
    """The five-year LPR bar is unmodelled for SNAP, but not for the reason we gave.

    LIMITS 16 said the engine "has no date-of-entry input". It has one -
    `years_since_us_entry`, default 5, a PolicyEngine modelling choice. SNAP's status
    test simply does not consult it. If that changes, LPR answer keys change.
    """
    vs = CountryTaxBenefitSystem().variables
    assert "years_since_us_entry" in vs
    assert vs["years_since_us_entry"].default_value == 5

    src = inspect.getsource(type(vs["is_snap_immigration_status_eligible"]))
    assert "years_since_us_entry" not in src, (
        "SNAP's immigration status test now reads years_since_us_entry; the five-year bar "
        "may be modelled. Re-check LEGAL_PERMANENT_RESIDENT answer keys (LIMITS 16)."
    )


def test_dependent_care_is_exercised_by_the_generator():
    """The dependent care deduction must actually appear in generated households."""
    hhs = generate_many(SEED, 40)
    with_care = [h for h in hhs if (h.dependent_care_cost or 0) > 0]
    assert with_care, "no generated household has a dependent care cost"
    assert len(with_care) >= 4, f"only {len(with_care)}/40 households exercise dependent care"
