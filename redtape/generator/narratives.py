"""Render a household record as a natural-language case file.

Phrasing, field order and units are varied from the same `(seed, index)` stream that
produced the household, so the rendering is reproducible but the answer cannot be
inferred from formatting (SPEC.md 4).

A WITHHELD fact is simply absent from the narrative. It is never described as unknown,
because a case file that says "income not provided" makes the omission trivially
detectable; the point is whether the model notices on its own.
"""

from __future__ import annotations

import random

from redtape.schemas import Household, Person

_MONTHS = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}

# Must cover every value in SAFE_IMMIGRATION_STATUSES. The lookup in _person_sentence is
# a strict dict access on purpose: a missing status must crash the build rather than render
# a person with no status clause. An absent clause means "withheld" everywhere else in this
# renderer, so a silent fallback would make a STATED status indistinguishable from a
# withheld one - the exact pathology recorded in docs/LIMITS.md 25.
#
# The five HR 1-affected statuses were added 2026-09-16 when the corpus was re-widened
# (docs/LIMITS.md 16 and 31). They were missing on the first attempt and 12% of generated
# households raised KeyError with no test catching it;
# `test_every_safe_status_has_narrative_prose` exists so that cannot recur.
#
# Phrasing states the status only. It must never hint at an eligibility conclusion.
_STATUS_PHRASE = {
    "CITIZEN": ["a U.S. citizen", "a citizen"],
    "LEGAL_PERMANENT_RESIDENT": ["a lawful permanent resident", "a green card holder"],
    "CUBAN_HAITIAN_ENTRANT": ["a Cuban/Haitian entrant", "granted Cuban/Haitian entrant status"],
    "REFUGEE": ["a refugee", "was admitted as a refugee"],
    "ASYLEE": ["an asylee", "was granted asylum"],
    "DEPORTATION_WITHHELD": [
        "has had deportation withheld",
        "was granted withholding of removal",
    ],
    "CONDITIONAL_ENTRANT": [
        "a conditional entrant",
        "was admitted as a conditional entrant",
    ],
    "PAROLED_ONE_YEAR": [
        "was paroled into the United States for at least one year",
        "a parolee admitted for at least one year",
    ],
    "UNDOCUMENTED": ["undocumented", "without lawful immigration status"],
    "DACA": ["a DACA recipient", "covered by DACA"],
    "TPS": ["a Temporary Protected Status holder", "covered by TPS"],
}

# SSN status, keyed by schemas.SSN_BY_STATUS values. Stated for every person whose status
# is stated; the engine default was a citizen's SSN card for everyone (LIMITS §36).
_SSN_PHRASE = {
    "citizen": ["has a Social Security number", "holds a Social Security number"],
    "work": ["has a Social Security number valid for work",
             "holds a work-authorized Social Security number"],
    "itin": ["has no Social Security number and files taxes with an ITIN",
             "has an ITIN but no Social Security number"],
}

# Stated in every case file (LIMITS §36). Each closes a class of premise the engine would
# otherwise take at a default. They name the categories that DO exist (employment income,
# shelter, dependent care) rather than saying "nothing else", because a withheld amount in
# one of those categories must stay unknown, not be asserted zero.
CLOSURE_SENTENCES = (
    "Other than employment income and any benefits stated above, nobody in the household "
    "has any income, and nobody has savings or other assets.",
    "The household has no expenses other than shelter, heating and cooling, and "
    "dependent care.",
    "Nobody is in work-study, on-the-job training or an employment-and-training programme, "
    "and nobody receives CalWORKs or other cash assistance.",
)
CLAIM_SENTENCE = (
    "The household is applying for SNAP, and will file a {year} federal tax return "
    "claiming every credit it is entitled to."
)
# 7 CFR 273.5(b)(9): a student caring for a child aged 6-11 is exempt only where the state
# agency has determined adequate child care is NOT available. The engine does not model
# (b)(9) ("not modeled", its own comment), i.e. treats it as never met; this makes that
# premise stated rather than assumed.
CHILD_CARE_SENTENCE = "Adequate child care is available for the children."

_OPENERS = [
    "Case file {hid}. The household applied for benefits in {state} for {month} {year}.",
    "Application {hid}, {state}, benefit month {month} {year}.",
    "{state} household {hid}. Determination is for {month} {year}.",
    "Intake record {hid}. Benefit month: {month} {year}. State: {state}.",
]


def _rng(hh: Household) -> random.Random:
    return random.Random(f"redtape/narrative/{hh.seed}/{hh.index}")


def _money(rng: random.Random, amount: float, per: str) -> str:
    """Vary the units the figure is stated in, without changing its value."""
    if per == "year" and rng.random() < 0.45:
        return f"${amount / 12:,.0f} per month"
    if per == "month" and rng.random() < 0.3:
        return f"${amount * 12:,.0f} per year"
    return f"${amount:,.0f} per {per}"


def _person_sentence(rng: random.Random, p: Person, is_first: bool) -> str:
    bits = []
    who = f"Person {p.person_id}"
    if p.age is not None:
        bits.append(f"{who} is {p.age}")
    else:
        bits.append(f"{who} is in the household")

    # Earnings and weekly hours are one fact for withholding (generator.withhold): both
    # stated or both silent. Hours were an unstated engine default of 0 until 2026-09-19,
    # and the SNAP student exemption (7 CFR 273.5(b)(5)) reads them.
    if p.employment_income is not None and p.employment_income > 0:
        hours = int(p.weekly_hours)
        bits.append(
            f"earns {_money(rng, p.employment_income, 'year')} from employment, "
            f"working {hours} hour{'s' if hours != 1 else ''} a week"
        )
    elif p.employment_income == 0:
        bits.append(rng.choice(["has no earnings", "is not working", "reports no wages"]))

    if p.immigration_status is not None:
        bits.append(rng.choice(_STATUS_PHRASE[p.immigration_status.value]))
        # Derived from status, so silent exactly when the status is withheld.
        bits.append(rng.choice(_SSN_PHRASE[p.ssn_status]))

    # Boolean facts are stated in BOTH directions, and omitted only when withheld.
    #
    # These used to render only when true, so "not a student" and "student status was
    # withheld" produced identical text. That made the eligibility-flip class - the
    # scarcest and most valuable T1b class - unanswerable: a reader had no way to know a
    # fact was missing, so correct abstention was not achievable from the narrative, and a
    # model could only have scored well by abstaining on every case mentioning no student.
    #
    # It is exactly the pathology in docs/LIMITS.md 3 ("omitting a fact and stating it as
    # zero are indistinguishable") reproduced in our own renderer, for the one fact
    # confirmed to flip eligibility. Age, income and immigration status never had the
    # problem, because for those an absent clause is itself the signal.
    #
    # `None` means withheld and stays silent. That silence is now informative.
    if p.is_disabled is True:
        bits.append("reports a disability")
    elif p.is_disabled is False:
        bits.append(rng.choice(["reports no disability", "does not report a disability"]))

    # Intensity is now a FIELD, not a phrase picked at random. The old renderer said
    # "enrolled full-time" for students the oracle treated as not full-time, which
    # contradicted the key (7 CFR 273.5(b)(10) reads full-time status).
    if p.is_higher_ed_student is True:
        bits.append(rng.choice(
            ["is enrolled full-time at a community college",
             "attends university full-time"]
            if p.student_full_time else
            ["is enrolled at least half-time, but not full-time, in a degree programme",
             "attends college half-time"]
        ))
    elif p.is_higher_ed_student is False:
        bits.append(
            rng.choice([
                "is not enrolled in college",
                "is not attending a degree programme",
                "is not a student",
            ])
        )
    for b in p.declared_benefits:
        label = {
            "ssi": "Supplemental Security Income",
            "social_security_disability": "Social Security Disability Insurance",
            "social_security": "Social Security",
        }.get(b.program, b.program)
        bits.append(f"receives {_money(rng, b.annual_amount, 'year')} in {label}")
    for st in p.declared_statuses:
        label = {
            "is_permanently_disabled_veteran": "is a permanently disabled veteran",
            "is_ssi_disabled": "has been determined disabled for SSI purposes",
            "is_surviving_spouse_of_disabled_veteran": "is the surviving spouse of a disabled veteran",
            "is_surviving_child_of_disabled_veteran": "is the surviving child of a disabled veteran",
        }.get(st, st)
        bits.append(label)

    return ", ".join(bits) + "."


def structure_sentence(hh: Household) -> str:
    """Who is whose, who is married, who files together. Fixed wording, because
    tests/test_unstated_premises.py parses it back and compares it with the roles the
    engine used; it carries no answer information beyond the facts it states."""
    kids = [p.person_id for p in hh.children]
    if hh.household_type == "married_couple":
        s = ("p1 and p2 are married to each other and file a joint federal tax return. "
             "Everyone listed lives together all year and buys and prepares food together.")
        if kids:
            s += f" {_join(kids)} {'are their children' if len(kids) > 1 else 'is their child'}" \
                 f", and {'are' if len(kids) > 1 else 'is'} claimed as their dependent" \
                 f"{'s' if len(kids) > 1 else ''}."
        return s
    s = ("p1 is not married, is the only adult in the household, and files their own "
         "federal tax return. Everyone listed lives together all year and buys and "
         "prepares food together.")
    if kids:
        s += f" {_join(kids)} {'are' if len(kids) > 1 else 'is'} p1's " \
             f"{'children' if len(kids) > 1 else 'child'}, claimed as p1's dependent" \
             f"{'s' if len(kids) > 1 else ''}."
    return s


def _join(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " and " + ids[-1]


def render(hh: Household) -> str:
    rng = _rng(hh)
    year, mm = hh.month.split("-")
    opener = rng.choice(_OPENERS).format(
        hid=hh.household_id, state="California", month=_MONTHS[mm], year=year
    )

    people = list(hh.people)
    if rng.random() < 0.4:
        people = people[:1] + list(reversed(people[1:]))

    lines = [opener, ""]
    lines += [_person_sentence(rng, p, i == 0) for i, p in enumerate(people)]
    lines.append(structure_sentence(hh))

    household_bits = []
    if hh.housing_cost is not None:
        household_bits.append(
            f"The household's shelter costs are {_money(rng, hh.housing_cost, 'year')}."
        )
    # A ZERO is stated. This used to render only when non-zero, so "pays nothing" and
    # "withheld" were the same text - the LIMITS §3 boolean pathology, on one of the six
    # withholdable facts. Only None is silent now.
    if hh.dependent_care_cost:
        household_bits.append(
            f"It pays {_money(rng, hh.dependent_care_cost, 'year')} for dependent care."
        )
    elif hh.dependent_care_cost == 0:
        household_bits.append("It pays nothing for dependent care.")
    household_bits.append(
        "The household pays for heating and cooling."
        if hh.pays_heating_cooling else
        "The household does not pay for heating or cooling."
    )
    rng.shuffle(household_bits)
    lines += household_bits
    lines += list(CLOSURE_SENTENCES)
    if hh.children:
        lines.append(CHILD_CARE_SENTENCE)
    lines.append(CLAIM_SENTENCE.format(year=hh.tax_year))

    # Irrelevant details, so length and specificity do not leak the answer.
    distractors = [
        "The applicant heard about the programme from a neighbour.",
        "The case was assigned to the downtown office.",
        "A previous application was withdrawn two years ago.",
        "The household has lived at the current address for several years.",
        "Contact is preferred by text message.",
        "An interpreter was not required.",
    ]
    rng.shuffle(distractors)
    lines += distractors[: rng.randint(0, 2)]

    lines += [
        "",
        f"Determine SNAP for {_MONTHS[mm]} {year}, and EITC and CTC for tax year {year}.",
    ]
    return "\n".join(lines)
