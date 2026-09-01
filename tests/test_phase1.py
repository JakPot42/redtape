"""Phase 1 test suite.

Covers the five things CLAUDE.md says must be locked down: oracle determinism,
generator reproducibility, schema validation, rules-table lint, and the period
semantics that everything downstream depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from redtape.generator.households import generate, generate_many, withhold
from redtape.oracle.determinability import SWEEPS, probe
from redtape.oracle.policyengine_oracle import MissingFactError, build_situation, compute
from redtape.scoring.rules_lint import lint
from redtape.schemas import Determinability, Person, SnapAnswer

RULES = Path(__file__).resolve().parents[1] / "rules" / "verification_requirements.yaml"
SEED = 20260828


# ----------------------------------------------------------------------------------
# Generator reproducibility
# ----------------------------------------------------------------------------------


def test_generator_is_reproducible_from_seed_and_index():
    assert generate(SEED, 3) == generate(SEED, 3)


def test_generator_index_is_independent_of_batch():
    """Household 7 alone must equal household 7 from a batch of 20."""
    assert generate(SEED, 7) == generate_many(SEED, 20)[7]


def test_generator_differs_across_seeds():
    a = generate_many(SEED, 10)
    b = generate_many(SEED + 1, 10)
    assert a != b


def test_generated_households_are_complete():
    for hh in generate_many(SEED, 25):
        assert hh.is_complete
        assert hh.withheld() == []


def test_generated_month_is_in_tax_year():
    for hh in generate_many(SEED, 25):
        year, month = hh.month.split("-")
        assert int(year) == hh.tax_year
        assert 1 <= int(month) <= 12


# ----------------------------------------------------------------------------------
# Schema validation
# ----------------------------------------------------------------------------------


def test_schema_rejects_unknown_field():
    with pytest.raises(ValidationError):
        Person(person_id="p1", age=30, employment_income=0.0, nonsense=1)


def test_schema_requires_explicit_period_label():
    with pytest.raises(ValidationError):
        SnapAnswer(eligible=True, benefit=1.0)  # no period_label


def test_answer_period_tags_are_fixed():
    a = SnapAnswer(period_label="2025-03", eligible=True, benefit=1.0)
    assert a.period == "month"


def test_household_is_frozen():
    hh = generate(SEED, 0)
    with pytest.raises(ValidationError):
        hh.household_id = "mutated"


# ----------------------------------------------------------------------------------
# Oracle: refuses to answer on a withheld fact
# ----------------------------------------------------------------------------------


def test_oracle_raises_rather_than_defaulting():
    """PolicyEngine would silently substitute housing_cost=0. We must not let it."""
    hh = withhold(generate(SEED, 0), "housing_cost")
    with pytest.raises(MissingFactError):
        compute(hh)


@pytest.mark.parametrize("fact", ["p1.employment_income", "p1.age", "p1.immigration_status"])
def test_oracle_raises_for_each_withheld_person_fact(fact):
    hh = withhold(generate(SEED, 0), fact)
    with pytest.raises(MissingFactError):
        build_situation(hh)


def test_oracle_is_deterministic():
    hh = generate(SEED, 1)
    assert compute(hh).answer == compute(hh).answer


def test_oracle_attaches_provenance_to_every_value():
    r = compute(generate(SEED, 2))
    fields = {p.field for p in r.provenance}
    assert fields == {
        "snap.eligible",
        "snap.benefit",
        "medicaid.person_eligible",
        "eitc.amount",
        "ctc.amount",
        # The gross entitlement is recorded alongside the received amount, because
        # ctc and ctc_value differ and both are worth keeping. See LIMITS 21.
        "ctc.gross_entitlement",
    }
    for p in r.provenance:
        assert p.variable and p.entity and p.period_queried


def test_snap_provenance_is_monthly_never_annual():
    """The period trap: is_snap_eligible queried annually returns December alone."""
    hh = generate(SEED, 2)
    r = compute(hh)
    snap = [p for p in r.provenance if p.field.startswith("snap.")]
    for p in snap:
        assert p.period_queried == hh.month
        assert "-" in p.period_queried, "SNAP must be queried at a month, not a year"


# ----------------------------------------------------------------------------------
# Period semantics regression - locks docs/LIMITS.md 1
# ----------------------------------------------------------------------------------


def test_monthly_stock_variable_annual_query_returns_december():
    """If a version bump changes this, fail here rather than silently in answer keys."""
    from policyengine_us import Simulation

    months = [f"2025-{m:02d}" for m in range(1, 13)]
    situation = {
        "people": {"a": {"age": {"2025": 35}}},
        "tax_units": {"tu": {"members": ["a"]}},
        "families": {"f": {"members": ["a"]}},
        "spm_units": {
            "s": {"members": ["a"], "is_snap_eligible": {m: (m == "2025-03") for m in months}}
        },
        "households": {"h": {"members": ["a"], "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["a"]}},
    }
    sim = Simulation(situation=situation)
    assert bool(sim.calculate("is_snap_eligible", "2025-03")[0]) is True
    # Eligible in March only, but the annual query reports December.
    assert bool(sim.calculate("is_snap_eligible", 2025)[0]) is False


def test_quantity_types_have_not_changed():
    from policyengine_us import CountryTaxBenefitSystem

    vs = CountryTaxBenefitSystem().variables
    expected = {
        "snap": ("month", "flow"),
        "is_snap_eligible": ("month", "stock"),
        "is_medicaid_eligible": ("year", "stock"),
        "eitc": ("year", "flow"),
        "ctc": ("year", "flow"),
    }
    for name, (period, qty) in expected.items():
        v = vs[name]
        # quantity_type is a plain string here, not an enum; normalise either form.
        raw = getattr(v.quantity_type, "name", v.quantity_type)
        got = (v.definition_period, str(raw).lower())
        assert got == (period, qty), f"{name} changed: {got} != {(period, qty)}"


# ----------------------------------------------------------------------------------
# Determinability prober
# ----------------------------------------------------------------------------------


def test_prober_refuses_a_fact_that_is_not_withheld():
    with pytest.raises(ValueError):
        probe(generate(SEED, 0), "housing_cost")


def test_prober_labels_income_as_deciding():
    """Income is load-bearing for SNAP in a low-income household; this must flip."""
    hh = generate(SEED, 0)
    lab = probe(withhold(hh, "p1.employment_income"), "p1.employment_income")
    assert lab.label is Determinability.INDETERMINATE
    assert "snap" in lab.deciding_programs


def test_prober_records_its_sweep_range():
    """A label without its declared range is not auditable."""
    hh = generate(SEED, 0)
    lab = probe(withhold(hh, "housing_cost"), "housing_cost")
    assert len(lab.sweep_values) == len(SWEEPS["housing_cost"])
    assert lab.sweep_values[0] == "0.0"


def test_prober_is_deterministic():
    hh = withhold(generate(SEED, 4), "housing_cost")
    assert probe(hh, "housing_cost") == probe(hh, "housing_cost")


# ----------------------------------------------------------------------------------
# Rules table lint
# ----------------------------------------------------------------------------------


def test_rules_table_lints_clean():
    rep = lint(RULES)
    assert rep.ok, rep.render()


def test_rules_table_has_no_unapproved_high_confidence():
    """Claude never promotes a rule to `high`; only the reviewer does."""
    rep = lint(RULES)
    assert rep.by_confidence.get("high", 0) == 0


def test_rules_table_reports_confidence_composition():
    rep = lint(RULES)
    assert rep.n_rules == sum(rep.by_confidence.values())
    assert rep.scored_rules == rep.n_rules - rep.by_confidence.get("low", 0)


def test_linter_rejects_missing_citation(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n"
        "  - id: SNAP-INC-01\n"
        "    program: snap\n"
        "    subject: household\n"
        "    applies_when: {always: true}\n"
        "    acceptable_documents: [{type: lease, issuer: landlord, count: 1}]\n"
        "    summary: something\n"
        "    confidence: medium\n",
        encoding="utf-8",
    )
    rep = lint(bad)
    assert not rep.ok
    assert any("missing citation" in e for e in rep.errors)


def test_linter_rejects_unapproved_high(tmp_path):
    bad = tmp_path / "high.yaml"
    bad.write_text(
        "rules:\n"
        "  - id: SNAP-INC-01\n"
        "    program: snap\n"
        "    subject: household\n"
        "    applies_when: {always: true}\n"
        "    acceptable_documents: [{type: lease, issuer: landlord, count: 1}]\n"
        "    citation: 7 CFR 273.2\n"
        "    summary: something\n"
        "    confidence: high\n",
        encoding="utf-8",
    )
    assert not lint(bad).ok
    assert lint(bad, approved_high={"SNAP-INC-01"}).ok
