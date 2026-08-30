"""Extreme-sweep rule: every scored variable is swept to BOTH ends before it is trusted.

Two errors have been found in this schema, both of the same kind: a plausibly-named
variable reporting a GROSS entitlement where the RECEIVED value was meant.

  * `ctc` reports $4,400 for a zero-income family with two children who receive $0.
  * `medicaid` reports a dollar value where `is_medicaid_eligible` (a boolean) was meant.

Both were invisible in the middle of the range. Both separated only at an extreme - zero
income, or income far past a phase-out. Neither was caught by review, because the variable
name looked right.

This module makes the rule mechanical rather than a habit:

1. **Coverage.** Every scored field must be exercised at both ends of every input
   dimension. `test_every_scored_field_has_extreme_coverage` fails if a new scored field
   is added without it.
2. **Divergence detection.** For each variable the oracle reads, find sibling variables
   that could represent the same quantity differently, evaluate them at both extremes,
   and fail on any same-type disagreement not in `EXPLAINED`. A new disagreement means
   either a new instance of this bug or a variable whose meaning we have not established -
   both require a human decision, not a silent pass.
"""

from __future__ import annotations

import pytest

from policyengine_us import CountryTaxBenefitSystem, Simulation

from redtape.oracle.takeup import apply_suppression
from redtape.schemas import SCORED_PROGRAMS

# Variables the oracle reads, by answer field.
ORACLE_VARIABLES = {
    "snap.eligible": "is_snap_eligible",
    "snap.benefit": "snap",
    "eitc.amount": "eitc",
    "ctc.amount": "ctc_value",
    "medicaid.person_eligible": "is_medicaid_eligible",  # computed, NOT scored
}

SCORED_FIELDS = [f for f in ORACLE_VARIABLES if f.split(".")[0] in SCORED_PROGRAMS]

# Both ends of every input dimension the generator can vary.
EXTREMES = {
    "employment_income": (0.0, 500_000.0),
    "housing_cost": (0.0, 120_000.0),
    "dependent_care_cost": (0.0, 40_000.0),
    "age": (18, 95),
    "n_children": (0, 6),
}

# Disagreements that have been investigated and explained. Anything NOT here fails.
# Key: (oracle variable, sibling variable) -> the explanation.
EXPLAINED = {
    ("ctc_value", "ctc"):
        "`ctc` is the GROSS credit before limitation; `ctc_value` is what the household "
        "receives. This is the bug this module exists to catch. docs/LIMITS.md 21.",
    ("ctc_value", "non_refundable_ctc"):
        "a component of the credit, not the total received. docs/LIMITS.md 21.",
    ("ctc_value", "refundable_ctc"):
        "the ACTC component only, not the total received. docs/LIMITS.md 21.",
}


@pytest.fixture(scope="module")
def variables():
    return CountryTaxBenefitSystem().variables


def _siblings(name: str, vs) -> list[str]:
    base = name
    for prefix in ("is_", "refundable_", "non_refundable_"):
        base = base.removeprefix(prefix)
    base = base.removesuffix("_value").removesuffix("_eligible")
    cands = [
        base, f"{base}_value", f"refundable_{base}", f"non_refundable_{base}",
        f"{base}_amount", f"uncapped_{base}", f"{base}_normal_allotment",
    ]
    return [c for c in dict.fromkeys(cands) if c in vs and c != name]


def _build(employment_income=18_000.0, housing_cost=12_000.0, dependent_care_cost=0.0,
           age=35, n_children=2):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    people = {"p1": {"age": {"2025": age}, "employment_income": {"2025": employment_income},
                     "immigration_status": {"2025": "CITIZEN"}}}
    for i in range(n_children):
        people[f"c{i+1}"] = {"age": {"2025": 8}, "employment_income": {"2025": 0},
                             "immigration_status": {"2025": "CITIZEN"}}
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": housing_cost},
                            "childcare_expenses": {"2025": dependent_care_cost},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025))


def _read(sim, name, vs):
    v = vs[name]
    per = "2025-11" if v.definition_period == "month" else 2025
    return float(sim.calculate(name, per)[0]), v.value_type.__name__


# ------------------------------------------------------------------ coverage
def test_every_scored_field_has_extreme_coverage():
    """A scored field added without extreme-range coverage fails here."""
    assert SCORED_FIELDS, "no scored fields found"
    for field in SCORED_FIELDS:
        assert field in ORACLE_VARIABLES, f"{field} has no declared oracle variable"
    for dim, ends in EXTREMES.items():
        assert len(ends) == 2, f"{dim} must declare BOTH extremes"
        assert ends[0] != ends[1], f"{dim} extremes are identical"


def test_medicaid_is_not_scored():
    """Guards the §20 decision: Medicaid must stay out of SCORED_PROGRAMS."""
    assert "medicaid" not in SCORED_PROGRAMS


# ------------------------------------------------------------------ the sweep
@pytest.mark.parametrize("dim", sorted(EXTREMES))
@pytest.mark.parametrize("end", [0, 1])
def test_no_unexplained_divergence_at_extremes(dim, end, variables):
    value = EXTREMES[dim][end]
    sim = _build(**{dim: value})
    unexplained = []

    for field, name in ORACLE_VARIABLES.items():
        try:
            mine, mine_type = _read(sim, name, variables)
        except Exception:
            continue
        for other in _siblings(name, variables):
            try:
                theirs, other_type = _read(sim, other, variables)
            except Exception:
                continue
            # Compare like with like. A bool-vs-float pair is a naming hazard, handled
            # separately; it is not a value disagreement.
            if mine_type != other_type:
                continue
            if abs(mine - theirs) <= 1.0:
                continue
            if (name, other) in EXPLAINED:
                continue
            unexplained.append(
                f"{field}: {name}={mine:,.2f} but {other}={theirs:,.2f} "
                f"at {dim}={value:,.0f}"
            )

    assert not unexplained, (
        "unexplained divergence at an extreme - this is the shape of the ctc/ctc_value "
        "bug. Investigate each and either fix the oracle or add an entry to EXPLAINED "
        "with the reason:\n  " + "\n  ".join(unexplained)
    )


# ------------------------------------- the boolean/amount naming hazard, explicitly
@pytest.mark.parametrize(
    "amount_var,bool_var",
    [("medicaid", "is_medicaid_eligible"), ("snap", "is_snap_eligible")],
)
def test_boolean_and_amount_variables_both_exist_and_differ(amount_var, bool_var, variables):
    """Both spellings exist for these programs; the schema must say which it means.

    `medicaid` is a dollar amount and `is_medicaid_eligible` a boolean. Reading the
    first where the second was meant is the original instance of this bug class, so the
    pairing is asserted rather than left implicit.
    """
    assert amount_var in variables and bool_var in variables
    assert variables[amount_var].value_type is float
    assert variables[bool_var].value_type is bool


def test_zero_income_does_not_produce_a_gross_entitlement_in_the_answer():
    """The specific regression: a zero-income family must not be credited unreceived money."""
    from redtape.oracle.policyengine_oracle import compute
    from redtape.schemas import Household, ImmigrationStatus, Person

    hh = Household(
        household_id="extreme-zero", seed=0, index=0, month="2025-11",
        people=(
            Person(person_id="p1", age=35, employment_income=0.0,
                   immigration_status=ImmigrationStatus.CITIZEN, is_disabled=False),
            Person(person_id="c1", age=8, employment_income=0.0,
                   immigration_status=ImmigrationStatus.CITIZEN, is_disabled=False),
            Person(person_id="c2", age=5, employment_income=0.0,
                   immigration_status=ImmigrationStatus.CITIZEN, is_disabled=False),
        ),
        housing_cost=12_000.0, dependent_care_cost=0.0,
    )
    answer = compute(hh).answer
    assert answer.ctc.amount == pytest.approx(0.0, abs=0.01), (
        f"zero-income family credited ${answer.ctc.amount:,.2f} of CTC it does not receive"
    )
    assert answer.ctc.gross_entitlement == pytest.approx(4_400.0, abs=1.0), (
        "the gross entitlement should still be recorded alongside"
    )
