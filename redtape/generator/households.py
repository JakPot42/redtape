"""Seeded procedural household generator.

Every household is reproducible from (seed, index) alone: the RNG is derived from the
pair, never carried across calls, so generating household 7 in isolation gives exactly
the same result as generating 0..99 and taking index 7. tests/test_generator.py locks
this.

Distributions are deliberately coarse in v0 and are documented rather than tuned to any
population. They exist to produce households that exercise the rules, not to be a
representative sample of California. That is stated in docs/LIMITS.md.
"""

from __future__ import annotations

import math
import random

from redtape.schemas import CORPUS_TAX_YEAR, Household, ImmigrationStatus, Person

# Coarse but defensible: enough spread to straddle the SNAP income limits and the
# excess shelter deduction cap.
_ADULT_AGES = (19, 64)
_CHILD_AGES = (0, 17)
_N_CHILDREN_WEIGHTS = ((0, 0.30), (1, 0.30), (2, 0.25), (3, 0.15))
_INCOME_BUCKETS = (
    ((0, 0), 0.15),            # no earned income
    # Floor raised from $1 to $1,000 on 2026-09-19: every earner now has stated weekly
    # hours at or above California's minimum wage, and below ~$858/yr no whole number of
    # hours (>= 1) can be paid at $16.50. See weekly_hours().
    ((1_000, 15_000), 0.30),   # deep poverty
    ((15_000, 35_000), 0.30),  # near the SNAP limits, where the interesting cases are
    ((35_000, 70_000), 0.20),
    ((70_000, 140_000), 0.05), # clearly over
)
_HOUSING_BUCKETS = (
    ((0, 0), 0.10),
    ((3_600, 18_000), 0.45),
    ((18_000, 36_000), 0.35),
    ((36_000, 60_000), 0.10),
)
# Restricted to SAFE_IMMIGRATION_STATUSES, which for the CA/2025 corpus scope is every
# engine status (docs/LIMITS.md 16). REFUGEE and ASYLEE were removed in error - we read a
# correctly modelled California delay (CDSS ACL 25-92, effective 2026-04-01) as a federal
# omission - and are restored here at their original weights. CUBAN_HAITIAN_ENTRANT, added
# while the corpus was restricted, is retained: it is the category HR 1 keeps eligible, so
# it is worth exercising. CITIZEN and LPR absorb the difference.
_STATUS_WEIGHTS = (
    (ImmigrationStatus.CITIZEN, 0.78),
    (ImmigrationStatus.LEGAL_PERMANENT_RESIDENT, 0.10),
    (ImmigrationStatus.CUBAN_HAITIAN_ENTRANT, 0.02),
    (ImmigrationStatus.REFUGEE, 0.03),
    (ImmigrationStatus.ASYLEE, 0.02),
    (ImmigrationStatus.UNDOCUMENTED, 0.05),
)

# Dependent care costs, annual. Zero for most households; a real cost where a working
# adult has a child. Exercises the SNAP dependent care deduction, which is otherwise
# an untested channel.
_CARE_BUCKETS = (
    ((0, 0), 0.60),
    ((240, 1_200), 0.20),
    ((1_200, 4_800), 0.15),
    ((4_800, 12_000), 0.05),
)

TAX_YEAR = CORPUS_TAX_YEAR

# California statewide minimum wage from 2025-01-01, read from the Department of Industrial
# Relations minimum-wage FAQ on 2026-09-19 (the same for all employer sizes). Every earner's
# stated hours imply an hourly wage at or above this.
CA_MIN_WAGE_2025 = 16.50
_MAX_WAGE = 40.00
_MAX_HOURS = 60

_P_MARRIED = 0.35            # was the old 2-adult share; the shape is now explicit
_SPOUSE_AGE_GAP = 8          # a married partner's age is p1's +/- this, within adult range
_MIN_PARENT_GAP = 16         # a child is at least this many years younger than each parent
_P_STUDENT = 0.10
_P_FULL_TIME = 0.5           # of students


def _weighted(rng: random.Random, weighted):
    r = rng.random()
    cum = 0.0
    for value, w in weighted:
        cum += w
        if r <= cum:
            return value
    return weighted[-1][0]


def _rng(seed: int, index: int) -> random.Random:
    """Derive an independent stream per (seed, index). Never reuse across households."""
    return random.Random(f"redtape/v0/{seed}/{index}")


def weekly_hours(rng: random.Random, annual_income: float) -> float:
    """Whole weekly hours consistent with `annual_income` at an hourly wage between the
    California minimum and _MAX_WAGE, capped at _MAX_HOURS.

    `floor` never rounds hours UP, so the implied wage never falls below the minimum.
    Zero income means zero hours. Public so tests can check the wage bound directly.
    """
    if annual_income <= 0:
        return 0.0
    at_min_wage = annual_income / (52 * CA_MIN_WAGE_2025)
    at_max_wage = annual_income / (52 * _MAX_WAGE)
    hi = min(at_min_wage, _MAX_HOURS)
    lo = min(at_max_wage, hi)
    return float(max(1, math.floor(rng.uniform(lo, hi))))


def _adult(rng: random.Random, pid: str, age: int) -> Person:
    lo, hi = _weighted(rng, _INCOME_BUCKETS)
    income = float(rng.randint(lo, hi)) if hi else 0.0
    student = rng.random() < _P_STUDENT
    return Person(
        person_id=pid,
        age=age,
        employment_income=income,
        weekly_hours=weekly_hours(rng, income),
        immigration_status=_weighted(rng, _STATUS_WEIGHTS),
        is_disabled=rng.random() < 0.12,
        # Higher-education enrolment can flip SNAP eligibility (7 CFR 273.5), but only
        # where no 273.5(b) exemption applies - and 20+ paid hours is one. Hours are now
        # stated, so a student who works is not a flip.
        is_higher_ed_student=student,
        student_full_time=(rng.random() < _P_FULL_TIME) if student else None,
    )


def generate(seed: int, index: int) -> Household:
    """One fully-specified household. No facts withheld; see withhold() for T1b.

    Two shapes only (schemas.HOUSEHOLD_TYPES). The relationships are part of the record,
    not inferred later by the oracle or the engine.
    """
    rng = _rng(seed, index)

    household_type = "married_couple" if rng.random() < _P_MARRIED else "single_adult"
    n_children = _weighted(rng, _N_CHILDREN_WEIGHTS)

    p1_age = rng.randint(*_ADULT_AGES)
    people: list[Person] = [_adult(rng, "p1", p1_age)]
    if household_type == "married_couple":
        p2_age = min(max(p1_age + rng.randint(-_SPOUSE_AGE_GAP, _SPOUSE_AGE_GAP),
                         _ADULT_AGES[0]), _ADULT_AGES[1])
        people.append(_adult(rng, "p2", p2_age))

    # A child must be plausibly the child of EVERY adult: at least _MIN_PARENT_GAP years
    # younger than the youngest parent. The old generator drew child ages independently,
    # so a 19-year-old could "have" a 17-year-old.
    oldest_child = min(_CHILD_AGES[1], min(p.age for p in people) - _MIN_PARENT_GAP)
    if oldest_child < _CHILD_AGES[0]:
        n_children = 0
    for _ in range(n_children):
        people.append(
            Person(
                person_id=f"p{len(people) + 1}",
                age=rng.randint(_CHILD_AGES[0], oldest_child),
                employment_income=0.0,
                weekly_hours=0.0,
                immigration_status=_weighted(rng, _STATUS_WEIGHTS),
                is_disabled=rng.random() < 0.05,
                is_higher_ed_student=False,
            )
        )

    lo, hi = _weighted(rng, _HOUSING_BUCKETS)
    housing = float(rng.randint(lo, hi)) if hi else 0.0

    lo, hi = _weighted(rng, _CARE_BUCKETS)
    care = float(rng.randint(lo, hi)) if (hi and n_children) else 0.0

    return Household(
        household_id=f"hh-{seed}-{index:05d}",
        seed=seed,
        index=index,
        month=f"{TAX_YEAR}-{rng.randint(1, 12):02d}",
        people=tuple(people),
        household_type=household_type,
        pays_heating_cooling=True,
        housing_cost=housing,
        dependent_care_cost=care,
    )


def withhold(hh: Household, fact: str) -> Household:
    """Return a copy with one fact withheld, for T1b.

    `fact` is either "housing_cost" or "<person_id>.<field>". Withheld means None,
    which the oracle refuses to answer on - it never becomes a silent default.
    """
    if fact in ("housing_cost", "dependent_care_cost"):
        return hh.model_copy(update={fact: None})

    pid, _, field = fact.partition(".")
    if not field:
        raise ValueError(f"unrecognised fact {fact!r}")

    # Coupled facts are withheld together, or the "withheld" fact is recoverable from the
    # narrative: stated weekly hours reveal withheld earnings; a stated full-time flag
    # reveals withheld student status. SSN needs no entry - it is DERIVED from immigration
    # status (Person.ssn_status), so withholding the status withholds it.
    update = {field: None}
    if field == "employment_income":
        update["weekly_hours"] = None
    if field == "is_higher_ed_student":
        update["student_full_time"] = None

    people = []
    for p in hh.people:
        people.append(p.model_copy(update=update) if p.person_id == pid else p)
    return hh.model_copy(update={"people": tuple(people)})


def generate_many(seed: int, n: int, start: int = 0):
    return [generate(seed, i) for i in range(start, start + n)]
