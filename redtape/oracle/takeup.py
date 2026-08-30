"""Modelled-take-up suppression.

**The problem.** PolicyEngine models a household as *receiving* the programs it would be
eligible for, and those receipts count as unearned income for SNAP. A zero-earnings
California household with a child is modelled as receiving CalWORKs ($930/mo); a
zero-earnings 67-year-old is modelled as receiving SSI ($967/mo). Neither is stated in
the narrative. Without suppression, the answer key for every generated household would
silently depend on a take-up assumption the agent cannot see - an undeclared
determinability problem sitting underneath the entire benchmark.

**The decision (v0): suppress take-up globally.** See CLAUDE.md for the reasoning and
the rejected alternative.

**Why a declared list plus an invariant, rather than a list alone.** A list can only
suppress programs we thought of, and the leak is household-shape-dependent: CalWORKs
appears for a parent with a child, SSI only once someone is old enough. A list alone
fails *open* - a shape we have not tried yet quietly reintroduces the problem.
`assert_no_unstated_income` is therefore the real guard: it asserts that the engine's
SNAP income equals exactly what the narrative states, so any unsuppressed program is
caught whether or not we anticipated it.
"""

from __future__ import annotations

from functools import lru_cache

# Programs the engine models receipt of, which count as SNAP unearned income.
# Entity and period are resolved from the engine at call time rather than hardcoded,
# because both have moved between versions.
SUPPRESSED_PROGRAMS = (
    "tanf",                 # federal TANF
    "ca_tanf",              # CalWORKs
    "ssi",                  # Supplemental Security Income
    "ca_state_supplement",  # CA SSI/SSP state supplement
    "social_security",
    "unemployment_compensation",
)


class TakeUpLeakError(AssertionError):
    """The engine gave the household income the narrative never stated."""


@lru_cache(maxsize=1)
def _variables():
    from policyengine_us import CountryTaxBenefitSystem

    return CountryTaxBenefitSystem().variables


def _zeros(var: str, tax_year: int) -> dict:
    v = _variables()[var]
    if v.definition_period == "month":
        return {f"{tax_year}-{m:02d}": 0 for m in range(1, 13)}
    return {str(tax_year): 0}


def apply_suppression(situation: dict, tax_year: int) -> dict:
    """Zero every modelled take-up program, at whichever entity holds it.

    Mutates and returns `situation`. Programs the engine does not define are skipped -
    a rename upstream must not crash the oracle, and the invariant below will catch it
    if the rename reopens a leak.
    """
    vs = _variables()
    entity_key_to_group = {
        "spm_unit": "spm_units",
        "household": "households",
        "tax_unit": "tax_units",
        "family": "families",
    }

    for var in SUPPRESSED_PROGRAMS:
        v = vs.get(var)
        if v is None:
            continue
        zeros = _zeros(var, tax_year)
        if v.entity.key == "person":
            for person in situation["people"].values():
                person[var] = dict(zeros)
        else:
            group = entity_key_to_group.get(v.entity.key)
            if group and group in situation:
                for unit in situation[group].values():
                    unit[var] = dict(zeros)
    return situation


def assert_no_unstated_income(sim, month: str, stated_monthly_earned: float) -> None:
    """The invariant. SNAP income must equal exactly what the narrative states.

    Raises TakeUpLeakError naming the shortfall, so a new leak is diagnosable rather
    than merely a failed equality.
    """
    unearned = float(sim.calculate("snap_unearned_income", month)[0])
    earned = float(sim.calculate("snap_earned_income", month)[0])

    if abs(unearned) > 0.01:
        raise TakeUpLeakError(
            f"engine gave the household ${unearned:,.2f}/mo of unearned income in {month}, "
            f"but the narrative states none. A modelled program is not in "
            f"SUPPRESSED_PROGRAMS. Run scripts/probe_takeup.py to identify it."
        )
    # One-sided on purpose. SNAP legitimately EXCLUDES some stated earnings - notably
    # the earned income of a child under 18 who is a student (7 CFR 273.9(c)(7)) - so
    # engine earned income BELOW the stated figure is correct behaviour, not a leak.
    # What must never happen is the engine inventing earnings nobody stated.
    if earned > stated_monthly_earned + 1.0:
        raise TakeUpLeakError(
            f"engine SNAP earned income ${earned:,.2f}/mo EXCEEDS stated "
            f"${stated_monthly_earned:,.2f}/mo in {month}; income was invented"
        )
