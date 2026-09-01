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

_STATUS_PHRASE = {
    "CITIZEN": ["a U.S. citizen", "a citizen"],
    "LEGAL_PERMANENT_RESIDENT": ["a lawful permanent resident", "a green card holder"],
    "CUBAN_HAITIAN_ENTRANT": ["a Cuban/Haitian entrant", "granted Cuban/Haitian entrant status"],
    "UNDOCUMENTED": ["undocumented", "without lawful immigration status"],
    "DACA": ["a DACA recipient", "covered by DACA"],
    "TPS": ["a Temporary Protected Status holder", "covered by TPS"],
}

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

    if p.employment_income is not None and p.employment_income > 0:
        bits.append(f"earns {_money(rng, p.employment_income, 'year')} from employment")
    elif p.employment_income == 0:
        bits.append(rng.choice(["has no earnings", "is not working", "reports no wages"]))

    if p.immigration_status is not None:
        bits.append(rng.choice(_STATUS_PHRASE[p.immigration_status.value]))

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

    if p.is_higher_ed_student is True:
        bits.append(
            rng.choice([
                "is enrolled full-time at a community college",
                "attends university more than half-time",
                "is enrolled more than half-time in a degree programme",
            ])
        )
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

    household_bits = []
    if hh.housing_cost is not None:
        household_bits.append(
            f"The household's shelter costs are {_money(rng, hh.housing_cost, 'year')}."
        )
    if hh.dependent_care_cost:
        household_bits.append(
            f"It pays {_money(rng, hh.dependent_care_cost, 'year')} for dependent care."
        )
    rng.shuffle(household_bits)
    lines += household_bits

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
