"""PolicyEngine wrapper. The only place in redtape that touches the engine.

Design constraints, all from CLAUDE.md:

* Raise loudly on a withheld fact rather than letting a default through. PolicyEngine
  silently substitutes plausible defaults (employment_income->0, age->40,
  immigration_status->CITIZEN, housing_cost->0), so a household with a hole in it
  would otherwise produce a confident wrong answer key.
* Never query a monthly variable at an annual period. is_snap_eligible is
  quantity_type=stock, so an annual query returns December alone. See docs/LIMITS.md 1.
* Attach the source variable, entity, and queried period to every value produced.
* Import cost is ~30s cold, so this is imported once at generation time and never
  from inside a rollout.
* Suppress modelled programme take-up, and assert afterwards that the engine gave
  the household no income the narrative did not state. See redtape/oracle/takeup.py.
"""

from __future__ import annotations

import platform
from functools import lru_cache
from importlib.metadata import version

from redtape.oracle.takeup import apply_suppression, assert_no_unstated_income
from redtape.schemas import (
    AnnualAmount,
    Household,
    MedicaidAnswer,
    OracleResult,
    Provenance,
    SnapAnswer,
    T1Answer,
)


class MissingFactError(ValueError):
    """A required fact was withheld. The engine would have silently defaulted it."""


@lru_cache(maxsize=1)
def _variables():
    from policyengine_us import CountryTaxBenefitSystem

    return CountryTaxBenefitSystem().variables


def build_situation(hh: Household) -> dict:
    """Structured household -> PolicyEngine situation dict.

    Facts are set only at genuine *input* variables. Overriding a mid-chain computed
    variable does not propagate to the eligibility path (docs/LIMITS.md 2).
    """
    missing = hh.withheld()
    if missing:
        raise MissingFactError(
            f"household {hh.household_id} withholds {missing}; the oracle refuses to "
            "answer rather than let PolicyEngine substitute a default"
        )

    year = str(hh.tax_year)
    members = [p.person_id for p in hh.people]

    people = {}
    for p in hh.people:
        people[p.person_id] = {
            "age": {year: p.age},
            "employment_income": {year: p.employment_income},
            "immigration_status": {year: p.immigration_status.value},
            "is_disabled": {year: p.is_disabled},
            "is_snap_higher_ed_student": {year: p.is_higher_ed_student},
        }

    # One marital unit per adult, matching PolicyEngine's expectations for
    # unmarried households. v0 does not model married couples.
    marital_units = {
        f"mu_{p.person_id}": {"members": [p.person_id]} for p in hh.people if p.age >= 18
    }

    situation = {
        "people": people,
        "tax_units": {"tu": {"members": members}},
        "families": {"fam": {"members": members}},
        "spm_units": {
            "spm": {
                "members": members,
                "housing_cost": {year: hh.housing_cost},
                "childcare_expenses": {year: hh.dependent_care_cost},
                "has_heating_cooling_expense": {year: True},
            }
        },
        "households": {"hh": {"members": members, "state_name": {year: hh.state}}},
        "marital_units": marital_units,
    }
    # Suppress imputed take-up, but pass through anything the narrative declares.
    declarations = {p.person_id: p.declarations() for p in hh.people}
    statuses = {p.person_id: p.declared_statuses for p in hh.people if p.declared_statuses}
    return apply_suppression(situation, hh.tax_year, declarations, statuses)


# (answer field, variable, period accessor). SNAP is monthly; the rest are annual.
_QUERIES = (
    ("snap.eligible", "is_snap_eligible", "month"),
    ("snap.benefit", "snap", "month"),
    ("medicaid.person_eligible", "is_medicaid_eligible", "year"),
    ("eitc.amount", "eitc", "year"),
    ("ctc.amount", "ctc_value", "year"),
    ("ctc.gross_entitlement", "ctc", "year"),
)


def _provenance(field: str, name: str, period: str) -> Provenance:
    v = _variables()[name]
    return Provenance(
        field=field,
        variable=name,
        entity=v.entity.key,
        period_queried=period,
        quantity_type=getattr(v.quantity_type, "name", str(v.quantity_type)).lower(),
    )


def compute(hh: Household) -> OracleResult:
    """Ground truth for one household. Raises MissingFactError if any fact is withheld."""
    from policyengine_us import Simulation

    sim = Simulation(situation=build_situation(hh))
    year, month = hh.tax_year, hh.month

    # Fails loudly if an imputed programme we did not suppress leaked income in.
    stated_monthly_earned = sum(p.employment_income for p in hh.people) / 12
    stated_monthly_unearned = sum(p.declared_annual_total for p in hh.people) / 12
    assert_no_unstated_income(sim, month, stated_monthly_earned, stated_monthly_unearned)

    eligible = bool(sim.calculate("is_snap_eligible", month)[0])
    benefit = float(sim.calculate("snap", month)[0])
    medicaid = sim.calculate("is_medicaid_eligible", year)
    eitc = float(sim.calculate("eitc", year)[0])
    # `ctc` is the GROSS credit before limitation; `ctc_value` is what the household
    # actually receives once tax liability and the refundable cap are applied. A
    # zero-income family with two children has ctc=4,400 and ctc_value=0. The scored
    # answer is what is received.
    ctc_gross = float(sim.calculate("ctc", year)[0])
    ctc = float(sim.calculate("ctc_value", year)[0])

    answer = T1Answer(
        snap=SnapAnswer(period_label=month, eligible=eligible, benefit=benefit),
        medicaid=MedicaidAnswer(
            period_label=str(year),
            person_eligible={p.person_id: bool(v) for p, v in zip(hh.people, medicaid)},
        ),
        eitc=AnnualAmount(period_label=str(year), amount=eitc),
        ctc=AnnualAmount(period_label=str(year), amount=ctc, gross_entitlement=ctc_gross),
    )

    prov = tuple(
        _provenance(f, n, month if p == "month" else str(year)) for f, n, p in _QUERIES
    )

    return OracleResult(
        answer=answer,
        provenance=prov,
        engine_version=version("policyengine-us"),
        python_version=platform.python_version(),
    )
