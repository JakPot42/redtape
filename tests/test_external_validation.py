"""External validation locked as regression tests.

These compare the engine against PUBLISHED CalFresh figures, not against itself. If a
policyengine-us version bump changes any of these, the suite fails here rather than
silently changing answer keys.

Sources (retrieved 2026-08-29):
  [A] LSNC Guide to CalFresh Benefits, "Maximum CalFresh deductions" (FFY2025)
  [B] Santa Clara County DEBS allotment/income chart (FFY2026)
  [C] Santa Clara County DEBS Update 24-07, CalFresh COLA FFY2025
"""

from __future__ import annotations

import math

import pytest

from policyengine_us import CountryTaxBenefitSystem, Simulation

REGION = "CONTIGUOUS_US"
FFY2025_INSTANT = "2025-04-01"
FFY2026_INSTANT = "2025-11-01"


def _cells(node_at_instant, region=REGION):
    out = {}
    for k, v in node_at_instant._children[region]._children.items():
        try:
            out[int(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


@pytest.fixture(scope="module")
def params():
    return CountryTaxBenefitSystem().parameters


# ----------------------------------------------------------------------------------
# Parameter cells vs published tables
# ----------------------------------------------------------------------------------

# [B] SCC DEBS chart, effective 10/01/2025-09/30/2026.
PUBLISHED_FFY2026_ALLOTMENT = {1: 298, 2: 546, 3: 785, 4: 994,
                               5: 1183, 6: 1421, 7: 1571, 8: 1789}

# [A] LSNC, effective 10/01/2024-09/30/2025.
PUBLISHED_FFY2025_STD_DEDUCTION = {1: 204, 2: 204, 3: 204, 4: 217, 5: 254, 6: 291}


def test_ffy2026_max_allotment_matches_published_table(params):
    got = _cells(params.gov.usda.snap.max_allotment.main(FFY2026_INSTANT))
    for size, expected in PUBLISHED_FFY2026_ALLOTMENT.items():
        assert got[size] == expected, f"size {size}: engine {got[size]} != published {expected}"


def test_ffy2025_standard_deduction_matches_published_table(params):
    got = _cells(params.gov.usda.snap.income.deductions.standard(FFY2025_INSTANT))
    for size, expected in PUBLISHED_FFY2025_STD_DEDUCTION.items():
        assert got[size] == expected, f"size {size}: engine {got[size]} != published {expected}"


def test_earned_income_deduction_is_20_percent(params):
    # [A] "20% of earnings"
    assert params.gov.usda.snap.income.deductions.earned_income(FFY2025_INSTANT) == 0.20


def test_income_limits_are_130_and_100_percent_fpl(params):
    # [B] gross limit is 130% FPL, net limit is 100% FPL
    assert params.gov.usda.snap.income.limit.gross(FFY2025_INSTANT) == 1.3
    assert params.gov.usda.snap.income.limit.net(FFY2025_INSTANT) == 1


# ----------------------------------------------------------------------------------
# End-to-end: benefit vs published tables
# ----------------------------------------------------------------------------------


def _snap(size: int, earned_annual: float, shelter_annual: float, month: str) -> float:
    ids = [f"p{i+1}" for i in range(size)]
    people = {
        i: {
            "age": {"2025": 30 if k == 0 else 8},
            "employment_income": {"2025": earned_annual if k == 0 else 0},
            "immigration_status": {"2025": "CITIZEN"},
        }
        for k, i in enumerate(ids)
    }
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}},
        "families": {"f": {"members": ids}},
        "spm_units": {
            "s": {
                "members": ids,
                "housing_cost": {"2025": shelter_annual},
                # Published SNAP examples take gross income as given; the engine would
                # otherwise add modelled CalWORKs cash aid as unearned income.
                "tanf": {"2025": 0},
                "ca_tanf": {"2025": 0},
            }
        },
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": [ids[0]]}},
    }
    return float(Simulation(situation=sit).calculate("snap", month)[0])


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6])
def test_zero_income_benefit_equals_published_max_allotment(size):
    """Zero net income means the benefit is exactly the published maximum allotment."""
    got = _snap(size, 0, 0, "2025-11")
    assert got == pytest.approx(PUBLISHED_FFY2026_ALLOTMENT[size], abs=1.0)


# Hand-computed from [A]+[C] using the published formula.
FFY2025_FORMULA_CASES = [
    (2, 1200, 900, 522.0),
    (2, 0, 900, 536.0),
    (2, 2000, 1200, 330.0),
    (2, 800, 0, 533.0),
]


@pytest.mark.parametrize("size,earned_m,shelter_m,expected", FFY2025_FORMULA_CASES)
def test_ffy2025_benefit_matches_hand_calculation(size, earned_m, shelter_m, expected):
    got = _snap(size, earned_m * 12, shelter_m * 12, "2025-04")
    assert got == pytest.approx(expected, abs=1.0)


def test_hand_calculation_is_reproducible_from_published_constants():
    """The published formula, applied to published constants, gives our expected values.

    Guards against the expected values above drifting into being copied from the engine.
    """
    std, sua, cap, rate, maxal = 204, 645, 712, 0.20, 536
    for _size, earned_m, shelter_m, expected in FFY2025_FORMULA_CASES:
        after = max(0.0, earned_m - std - rate * earned_m)
        excess = min(cap, max(0.0, shelter_m + sua - 0.5 * after))
        net = max(0.0, after - excess)
        assert max(0.0, maxal - math.ceil(0.30 * net)) == expected


def test_fiscal_year_boundary_falls_at_october():
    """FFY2026 standards start 2025-10-01; a tax-year-2025 household straddles two FFYs."""
    assert _snap(1, 0, 0, "2025-09") == pytest.approx(292.0, abs=1.0)  # FFY2025
    assert _snap(1, 0, 0, "2025-10") == pytest.approx(298.0, abs=1.0)  # FFY2026
