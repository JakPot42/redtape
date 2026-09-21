"""No scored answer may depend on a premise the case file does not state (LIMITS §35–§36).

This is the test that was missing when a third of the dev split's answer keys turned out to
rest on unstated premises: two adults keyed as married, students keyed at zero hours,
undocumented filers keyed with a citizen's SSN. The perturbation prober could not see any of
them, because it only varies the facts the generator withholds ON PURPOSE. This test asks
the engine what it actually READ.

Per representative household, built by the real generator, oracle and `render()`:

1. Compute every scored variable with the engine's tracer on, and collect every INPUT
   variable (no formula) the computation read.
2. Every input the ORACLE SET must have evidence in the rendered narrative (`_evidence`).
   An oracle input with no registered evidence check fails, so a new oracle input cannot
   slip in unstated.
3. Every input read at the engine's DEFAULT must be one of:
   - MUST_STATE    - fails. Non-none defaults, or a "none" that would be false.
   - MOOT          - proved irrelevant HERE by perturbation: scored answers must not move.
   - none-like     - covered by the closure sentences, which must be present verbatim.
   - anything else - fails as UNCLASSIFIED.
4. The engine's tax-unit roles and filing status must equal the structure parsed back out of
   the narrative's structure sentence.
5. Guards against the checker going blind: the trace must have read a realistic number of
   inputs, and every representative shape must be found.

Mutation checks that this passes for the RIGHT reason are recorded in LIMITS §36: removing a
narrative clause, an oracle setting, or a closure sentence each turns it red.
"""
from __future__ import annotations

import copy
import re

import pytest

pytest.importorskip("policyengine_us")

from redtape.config import DEV_SEED  # noqa: E402
from redtape.schemas import FIVE_YEAR_BAR  # noqa: E402
from redtape.generator.households import generate  # noqa: E402
from redtape.generator.narratives import render  # noqa: E402
from redtape.oracle.policyengine_oracle import build_situation  # noqa: E402

SCORED = (("is_snap_eligible", "month"), ("snap", "month"),
          ("eitc", "year"), ("ctc_value", "year"))

# Defaults that must NEVER be read at their default. Adding one is a decision; it makes the
# test fail until the oracle sets it and the narrative states it.
MUST_STATE = {
    "is_related_to_head_or_spouse": "engine assumes every member is family (default True)",
    "ssn_card_type": "engine assumes a citizen's SSN card for everyone, incl. undocumented",
    "has_tin": "engine assumes everyone has a TIN (an ITIN if no SSN)",
    "takes_up_eitc": "engine assumes the household claims EITC",
    "takes_up_snap_if_eligible": "engine assumes the household applies for SNAP",
    "would_file_if_eligible_for_refundable_credit": "engine assumes a return is filed",
    "weekly_hours_worked_before_lsr": "0 hours for a stated earner is false; 7 CFR 273.5(b)(5)",
    "is_full_time_college_student": "7 CFR 273.5(b)(10) reads it",
    "is_tax_unit_head": "the engine would infer it from age order",
    "is_tax_unit_spouse": "the engine would make any second adult a spouse",
    "is_tax_unit_dependent": "the engine would infer it",
}

# Proved irrelevant per household by perturbation, not asserted.
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

# EXPECTATIONS ARE OWNED BY THIS TEST, NOT IMPORTED FROM THE RENDERER. The first version
# imported CLOSURE_SENTENCES and the phrase tables from narratives.py, so shrinking a closure
# sentence, or mapping "itin" to citizen wording, changed the narrative AND the expectation
# together, and the test stayed green (mutation check, LIMITS §36). What each premise must SAY
# is pinned here, independently of how the renderer happens to say it.
REQUIRED_CLOSURE = (
    "nobody in the household has any income",          # other income sources: none
    "nobody has savings or other assets",               # assets / resources: none
    "no expenses other than shelter, heating and cooling, and dependent care",
    "work-study",                                       # 7 CFR 273.5(b)(6)
    "on-the-job training",                              # 273.5(b)(7)
    "employment-and-training programme",                # 273.5(b)(11)
    "CalWORKs or other cash assistance",                # TANF, 273.5(b)(3)
)
REQUIRED_CLAIM = ("applying for SNAP", "federal tax return", "claiming every credit")
# What each SSN status must convey: (must contain all, must contain none).
SSN_MEANING = {
    # Not the bare word "work": "is not working" is on many citizens' lines. That was a
    # false failure in this test's first hardened version, caught by running it on correct
    # code before trusting it.
    "citizen": (("Social Security number",),
                ("no Social Security", "ITIN", "valid for work", "work-authorized")),
    "work": (("Social Security number",), ("no Social Security", "ITIN")),
    "itin": (("ITIN", "no Social Security number"), ()),
}
SSN_WORK_WORDS = ("valid for work", "work-authorized")
# What each generated immigration status must convey (any of).
STATUS_MEANING = {
    "CITIZEN": ("citizen",),
    "LEGAL_PERMANENT_RESIDENT": ("permanent resident", "green card"),
    "CUBAN_HAITIAN_ENTRANT": ("Cuban/Haitian",),
    "REFUGEE": ("refugee",),
    "ASYLEE": ("asylee", "asylum"),
    "UNDOCUMENTED": ("undocumented", "without lawful immigration status"),
}

_GROUP = {"person": "people", "tax_unit": "tax_units", "spm_unit": "spm_units",
          "household": "households", "family": "families", "marital_unit": "marital_units"}
_NO_EARNINGS = ("has no earnings", "is not working", "reports no wages")
_NOT_STUDENT = ("not enrolled in college", "not attending a degree", "not a student")
_SUPPRESSED = ("ssi", "social_security", "social_security_disability",
               "unemployment_compensation", "tanf", "ca_tanf", "ca_state_supplement")


def _person_lines(narrative: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in re.finditer(r"^Person (p\d+) .*$", narrative, re.M)}


def _any(phrases, text) -> bool:
    return any(ph in text for ph in phrases)


def _closure(narr: str) -> bool:
    return all(s in narr for s in REQUIRED_CLOSURE)


def _ssn_stated(status: str, line: str) -> bool:
    must, must_not = SSN_MEANING[status]
    ok = all(m in line for m in must) and not any(m in line for m in must_not)
    if status == "work":
        ok = ok and _any(SSN_WORK_WORDS, line)
    return ok


def _evidence(var: str, value, hh, narr: str, pid: str | None) -> bool:
    """Is the value the oracle set for `var` stated in the narrative? Checked on the
    person's own line where the variable is per person. Raises KeyError for a variable with
    no registered check."""
    line = _person_lines(narr).get(pid, "")
    p = next((x for x in hh.people if x.person_id == pid), None)
    if var == "age":
        return line.startswith(f"Person {pid} is {p.age},")
    if var == "employment_income":
        return ("earns $" in line) if p.employment_income > 0 else _any(_NO_EARNINGS, line)
    if var == "weekly_hours_worked_before_lsr":
        if p.employment_income > 0:
            return f"working {int(p.weekly_hours)} hour" in line
        return _any(_NO_EARNINGS, line)
    if var == "immigration_status":
        return _any(STATUS_MEANING[p.immigration_status.value], line)
    if var == "years_since_us_entry":
        # Set from the stated start year; the narrative says "since <year>", "in <year>",
        # or "since birth" (docs/LIMITS.md 39).
        return (f"since {p.status_since}" in line or f"in {p.status_since}" in line
                or ("since birth" in line and p.status_since == hh.tax_year - p.age))
    if var in ("ssn_card_type", "has_tin"):
        return _ssn_stated(p.ssn_status, line)
    if var == "is_disabled":
        if p.is_disabled:
            return "reports a disability" in line
        return _any(("reports no disability", "does not report a disability"), line)
    if var in ("is_snap_higher_ed_student", "is_full_time_college_student"):
        if not p.is_higher_ed_student:
            return _any(_NOT_STUDENT, line)
        return ("full-time" in line and "not full-time" not in line) == bool(p.student_full_time)
    if var in ("is_tax_unit_head", "is_tax_unit_spouse", "is_tax_unit_dependent",
               "is_related_to_head_or_spouse"):
        return _stated_structure(narr) is not None      # values compared in step 4
    if var == "housing_cost":
        return "shelter costs are" in narr
    if var == "childcare_expenses":
        return ("for dependent care" in narr and "pays nothing" not in narr) if value \
            else "pays nothing for dependent care" in narr
    if var == "has_heating_cooling_expense":
        return ("pays for heating and cooling" in narr) if value \
            else "does not pay for heating or cooling" in narr
    if var in ("takes_up_eitc", "would_file_if_eligible_for_refundable_credit",
               "takes_up_snap_if_eligible"):
        return all(c in narr for c in REQUIRED_CLAIM) and f"{hh.tax_year} federal" in narr
    if var == "state_name":
        return "California" in narr
    # Take-up suppression zeroes these. Zero is stated by the closure sentences; a declared
    # (non-zero) amount must appear on the person's line.
    if var in _SUPPRESSED:
        return _closure(narr) if not value else "receives $" in line
    raise KeyError(var)


def _stated_structure(narr: str):
    """(married, children) parsed from the structure sentence, or None."""
    if "p1 and p2 are married to each other and file a joint federal tax return." in narr:
        married = True
    elif "p1 is not married, is the only adult in the household" in narr:
        married = False
    else:
        return None
    m = re.search(r"((?:p\d+)(?:(?:, | and )p\d+)*) (?:are|is) (?:their|p1's) child", narr)
    kids = tuple(re.findall(r"p\d+", m.group(1))) if m else ()
    return married, kids


def _set_vars(situation) -> dict[str, list]:
    """var -> [(person_id or None, value), ...] for everything the oracle set."""
    out: dict[str, list] = {}
    for group, ents in situation.items():
        for eid, e in ents.items():
            for k, v in e.items():
                if k == "members":
                    continue
                out.setdefault(k, []).append((eid if group == "people" else None,
                                              next(iter(v.values()))))
    return out


def _none_like(v) -> bool:
    return v in (0, 0.0, False, "", None) or getattr(v, "name", "") == "NONE"


SHAPES = ("single_no_children", "single_parent", "married_with_children",
          "student_under_20h", "student_20h_plus", "undocumented_adult",
          "mixed_status_couple", "lpr_adult")


def _representative(n_scan: int = 400):
    want: dict[str, object] = dict.fromkeys(SHAPES)
    for i in range(n_scan):
        hh = generate(DEV_SEED, i)
        a, kids = hh.adults, hh.children
        hit = {
            "single_no_children": hh.household_type == "single_adult" and not kids,
            "single_parent": hh.household_type == "single_adult" and bool(kids),
            "married_with_children": hh.household_type == "married_couple" and bool(kids),
            "student_under_20h": any(p.is_higher_ed_student and p.weekly_hours < 20
                                     for p in a),
            "student_20h_plus": any(p.is_higher_ed_student and p.weekly_hours >= 20
                                    for p in a),
            "undocumented_adult": any(p.ssn_status == "itin" for p in a),
            "mixed_status_couple": hh.household_type == "married_couple"
            and len({p.ssn_status == "itin" for p in a}) == 2,
            "lpr_adult": any(p.immigration_status is not None
                             and p.immigration_status.value == "LEGAL_PERMANENT_RESIDENT"
                             for p in a),
        }
        for k, h in hit.items():
            if h and want[k] is None:
                want[k] = hh
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
    set_vars = _set_vars(situation)

    problems = []
    if len(inputs) < 200:
        problems.append(f"trace read only {len(inputs)} inputs; the checker may be blind")

    # 1b. every MUST_STATE premise is SET by the oracle, for every person/entity - not merely
    # absent from the defaulted inputs. Role variables have formulas, so the trace never
    # lists them as inputs, and for v0's two shapes the engine's inference happens to give
    # the right roles: deleting the oracle's explicit spouse role left the first version of
    # this test green (mutation check). "Neither may infer" has to be checked directly.
    n_people = len(hh.people)
    for var in MUST_STATE:
        got = set_vars.get(var, [])
        per_person = tbs.variables[var].entity.key == "person"
        if not got or (per_person and len(got) != n_people):
            problems.append(f"{var}: not set explicitly by the oracle "
                            f"({len(got)} entries); the engine would infer or default it")

    # 2. everything the oracle set is stated
    for var, entries in sorted(set_vars.items()):
        for pid, value in entries:
            try:
                ok = _evidence(var, value, hh, narrative, pid)
            except KeyError:
                problems.append(f"{var}: set by the oracle with NO registered evidence check")
                break
            if not ok:
                problems.append(f"{var}[{pid}]={value!r}: set by the oracle, not stated")

    # 3. everything read at a default is accounted for
    for var in sorted(inputs - set(set_vars)):
        if var in MUST_STATE:
            problems.append(f"{var}: read at its DEFAULT, must be stated ({MUST_STATE[var]})")
        elif var in MOOT_PERTURBATIONS:
            s2 = copy.deepcopy(situation)
            for e in s2[_GROUP[tbs.variables[var].entity.key]].values():
                e[var] = {str(hh.tax_year): MOOT_PERTURBATIONS[var]}
            if _scored(Simulation(situation=s2), hh) != baseline:
                problems.append(f"{var}: registered MOOT but perturbing it moves the answer")
        elif _none_like(tbs.variables[var].default_value):
            if not _closure(narrative):
                problems.append(f"{var}: none-like default with no closure sentence")
        else:
            problems.append(f"{var}: UNCLASSIFIED non-none default "
                            f"{tbs.variables[var].default_value!r}")

    # 3b. The SNAP five-year bar (8 U.S.C. 1613(a)): the engine does not model it, so the
    # corpus keeps every lawful status old enough that it cannot apply, and states the year.
    # Without this the answer key for a recently-arrived LPR adult is legally wrong - the
    # gap GPT-5.6 Sol found by asking for the fact 13 times (docs/LIMITS.md 39).
    for person in hh.people:
        if person.immigration_status is None or person.age is None:
            continue
        if person.immigration_status.value != "LEGAL_PERMANENT_RESIDENT" or person.age < 18:
            continue
        if person.status_since is None:
            problems.append(f"{person.person_id}: LPR adult with no stated status start year")
        elif hh.tax_year - person.status_since < FIVE_YEAR_BAR:
            problems.append(
                f"{person.person_id}: LPR adult whose status began {person.status_since}, "
                f"only {hh.tax_year - person.status_since} years before the tax year; the "
                f"five-year bar would apply and the engine does not model it")

    # 4. engine roles == stated structure
    stated = _stated_structure(narrative)
    if stated is None:
        problems.append("no structure sentence in the narrative")
        return problems
    married, kids = stated
    year = hh.tax_year
    ids = [p.person_id for p in hh.people]
    head = [bool(x) for x in sim.calculate("is_tax_unit_head", year)]
    spouse = [bool(x) for x in sim.calculate("is_tax_unit_spouse", year)]
    dep = [bool(x) for x in sim.calculate("is_tax_unit_dependent", year)]
    want = ([i == "p1" for i in ids], [married and i == "p2" for i in ids],
            [i in kids for i in ids])
    if (head, spouse, dep) != want:
        problems.append(f"roles: engine head={head} spouse={spouse} dependent={dep}; "
                        f"narrative says married={married} children={kids}")
    filing = sim.calculate("filing_status", year).decode_to_str()[0]
    if (filing == "JOINT") != married:
        problems.append(f"filing status {filing} contradicts stated married={married}")
    if set(kids) | {"p1"} | ({"p2"} if married else set()) != set(ids):
        problems.append(f"structure sentence does not account for everyone: {ids}")
    return problems


REPRESENTATIVE = _representative()


@pytest.mark.parametrize("shape,hh", REPRESENTATIVE, ids=[s for s, _ in REPRESENTATIVE])
def test_no_scored_answer_rests_on_an_unstated_premise(shape, hh):
    assert _problems(hh) == []


def test_representative_shapes_were_all_found():
    """If the scan stops finding a shape, the test above silently covers less."""
    assert {k for k, _ in REPRESENTATIVE} == set(SHAPES)
