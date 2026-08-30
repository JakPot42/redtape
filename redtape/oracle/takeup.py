"""Suppress imputation, permit declaration.

**The problem.** PolicyEngine models a household as *receiving* the programmes it would
be eligible for, and those receipts count as unearned income for SNAP. A zero-earnings
California household with a child is modelled as receiving CalWORKs at $930/month; a
zero-earnings 67-year-old is modelled as receiving SSI at $967/month. The narrative
states neither, so the answer key would silently depend on a take-up assumption the agent
cannot see.

**The distinction this module encodes.**

* *Imputation* - the engine deciding, on its own, that a household takes up a programme.
  Always suppressed. It is invisible to the agent, so it can never be part of a fair
  answer key.
* *Declaration* - the narrative stating that the household receives a benefit, exactly as
  it states employment income. Always permitted, and passed through to the engine.

An earlier version conflated the two by zeroing the variables outright. That was
over-broad by one step, and it cost a real axis: SNAP's definition of a disabled member
requires *receipt* of a qualifying benefit (SSI, SSDI, VA disability), not a
self-reported flag, so zeroing SSI meant `is_disabled=True` could never make a household
elderly-or-disabled and the excess-shelter-cap exemption was unreachable. Declaring
receipt restores it.

The invariant is unchanged in spirit: the engine may not give the household income the
narrative did not state. Declared benefits *are* stated, so they are permitted; the
invariant now checks against the declared total rather than against zero.
"""

from __future__ import annotations

from functools import lru_cache

# Programmes the engine imputes receipt of, which count as SNAP unearned income.
# Entity and period are resolved from the engine at call time rather than hardcoded,
# because both have moved between versions.
SUPPRESSED_PROGRAMS = (
    "tanf",                 # federal TANF
    "ca_tanf",              # CalWORKs
    "ssi",                  # Supplemental Security Income
    "ca_state_supplement",  # CA SSI/SSP state supplement
    "social_security",
    "social_security_disability",
    "unemployment_compensation",
)

# Programmes a narrative may state receipt of. A declared programme is written to the
# engine at the declared amount instead of being zeroed. Restricted to the benefits that
# establish "elderly or disabled" status for SNAP, plus retirement Social Security.
DECLARABLE_PROGRAMS = (
    "ssi",
    "social_security_disability",
    "social_security",
)

# Boolean status facts a narrative may state. These are NOT payments - they are
# determinations. Three of the five entries in `gov.usda.disabled_programs` are of this
# kind, so without them the disability axis cannot be reached by declaration alone.
#
# Note carefully: `is_ssi_disabled` is the SSI *determination*. Declaring an SSI dollar
# amount does NOT establish it, because `ssi` is not in gov.usda.disabled_programs.
# A narrative that says "receives $967/month in SSI" therefore does not make the
# household elderly-or-disabled for SNAP; one that says "receives SSDI" does.
DECLARABLE_STATUSES = (
    "is_ssi_disabled",
    "is_permanently_disabled_veteran",
    "is_surviving_spouse_of_disabled_veteran",
    "is_surviving_child_of_disabled_veteran",
)


class TakeUpLeakError(AssertionError):
    """The engine gave the household income the narrative never stated."""


@lru_cache(maxsize=1)
def _variables():
    from policyengine_us import CountryTaxBenefitSystem

    return CountryTaxBenefitSystem().variables


def _periodised(var: str, tax_year: int, annual_amount: float) -> dict:
    """Spread an annual amount across the variable's own definition period."""
    v = _variables()[var]
    if v.definition_period == "month":
        return {f"{tax_year}-{m:02d}": annual_amount / 12 for m in range(1, 13)}
    return {str(tax_year): annual_amount}


_ENTITY_GROUP = {
    "spm_unit": "spm_units",
    "household": "households",
    "tax_unit": "tax_units",
    "family": "families",
}


def apply_suppression(
    situation: dict,
    tax_year: int,
    declarations: dict | None = None,
    statuses: dict | None = None,
) -> dict:
    """Zero every imputed programme; write declared ones at their stated amount.

    `declarations` maps person_id -> {programme: annual_amount}. A programme declared for
    a person is set to that amount; every other programme, for every other person and
    unit, is set to zero.

    Programmes the engine does not define are skipped - an upstream rename must not crash
    the oracle, and the invariant is what catches it if the rename reopens a leak.
    """
    declarations = declarations or {}
    statuses = statuses or {}
    vs = _variables()

    # Declared boolean statuses. Not suppressed - they are never imputed as income, and
    # they carry no dollar value, so they cannot leak into the income invariant.
    for pid, flags in statuses.items():
        for name in flags:
            if name in DECLARABLE_STATUSES and name in vs:
                situation["people"][pid][name] = {str(tax_year): True}

    for var in SUPPRESSED_PROGRAMS:
        v = vs.get(var)
        if v is None:
            continue
        if v.entity.key == "person":
            for pid, person in situation["people"].items():
                declared = declarations.get(pid, {}).get(var, 0.0)
                person[var] = _periodised(var, tax_year, declared)
        else:
            group = _ENTITY_GROUP.get(v.entity.key)
            if group and group in situation:
                for unit in situation[group].values():
                    unit[var] = _periodised(var, tax_year, 0.0)
    return situation


def assert_no_unstated_income(
    sim, month: str, stated_monthly_earned: float, stated_monthly_unearned: float = 0.0
) -> None:
    """The invariant: engine income must not exceed what the narrative stated.

    One-sided in both directions, for different reasons:

    * Earned - SNAP legitimately EXCLUDES some stated earnings, notably a student child's
      earned income (7 CFR 273.9(c)(7)). Engine earned income below the stated figure is
      correct behaviour. Only invented earnings are a leak.
    * Unearned - a declared benefit may be partly excluded or counted differently, so
      below-stated is fine. Above-stated means an imputed programme we did not suppress.
    """
    unearned = float(sim.calculate("snap_unearned_income", month)[0])
    earned = float(sim.calculate("snap_earned_income", month)[0])

    if unearned > stated_monthly_unearned + 1.0:
        raise TakeUpLeakError(
            f"engine gave the household ${unearned:,.2f}/mo of unearned income in {month} "
            f"but the narrative states ${stated_monthly_unearned:,.2f}. An imputed "
            f"programme is not in SUPPRESSED_PROGRAMS. Run scripts/probe_takeup.py."
        )
    if earned > stated_monthly_earned + 1.0:
        raise TakeUpLeakError(
            f"engine SNAP earned income ${earned:,.2f}/mo EXCEEDS stated "
            f"${stated_monthly_earned:,.2f}/mo in {month}; income was invented"
        )
