"""No scored answer may depend on a premise the case file does not state (LIMITS §35–§36).

This is the test that was missing when a third of the dev split's answer keys turned out to
rest on unstated premises: two adults keyed as married, students keyed at zero hours,
undocumented filers keyed with a citizen's SSN. The perturbation prober could not see any of
them, because it only varies the facts the generator withholds ON PURPOSE. This test instead
asks the engine what it actually READ.

Method, per representative household:

1. Build the situation exactly as the oracle does, and compute every scored variable with the
   engine's tracer on.
2. Collect every INPUT variable (no formula) the computation read. Anything the oracle did not
   set was read at the engine's default: an unstated premise.
3. Each premise must be accounted for in exactly one way:
   - STATED    - the oracle sets it and the rendered narrative states it.
   - CLOSURE   - a none-like default (0, False, empty) covered by the narrative's explicit
                 "nothing else" sentence, which must be present in the rendered text.
   - MOOT      - proved irrelevant HERE by perturbing it: scored answers must not move.
   - anything else fails, including MUST_STATE entries: defaults that are not none-like
     (the engine assumes relatedness, a citizen SSN, take-up) or whose "none" would be false
     (zero weekly hours for someone with earnings).
4. Filing structure is not an input variable, so it is checked separately: the engine's
   head / spouse / dependent roles must match a filing structure the narrative states.

It is marked xfail(strict=True) because the corpus is known to fail it (LIMITS §36). Strict:
the day it starts passing, the run FAILS until the marker is removed by hand. That is the
moment to verify its teeth and record it.
"""
from __future__ import annotations

import copy
import re

import pytest

pytest.importorskip("policyengine_us")

from redtape.config import DEV_SEED  # noqa: E402
from redtape.generator.households import generate  # noqa: E402
from redtape.generator.narratives import render  # noqa: E402
from redtape.oracle.policyengine_oracle import build_situation  # noqa: E402

SCORED = (("is_snap_eligible", "month"), ("snap", "month"),
          ("eitc", "year"), ("ctc_value", "year"))

# Non-none defaults, and none-like defaults whose "none" would be false for some households.
# Each is a premise the narrative must state and the oracle must set. Adding an entry is a
# decision, never a way to make the test pass: entries here FAIL until they are set.
MUST_STATE = {
    "is_related_to_head_or_spouse": "engine assumes every member is family (default True)",
    "ssn_card_type": "engine assumes a citizen's SSN card for everyone, incl. undocumented",
    "takes_up_eitc": "engine assumes the household claims EITC",
    "takes_up_snap_if_eligible": "engine assumes the household applies for SNAP",
    "would_file_if_eligible_for_refundable_credit": "engine assumes a return is filed",
    "weekly_hours_worked_before_lsr": "0 hours for a person with stated earnings is false; "
                                      "the SNAP student exemption reads it",
    "is_full_time_college_student": "narratives say 'enrolled full-time'; the flag stays False",
}

# Set by the oracle but, today, not stated by any narrative.
SET_BUT_UNSTATED = {
    "has_heating_cooling_expense": "oracle sets True for every household; never narrated",
}

# Proved irrelevant per household by perturbation, not asserted: (variable, perturbed value).
MOOT_PERTURBATIONS = {
    "divorce_year": 2020,
    "county_fips": "06037",
    "business_is_qualified": False,
    "self_employment_income_would_be_qualified": False,
    "farm_operations_income_would_be_qualified": False,
    "partnership_s_corp_income_would_be_qualified": False,
    "sstb_self_employment_income_would_be_qualified": False,
    "rental_income_would_be_qualified": False,
    "farm_rent_income_would_be_qualified": False,
    "estate_income_would_be_qualified": False,
}

CLOSURE_PATTERN = re.compile(r"no other (income|resources|expenses|circumstances)", re.I)
FILING_PATTERN = re.compile(r"\b(files|file) (a|their|one|separate|joint)\b.*\breturn", re.I)


def _none_like(v) -> bool:
    return v in (0, 0.0, False, "", None) or getattr(v, "name", "") == "NONE"


def _set_vars(situation) -> set[str]:
    out = set()
    for group, ents in situation.items():
        for e in ents.values():
            out |= {k for k in e if k != "members"}
    return out


def _representative(n_scan: int = 80) -> list:
    """First household of each risky shape, from the real generator."""
    want = {"single_adult": None, "two_adults_child": None, "student_earner": None,
            "undocumented": None}
    for i in range(n_scan):
        hh = generate(DEV_SEED, i)
        try:
            build_situation(hh)
        except Exception:
            continue            # withheld-fact households raise by design
        adults = [p for p in hh.people if p.age >= 18]
        kids = [p for p in hh.people if p.age < 18]
        if len(adults) == 1 and want["single_adult"] is None:
            want["single_adult"] = hh
        if len(adults) >= 2 and kids and want["two_adults_child"] is None:
            want["two_adults_child"] = hh
        if (any(p.is_higher_ed_student and p.employment_income > 0 for p in hh.people)
                and want["student_earner"] is None):
            want["student_earner"] = hh
        if (any(p.immigration_status.value == "UNDOCUMENTED" for p in hh.people)
                and want["undocumented"] is None):
            want["undocumented"] = hh
    return [(k, v) for k, v in want.items() if v is not None]


def _scored(sim, hh):
    return tuple(round(float(sim.calculate(v, hh.month if p == "month" else hh.tax_year)[0]), 2)
                 for v, p in SCORED)


def _problems(hh) -> list[str]:
    from policyengine_us import Simulation

    situation = build_situation(hh)
    narrative = render(hh)
    sim = Simulation(situation=situation)
    sim.trace = True
    baseline = _scored(sim, hh)
    tbs = sim.tax_benefit_system
    read = {n.name for n in sim.tracer.browse_trace()}
    inputs = {v for v in read if v in tbs.variables and not tbs.variables[v].formulas}
    defaulted = inputs - _set_vars(situation)

    problems = []
    for var in sorted(defaulted):
        if var in MUST_STATE:
            problems.append(f"{var}: defaulted, must be stated ({MUST_STATE[var]})")
        elif var in MOOT_PERTURBATIONS:
            s2 = copy.deepcopy(situation)
            ent = tbs.variables[var].entity.key
            year = str(hh.tax_year)
            groups = {"person": "people", "tax_unit": "tax_units", "spm_unit": "spm_units",
                      "household": "households", "family": "families",
                      "marital_unit": "marital_units"}[ent]
            for e in s2[groups].values():
                e[var] = {year: MOOT_PERTURBATIONS[var]}
            if _scored(Simulation(situation=s2), hh) != baseline:
                problems.append(f"{var}: registered MOOT but perturbing it moves the answer")
        elif _none_like(tbs.variables[var].default_value):
            if not CLOSURE_PATTERN.search(narrative):
                problems.append(f"{var}: none-like default, but the narrative has no "
                                f"closure sentence covering it")
        else:
            problems.append(f"{var}: UNCLASSIFIED non-none default "
                            f"{tbs.variables[var].default_value!r}")

    for var in sorted(_set_vars(situation) & set(SET_BUT_UNSTATED)):
        problems.append(f"{var}: set by the oracle, not stated ({SET_BUT_UNSTATED[var]})")

    # Filing structure: engine roles must be matched by a stated structure.
    year = hh.tax_year
    spouse = [bool(x) for x in sim.calculate("is_tax_unit_spouse", year)]
    dependent = [bool(x) for x in sim.calculate("is_tax_unit_dependent", year)]
    if (any(spouse) or any(dependent) or len(hh.people) > 1) and \
            not FILING_PATTERN.search(narrative):
        problems.append(f"filing structure: engine roles spouse={spouse} "
                        f"dependent={dependent}, narrative states none")
    return problems


@pytest.mark.xfail(strict=True, reason="LIMITS §36: the corpus is known to rest on unstated "
                                       "premises; remove this marker only when it passes")
@pytest.mark.parametrize("shape,hh", _representative(), ids=lambda x: x if isinstance(x, str) else "")
def test_no_scored_answer_rests_on_an_unstated_premise(shape, hh):
    assert _problems(hh) == []


def test_representative_shapes_were_all_found():
    """If the scan stops finding a shape, the test above silently covers less."""
    assert {k for k, _ in _representative()} == {
        "single_adult", "two_adults_child", "student_earner", "undocumented"}
