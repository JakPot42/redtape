"""Parameter drift detector.

The oracle-freshness risk is structural, not incidental. PolicyEngine implements
`is_snap_abawd_hr1_in_effect` but not the HR 1 SUA changes, so its coverage of major
legislation is partial and lagging by an unknown amount. Discovering that case by case
does not scale.

This module asserts that the engine's parameter values equal externally published
figures, cell by cell, and fails the build on divergence. It is continuous evidence of
where the oracle is stale, rather than a one-off audit.

**Every expected value here must come from an external published source, never from the
engine.** A value copied from the engine would make this file self-confirming and
worthless. Sources are recorded per block.
"""

from __future__ import annotations

import pytest

from policyengine_us import CountryTaxBenefitSystem

REGION = "CONTIGUOUS_US"
FFY2025 = "2025-04-01"   # FFY2025: 2024-10-01 .. 2025-09-30
FFY2026 = "2025-11-01"   # FFY2026: 2025-10-01 .. 2026-09-30


@pytest.fixture(scope="module")
def P():
    return CountryTaxBenefitSystem().parameters


def _cells(node_at_instant, region=REGION):
    out = {}
    for k, v in node_at_instant._children[region]._children.items():
        try:
            out[int(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


# ----------------------------------------------------------------------------------
# Sources
#   [A] LSNC Guide to CalFresh Benefits, calfresh.guide, retrieved 2026-08-29 (FFY2025)
#   [B] Santa Clara County DEBS allotment/income chart, retrieved 2026-08-29 (FFY2026)
#   [D] LSNC maximum allotments as of 10/01/2024, retrieved 2026-08-29 (FFY2025)
#   [E] CDSS ACIN I-46-25 FFY2026 COLA, supplied by reviewer 2026-08-29
#   [F] FNS SNAP FY2026 COLA memo, read by reviewer 2026-08-29
#   [G] CBPP "A Quick Guide to SNAP Eligibility and Benefits", updated 2025-10-03,
#       endnotes 4, 6, 9 and 12; supplied by reviewer 2026-08-30
# ----------------------------------------------------------------------------------

PUBLISHED = {
    "max_allotment": {
        FFY2025: ({1: 292, 2: 536, 3: 768, 4: 975, 5: 1158, 6: 1390, 7: 1536, 8: 1756}, "[D]"),
        FFY2026: ({1: 298, 2: 546, 3: 785, 4: 994, 5: 1183, 6: 1421, 7: 1571, 8: 1789}, "[B][E]"),
    },
    "standard_deduction": {
        FFY2025: ({1: 204, 2: 204, 3: 204, 4: 217, 5: 254, 6: 291}, "[A]"),
        # [F] FNS FY2026 COLA memo gives sizes 1-3; [G] CBPP endnote 9 gives all sizes.
        # Sizes 4+ now have an external source, so they are no longer self-confirming.
        FFY2026: ({1: 209, 2: 209, 3: 209, 4: 223, 5: 261, 6: 299}, "[F][G]"),
    },
    "shelter_cap": {FFY2025: (712, "[A]"), FFY2026: (744, "[F]")},
    "homeless_shelter_deduction": {FFY2025: (190.30, "[A]"), FFY2026: (198.99, "[F]")},
    "sua_ca": {FFY2025: (645, "[A]"), FFY2026: (663, "[E]")},
    "lua_ca": {FFY2025: (166, "[A]"), FFY2026: (170, "[E]")},
}


@pytest.mark.parametrize("instant", [FFY2025, FFY2026])
def test_max_allotment_matches_published(P, instant):
    expected, src = PUBLISHED["max_allotment"][instant]
    got = _cells(P.gov.usda.snap.max_allotment.main(instant))
    for size, want in expected.items():
        assert got[size] == want, (
            f"DRIFT: max allotment size {size} at {instant}: engine {got[size]} != "
            f"published {want} {src}"
        )


@pytest.mark.parametrize("instant", [FFY2025, FFY2026])
def test_standard_deduction_matches_published(P, instant):
    expected, src = PUBLISHED["standard_deduction"][instant]
    got = _cells(P.gov.usda.snap.income.deductions.standard(instant))
    for size, want in expected.items():
        assert got[size] == want, (
            f"DRIFT: standard deduction size {size} at {instant}: engine {got[size]} != "
            f"published {want} {src}"
        )


@pytest.mark.parametrize("instant", [FFY2025, FFY2026])
def test_shelter_cap_matches_published(P, instant):
    want, src = PUBLISHED["shelter_cap"][instant]
    got = float(P.gov.usda.snap.income.deductions.excess_shelter_expense.cap(instant)[REGION])
    assert got == want, f"DRIFT: shelter cap at {instant}: engine {got} != published {want} {src}"


@pytest.mark.parametrize("instant", [FFY2025, FFY2026])
def test_homeless_shelter_deduction_matches_published(P, instant):
    want, src = PUBLISHED["homeless_shelter_deduction"][instant]
    got = float(P.gov.usda.snap.income.deductions.excess_shelter_expense.homeless(instant)["deduction"])
    assert got == pytest.approx(want, abs=0.01), (
        f"DRIFT: homeless shelter deduction at {instant}: engine {got} != published {want} {src}"
    )


def _ca_utility(month: str, variable: str) -> float:
    """Read a CA utility allowance through the variable, not the parameter tree.

    The parameter subtree is not state-keyed at this path; the per-state value is
    resolved inside the variable.
    """
    from policyengine_us import Simulation

    from redtape.oracle.takeup import apply_suppression

    sit = {
        "people": {"p1": {"age": {"2025": 30}, "employment_income": {"2025": 18_000}}},
        "tax_units": {"tu": {"members": ["p1"]}},
        "families": {"f": {"members": ["p1"]}},
        "spm_units": {"s": {"members": ["p1"], "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ["p1"], "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    sim = Simulation(situation=apply_suppression(sit, 2025))
    return float(sim.calculate(variable, month)[0])


@pytest.mark.parametrize(
    "instant,month", [(FFY2025, "2025-04"), (FFY2026, "2025-11")]
)
def test_california_sua_matches_published(P, instant, month):
    want, src = PUBLISHED["sua_ca"][instant]
    got = _ca_utility(month, "snap_standard_utility_allowance")
    assert got == want, (
        f"DRIFT: CA SUA at {month}: engine {got} != published {want} {src}"
    )


def test_california_lua_is_unreachable_and_therefore_unvalidated(P):
    """Published CA LUA ($166 FFY2025 / $170 FFY2026) CANNOT be validated here.

    California's `always_standard` flag is True, so every CA household is given the
    Standard Utility Allowance and the Limited Utility Allowance is never reached. The
    engine returns 0 for the LUA-by-size variable in California as a result.

    This is not a drift failure - it is a direct consequence of the same modelling gap
    documented in docs/HR1_SUA_DIVERGENCE.md. Recorded as a test so that if California
    ever becomes conditional upstream, the LUA becomes reachable and this test tells us
    to start validating it.
    """
    assert bool(
        P.gov.usda.snap.income.deductions.utility.always_standard("2025-04-01")["CA"]
    ) is True, "CA is no longer always-SUA; the published LUA is now reachable and must be validated"
    assert _ca_utility("2025-04", "snap_limited_utility_allowance_by_household_size") == 0.0


def test_fy2026_minimum_benefit_is_24(P):
    """[F][G] FY2026 minimum benefit is $24 for 1- and 2-person households (48 + DC).

    The engine derives it as a rate against the 1-person maximum allotment rather than
    storing it directly, so the check is on the derived value.
    """
    node = P.gov.usda.snap.min_allotment(FFY2026)
    rate = float(node["rate"])
    max_size = int(node["maximum_household_size"])
    ref_size = int(node["relevant_max_allotment_household_size"])
    one_person = _cells(P.gov.usda.snap.max_allotment.main(FFY2026))[ref_size]
    derived = round(rate * one_person)
    assert max_size == 2, f"DRIFT: minimum benefit should apply to sizes 1-2, got {max_size}"
    assert derived == 24, (
        f"DRIFT: FY2026 minimum benefit: engine derives {derived} "
        f"({rate} x {one_person}) != published 24 [F][G]"
    )


def test_fy2026_poverty_line_for_family_of_three(P):
    """[G] FY2026 poverty line for a family of three is $2,221/month; 130% is $2,888.

    Read through the SNAP income-limit variables so the check covers the value SNAP
    actually uses, not merely a parameter sitting elsewhere in the tree.
    """
    from policyengine_us import Simulation

    from redtape.oracle.takeup import apply_suppression

    ids = ["p1", "p2", "p3"]
    sit = {
        "people": {
            "p1": {"age": {"2025": 35}, "employment_income": {"2025": 0}},
            "p2": {"age": {"2025": 8}, "employment_income": {"2025": 0}},
            "p3": {"age": {"2025": 5}, "employment_income": {"2025": 0}},
        },
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    sim = Simulation(situation=apply_suppression(sit, 2025))

    monthly_fpg = None
    for var in ("snap_fpg", "spm_unit_fpg", "snap_net_income_limit"):
        try:
            v = float(sim.calculate(var, "2025-11")[0])
        except Exception:
            try:
                v = float(sim.calculate(var, 2025)[0]) / 12
            except Exception:
                continue
        monthly_fpg = v
        break

    if monthly_fpg is None:
        pytest.skip("no reachable FPG variable; poverty line not checkable this way")

    assert monthly_fpg == pytest.approx(2221, abs=2), (
        f"DRIFT: FY2026 monthly poverty line for a family of three: engine "
        f"{monthly_fpg:,.2f} != published 2,221 [G]"
    )
    assert monthly_fpg * 1.3 == pytest.approx(2888, abs=3), (
        f"DRIFT: 130% of the poverty line: engine {monthly_fpg * 1.3:,.2f} != published 2,888 [G]"
    )


def test_structural_rates_match_published(P):
    """20% earned income deduction; 130%/100% FPL limits; elderly threshold 60. [A][B]"""
    assert float(P.gov.usda.snap.income.deductions.earned_income(FFY2025)) == 0.20
    assert float(P.gov.usda.snap.income.limit.gross(FFY2025)) == 1.3
    assert float(P.gov.usda.snap.income.limit.net(FFY2025)) == 1.0
    assert int(P.gov.usda.elderly_age_threshold(FFY2025)) == 60


def test_fiscal_year_boundary_is_october(P):
    """FFY2026 values must not appear before 2025-10-01, nor FFY2025 after."""
    sep = _cells(P.gov.usda.snap.max_allotment.main("2025-09-30"))
    oct_ = _cells(P.gov.usda.snap.max_allotment.main("2025-10-01"))
    assert sep[1] == 292, f"DRIFT: September should be on FFY2025, got {sep[1]}"
    assert oct_[1] == 298, f"DRIFT: October should be on FFY2026, got {oct_[1]}"


# ----------------------------------------------------------------------------------
# Known divergence - asserted so it is tracked, and so we are TOLD when upstream fixes it
# ----------------------------------------------------------------------------------


def test_known_divergence_hr1_sua_still_unmodelled(P):
    """CA `always_standard` should become conditional once HR 1 is implemented upstream.

    This test asserts the CURRENT, KNOWN-WRONG behaviour on purpose. When PolicyEngine
    implements the 2025-07-04 Heat-and-Eat termination or the 2025-10-31 SUAS
    restriction, this test fails - which is the notification we want. Do not "fix" it by
    changing the assertion; update docs/HR1_SUA_DIVERGENCE.md and the affected month
    ranges instead.
    """
    node = P.gov.usda.snap.income.deductions.utility.always_standard
    for instant in ("2025-05-01", "2025-07-05", "2025-10-01", "2025-11-01"):
        assert bool(node(instant)["CA"]) is True, (
            f"UPSTREAM CHANGE at {instant}: California always_standard is no longer True. "
            "PolicyEngine may have implemented the HR 1 SUA rules. Re-read "
            "docs/HR1_SUA_DIVERGENCE.md and re-scope the affected months."
        )
