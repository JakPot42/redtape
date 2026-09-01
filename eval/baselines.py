"""Five trivial and heuristic baselines. SPEC.md 5.

**Baselines see the prompt and nothing else.** They never read the task record's
`withheld_fact`, `determinability` or answer key - those are answer-key information, and a
baseline that consulted them would be measuring the generator rather than the difficulty
of the task. Everything below is parsed out of the narrative text, exactly as a model would
have to.

The five:

| name             | abstention policy        | numeric policy                       |
|------------------|--------------------------|--------------------------------------|
| `always_abstain` | abstain on all three     | rules-only                           |
| `never_abstain`  | never abstain            | rules-only                           |
| `always_eligible`| never abstain            | eligible, maximum allotment          |
| `never_eligible` | never abstain            | ineligible, everything zero          |
| `rules_only`     | abstain on SNAP when no  | gross-income-vs-FPL test plus the     |
|                  | shelter cost is stated   | SNAP net income benefit formula      |

`always_abstain` and `never_abstain` share the rules-only numeric policy on purpose, so the
difference between them isolates the abstention axis and nothing else. That pair is the
control for headline metric (b).

**The rules-only baseline is given the engine's own FPL table.** That makes it stronger
than a hand-built heuristic would be, which is the conservative direction: a trivial
baseline should be given every advantage before anyone claims a model beat it. The SNAP
allotment, standard deduction, shelter cap and CA utility allowance come from
`tests/test_parameter_drift.py`'s externally-published table, not from the engine.

Known and deliberate gap: the rules-only baseline does not model EITC and returns 0 for it.
Reproducing the EITC schedule is not "rules-only heuristic" territory, and a baseline that
scores zero on one program is an honest floor rather than a rigged one. Its per-program
breakdown is in the results file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from redtape.schemas import (
    SCORED_PROGRAMS,
    AnnualAmount,
    CannotDetermine,
    MedicaidAnswer,
    SnapAnswer,
    T1Answer,
)

# ---------------------------------------------------------------------------------
# Published SNAP parameters. Sources are the same reviewer-checked ones used by
# tests/test_parameter_drift.py:
#   [D] LSNC maximum allotments as of 10/01/2024 (FFY2025)
#   [B][E] Santa Clara County DEBS chart / CDSS ACIN I-46-25 (FFY2026)
#   [A] LSNC Guide to CalFresh Benefits (FFY2025)
#   [F] FNS SNAP FY2026 COLA memo; [G] CBPP quick guide endnote 9
# FFY2025 runs 2024-10-01..2025-09-30; FFY2026 begins 2025-10-01.
# ---------------------------------------------------------------------------------
MAX_ALLOTMENT = {
    2025: {1: 292, 2: 536, 3: 768, 4: 975, 5: 1158, 6: 1390, 7: 1536, 8: 1756},
    2026: {1: 298, 2: 546, 3: 785, 4: 994, 5: 1183, 6: 1421, 7: 1571, 8: 1789},
}
STANDARD_DEDUCTION = {
    2025: {1: 204, 2: 204, 3: 204, 4: 217, 5: 254, 6: 291},
    2026: {1: 209, 2: 209, 3: 209, 4: 223, 5: 261, 6: 299},
}
SHELTER_CAP = {2025: 712, 2026: 744}
SUA_CA = {2025: 645, 2026: 663}
EARNED_INCOME_DEDUCTION = 0.20
SHELTER_THRESHOLD = 0.50
BENEFIT_REDUCTION = 0.30
MIN_BENEFIT_SMALL_HH = 23  # sizes 1-2, [A]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _ffy(month: str) -> int:
    """Federal fiscal year for a "YYYY-MM" month. FFY2026 begins 2025-10-01."""
    y, m = (int(x) for x in month.split("-"))
    return y + 1 if m >= 10 else y


def _table(t: dict, size: int, key: int):
    row = t[key]
    top = max(row)
    if size <= top:
        return row[size]
    # Above the published table, SNAP adds a fixed increment per extra person. Rather
    # than invent one, extend by the last published step - flagged as an approximation.
    step = row[top] - row[top - 1]
    return row[top] + step * (size - top)


# ---------------------------------------------------------------------------------
# Narrative parsing. The renderer's phrasings are ours, so this is a reader for a
# known format, not general NLP. It is deliberately conservative: anything it cannot
# read is reported as absent, which is what a heuristic agent would conclude too.
# ---------------------------------------------------------------------------------

_MONEY = r"\$([\d,]+)\s+per\s+(year|month)"


@dataclass(frozen=True)
class ReadNarrative:
    month: str
    year: int
    n_people: int
    n_children: int
    ages: tuple[int, ...]
    monthly_earned: float
    shelter_stated: bool
    monthly_shelter: float
    any_age_withheld: bool
    any_income_withheld: bool
    any_status_withheld: bool
    people: tuple[dict, ...] = ()
    """One entry per person line, IN ORDER, with None where the narrative states nothing.

    `ages` cannot carry this: it holds only the ages that parsed, so a withheld age does
    not leave a gap - it shifts every later age one position earlier and silently
    reattaches it to the wrong person. And a one-person household with a withheld age
    produces an empty tuple, which built a payload with no people at all and crashed the
    engine with "No person found".
    """


def _money_monthly(text: str) -> float | None:
    m = re.search(_MONEY, text)
    if not m:
        return None
    amount = float(m.group(1).replace(",", ""))
    return amount / 12 if m.group(2) == "year" else amount


def read_narrative(prompt: str) -> ReadNarrative:
    month_match = re.search(
        r"\b(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", prompt, re.IGNORECASE
    )
    if month_match:
        mm = _MONTHS[month_match.group(1).lower()]
        year = int(month_match.group(2))
    else:  # pragma: no cover - every opener states a month
        mm, year = 1, 2025

    ages: list[int] = []
    people: list[dict] = []
    monthly_earned = 0.0
    n_people = 0
    any_age_withheld = False
    any_income_withheld = False
    any_status_withheld = False

    for line in prompt.splitlines():
        line = line.strip()
        if not line.startswith("Person p"):
            continue
        n_people += 1
        person: dict = {"age": None, "employment_income": None,
                        "immigration_status": None}
        pid_m = re.match(r"Person (p\d+) ", line)
        person["person_id"] = pid_m.group(1) if pid_m else f"p{n_people}"

        age_m = re.match(r"Person p\d+ is (\d+)", line)
        if age_m:
            ages.append(int(age_m.group(1)))
            person["age"] = int(age_m.group(1))
        else:
            any_age_withheld = True

        if "earns" in line:
            got = _money_monthly(line[line.index("earns"):])
            if got is not None:
                monthly_earned += got
                person["employment_income"] = got * 12
        elif re.search(r"has no earnings|is not working|reports no wages", line):
            person["employment_income"] = 0.0
        else:
            any_income_withheld = True

        # Every phrasing the generator can emit for immigration status. A person line
        # carrying none of them has had the status withheld - which is a DIFFERENT thing
        # from stating CITIZEN, and the whole point of the unknowns condition is that the
        # tool must not collapse the two the way the engine does (LIMITS 3).
        if not re.search(
            r"citizen|lawful permanent resident|green card holder|Cuban/Haitian|"
            r"undocumented|without lawful immigration status|DACA|"
            r"Temporary Protected Status|covered by TPS",
            line, re.IGNORECASE,
        ):
            any_status_withheld = True
        else:
            person["immigration_status"] = "CITIZEN"

        people.append(person)

    shelter_stated = "shelter costs are" in prompt
    monthly_shelter = 0.0
    if shelter_stated:
        got = _money_monthly(prompt[prompt.index("shelter costs are"):])
        monthly_shelter = got or 0.0

    return ReadNarrative(
        month=f"{year}-{mm:02d}",
        year=year,
        n_people=max(n_people, 1),
        n_children=sum(1 for a in ages if a < 18),
        ages=tuple(ages),
        monthly_earned=monthly_earned,
        shelter_stated=shelter_stated,
        monthly_shelter=monthly_shelter,
        any_age_withheld=any_age_withheld,
        any_income_withheld=any_income_withheld,
        any_status_withheld=any_status_withheld,
        people=tuple(people),
    )


# ---------------------------------------------------------------------------------
# The heuristic
# ---------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _fpl_table():
    """Annual federal poverty line by household size, from the engine's parameters.

    Read once. Using the engine's table here makes the baseline stronger, not weaker:
    the trivial baseline should be given the best version of itself.
    """
    from policyengine_us import CountryTaxBenefitSystem

    p = CountryTaxBenefitSystem().parameters
    node = p.gov.hhs.fpg("2025-01-01")
    first = float(node.first_person["CONTIGUOUS_US"])
    addl = float(node.additional_person["CONTIGUOUS_US"])
    return first, addl


def fpl_annual(size: int) -> float:
    first, addl = _fpl_table()
    return first + addl * (size - 1)


def snap_estimate(r: ReadNarrative) -> tuple[bool, float]:
    """Gross-income-vs-130%-FPL test, then the SNAP net income benefit formula."""
    ffy = _ffy(r.month)
    size = r.n_people
    gross = r.monthly_earned
    limit = 1.30 * fpl_annual(size) / 12
    if gross > limit:
        return False, 0.0

    earned_ded = EARNED_INCOME_DEDUCTION * r.monthly_earned
    std = _table(STANDARD_DEDUCTION, size, ffy)
    after = max(gross - earned_ded - std, 0.0)

    shelter = r.monthly_shelter + SUA_CA[ffy]
    excess = max(shelter - SHELTER_THRESHOLD * after, 0.0)
    excess = min(excess, _table(SHELTER_CAP, size, ffy) if isinstance(SHELTER_CAP[ffy], dict)
                 else SHELTER_CAP[ffy])

    net = max(after - excess, 0.0)
    allotment = _table(MAX_ALLOTMENT, size, ffy)
    benefit = max(allotment - BENEFIT_REDUCTION * net, 0.0)
    if benefit <= 0 and size <= 2:
        benefit = MIN_BENEFIT_SMALL_HH
    return benefit > 0, round(benefit, 2)


def ctc_estimate(r: ReadNarrative) -> float:
    """Additional CTC: 15% of earnings over $2,500, capped at $1,700 per child.

    The refundable path only, which is what a zero- or low-tax household actually
    receives. No attempt at the non-refundable portion.
    """
    if not r.n_children:
        return 0.0
    earned = r.monthly_earned * 12
    return round(min(0.15 * max(earned - 2_500, 0.0), 1_700 * r.n_children), 2)


def _answer(r: ReadNarrative, *, eligible: bool, snap: float, eitc: float, ctc: float,
            abstain=()) -> T1Answer:
    return T1Answer(
        snap=SnapAnswer(period_label=r.month, eligible=eligible, benefit=snap),
        # Medicaid is unscored; a baseline states the shape and nothing more.
        medicaid=MedicaidAnswer(
            period_label=str(r.year),
            person_eligible={f"p{i + 1}": False for i in range(r.n_people)},
        ),
        eitc=AnnualAmount(period_label=str(r.year), amount=eitc),
        ctc=AnnualAmount(period_label=str(r.year), amount=ctc),
        cannot_determine=tuple(
            CannotDetermine(program=p, missing_fact="unstated") for p in abstain
        ),
    )


# ---------------------------------------------------------------------------------
# The five
# ---------------------------------------------------------------------------------


def always_abstain(prompt: str) -> T1Answer:
    r = read_narrative(prompt)
    eligible, snap = snap_estimate(r)
    return _answer(r, eligible=eligible, snap=snap, eitc=0.0, ctc=ctc_estimate(r),
                   abstain=SCORED_PROGRAMS)


def never_abstain(prompt: str) -> T1Answer:
    r = read_narrative(prompt)
    eligible, snap = snap_estimate(r)
    return _answer(r, eligible=eligible, snap=snap, eitc=0.0, ctc=ctc_estimate(r))


def always_eligible(prompt: str) -> T1Answer:
    r = read_narrative(prompt)
    allotment = _table(MAX_ALLOTMENT, r.n_people, _ffy(r.month))
    return _answer(r, eligible=True, snap=float(allotment), eitc=0.0,
                   ctc=float(2_200 * r.n_children))


def never_eligible(prompt: str) -> T1Answer:
    r = read_narrative(prompt)
    return _answer(r, eligible=False, snap=0.0, eitc=0.0, ctc=0.0)


def rules_only(prompt: str) -> T1Answer:
    """The one baseline with a defensible abstention rule of its own.

    A shelter cost that is never stated is the single omission a rules-only agent can
    detect without any model of determinability - the deduction is in the formula and the
    input is absent. It abstains on SNAP for that and nothing else, which is exactly the
    shallow heuristic v0 exists to be measured against.
    """
    r = read_narrative(prompt)
    eligible, snap = snap_estimate(r)
    abstain = ("snap",) if not r.shelter_stated else ()
    return _answer(r, eligible=eligible, snap=snap, eitc=0.0, ctc=ctc_estimate(r),
                   abstain=abstain)


BASELINES = {
    "always_abstain": always_abstain,
    "never_abstain": never_abstain,
    "always_eligible": always_eligible,
    "never_eligible": never_eligible,
    "rules_only": rules_only,
}
