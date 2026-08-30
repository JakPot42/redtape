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

import random

from redtape.schemas import Household, ImmigrationStatus, Person

# Coarse but defensible: enough spread to straddle the SNAP income limits and the
# excess shelter deduction cap.
_ADULT_AGES = (19, 64)
_CHILD_AGES = (0, 17)
_N_CHILDREN_WEIGHTS = ((0, 0.30), (1, 0.30), (2, 0.25), (3, 0.15))
_INCOME_BUCKETS = (
    ((0, 0), 0.15),            # no earned income
    ((1, 15_000), 0.30),       # deep poverty
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
# Restricted to SAFE_IMMIGRATION_STATUSES. REFUGEE and ASYLEE were previously generated
# and have been REMOVED: the engine still models them as SNAP-eligible after HR 1 removed
# that eligibility, so any household carrying them has a known-wrong answer key
# (docs/LIMITS.md 16). Weights are renormalised over the remaining statuses.
_STATUS_WEIGHTS = (
    (ImmigrationStatus.CITIZEN, 0.80),
    (ImmigrationStatus.LEGAL_PERMANENT_RESIDENT, 0.12),
    (ImmigrationStatus.CUBAN_HAITIAN_ENTRANT, 0.02),
    (ImmigrationStatus.UNDOCUMENTED, 0.06),
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

TAX_YEAR = 2025


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


def generate(seed: int, index: int) -> Household:
    """One fully-specified household. No facts withheld; see withhold() for T1b."""
    rng = _rng(seed, index)

    n_children = _weighted(rng, _N_CHILDREN_WEIGHTS)
    n_adults = 1 if rng.random() < 0.65 else 2

    people: list[Person] = []
    for i in range(n_adults):
        lo, hi = _weighted(rng, _INCOME_BUCKETS)
        people.append(
            Person(
                person_id=f"p{len(people) + 1}",
                age=rng.randint(*_ADULT_AGES),
                employment_income=float(rng.randint(lo, hi)) if hi else 0.0,
                immigration_status=_weighted(rng, _STATUS_WEIGHTS),
                is_disabled=rng.random() < 0.12,
            )
        )
    for i in range(n_children):
        people.append(
            Person(
                person_id=f"p{len(people) + 1}",
                age=rng.randint(*_CHILD_AGES),
                employment_income=0.0,
                immigration_status=_weighted(rng, _STATUS_WEIGHTS),
                is_disabled=rng.random() < 0.05,
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

    people = []
    for p in hh.people:
        people.append(p.model_copy(update={field: None}) if p.person_id == pid else p)
    return hh.model_copy(update={"people": tuple(people)})


def generate_many(seed: int, n: int, start: int = 0):
    return [generate(seed, i) for i in range(start, start + n)]
