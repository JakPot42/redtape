"""External validation of EITC and CTC for tax year 2025.

Published anchors, from two independent sources retrieved 2026-08-30 that agree on every
figure used here:

  [H] IRS.gov, "Earned income and Earned Income Tax Credit (EITC) tables"
  [I] Tax Foundation, "2025 Tax Brackets and Federal Income Tax Rates"
  both consistent with IRS Rev. Proc. 2024-40 as each summarises it.
  [J] PL 119-21 CTC provisions as reported by IRS Schedule 8812 guidance and
      Tax Foundation: $2,200 per qualifying child, $1,700 refundable (ACTC),
      ACTC requires >= $2,500 earned income, phaseout begins $200,000 single,
      reduced $50 per $1,000 over.

Cases span the phase-in, plateau and phase-out regions for 0, 1, 2 and 3+ children,
because phase-out behaviour is where engines and models most often diverge.
"""

from __future__ import annotations

import pytest

from policyengine_us import Simulation

from redtape.oracle.takeup import apply_suppression

# [H][I] TY2025
EITC_MAX = {0: 649.0, 1: 4328.0, 2: 7152.0, 3: 8046.0}
EITC_PHASEOUT_END_SINGLE = {0: 19_104, 1: 50_434, 2: 57_310, 3: 61_555}
EITC_INVESTMENT_LIMIT = 11_950

# [J] TY2025
CTC_PER_CHILD = 2_200.0
CTC_REFUNDABLE_CAP = 1_700.0
CTC_PHASEOUT_START_SINGLE = 200_000
CTC_PHASEOUT_RATE_PER_1000 = 50.0


def _sim(n_children: int, earned: float, child_age: int = 8):
    ids = ["p1"] + [f"c{i+1}" for i in range(n_children)]
    people = {
        "p1": {"age": {"2025": 35}, "employment_income": {"2025": earned},
               "immigration_status": {"2025": "CITIZEN"}}
    }
    for i in range(n_children):
        people[f"c{i+1}"] = {"age": {"2025": child_age}, "employment_income": {"2025": 0},
                             "immigration_status": {"2025": "CITIZEN"}}
    sit = {
        "people": people,
        "tax_units": {"tu": {"members": ids}}, "families": {"f": {"members": ids}},
        "spm_units": {"s": {"members": ids, "housing_cost": {"2025": 12_000},
                            "has_heating_cooling_expense": {"2025": True}}},
        "households": {"h": {"members": ids, "state_name": {"2025": "CA"}}},
        "marital_units": {"m": {"members": ["p1"]}},
    }
    return Simulation(situation=apply_suppression(sit, 2025))


def eitc(n, earned):
    return float(_sim(n, earned).calculate("eitc", 2025)[0])


def ctc_received(n, earned, child_age=8):
    return float(_sim(n, earned, child_age).calculate("ctc_value", 2025)[0])


def ctc_gross(n, earned, child_age=8):
    return float(_sim(n, earned, child_age).calculate("ctc", 2025)[0])


# ----------------------------------------------------------------- EITC: plateau
@pytest.mark.parametrize(
    "n,earned", [(0, 9_000), (1, 15_000), (1, 20_000), (2, 20_000), (3, 20_000)]
)
def test_eitc_plateau_equals_published_maximum(n, earned):
    """In the plateau the credit is exactly the published maximum. [H][I]"""
    assert eitc(n, earned) == pytest.approx(EITC_MAX[n], abs=1.0)


# ----------------------------------------------------------------- EITC: phase-in
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_eitc_is_zero_with_no_earned_income(n):
    assert eitc(n, 0) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_eitc_phase_in_is_monotonic_and_below_maximum(n):
    """Phase-in region: strictly increasing, never above the published maximum."""
    vals = [eitc(n, e) for e in (1_000, 3_000, 5_000, 7_000, 9_000)]
    assert all(b > a for a, b in zip(vals, vals[1:])), f"not monotonic: {vals}"
    assert all(v <= EITC_MAX[n] + 1 for v in vals)


# ----------------------------------------------------------------- EITC: phase-out
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_eitc_is_zero_at_published_completed_phaseout(n):
    """At and above the published completed-phaseout amount the credit is zero. [H][I]"""
    end = EITC_PHASEOUT_END_SINGLE[n]
    assert eitc(n, end + 500) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_eitc_phase_out_is_monotonic_decreasing(n):
    """Between the plateau and the phaseout end the credit falls monotonically."""
    end = EITC_PHASEOUT_END_SINGLE[n]
    points = [int(end * f) for f in (0.55, 0.7, 0.85, 0.95)]
    vals = [eitc(n, e) for e in points]
    assert all(b < a for a, b in zip(vals, vals[1:])), f"not decreasing: {vals}"
    assert all(0 <= v < EITC_MAX[n] for v in vals)


def test_eitc_maxima_are_ordered_by_child_count():
    assert EITC_MAX[0] < EITC_MAX[1] < EITC_MAX[2] < EITC_MAX[3]
    for n in (0, 1, 2, 3):
        plateau = {0: 9_000, 1: 15_000, 2: 20_000, 3: 20_000}[n]
        assert eitc(n, plateau) == pytest.approx(EITC_MAX[n], abs=1.0)


# ----------------------------------------------------------------- CTC
@pytest.mark.parametrize("n", [1, 2, 3])
def test_ctc_gross_is_2200_per_qualifying_child(n):
    """[J] PL 119-21 raised the CTC to $2,200 per qualifying child for TY2025."""
    assert ctc_gross(n, 30_000) == pytest.approx(CTC_PER_CHILD * n, abs=1.0)


def test_ctc_received_is_zero_with_no_earned_income():
    """[J] ACTC requires at least $2,500 of earned income.

    The engine's `ctc` reports the GROSS entitlement ($4,400 for two children) even
    here; `ctc_value` reports what is received. The scored answer uses ctc_value.
    """
    assert ctc_gross(2, 0) == pytest.approx(4_400.0, abs=1.0)
    assert ctc_received(2, 0) == pytest.approx(0.0, abs=0.01)


def test_ctc_received_is_zero_below_the_earned_income_floor():
    assert ctc_received(2, 2_000) == pytest.approx(0.0, abs=0.01)


def test_ctc_received_rises_with_earnings_then_reaches_the_full_credit():
    vals = [ctc_received(2, e) for e in (5_000, 12_000, 20_000, 30_000, 45_000)]
    assert all(b >= a for a, b in zip(vals, vals[1:])), f"not monotonic: {vals}"
    assert vals[-1] == pytest.approx(CTC_PER_CHILD * 2, abs=1.0)


@pytest.mark.parametrize(
    "earned,expected",
    [(200_000, 2_200.0), (210_000, 1_700.0), (230_000, 700.0), (244_000, 0.0)],
)
def test_ctc_phases_out_at_50_per_1000_above_200k(earned, expected):
    """[J] Reduced $50 per $1,000 of MAGI above $200,000 (single), zero at $244,000."""
    assert ctc_received(1, earned) == pytest.approx(expected, abs=1.0)


@pytest.mark.parametrize("age,expected", [(8, 2_200.0), (16, 2_200.0), (18, 0.0)])
def test_ctc_requires_a_child_under_17(age, expected):
    assert ctc_received(1, 30_000, child_age=age) == pytest.approx(expected, abs=1.0)


def test_seventeen_year_old_gets_other_dependent_credit_not_ctc():
    """A 17-year-old is not a CTC qualifying child but is an `other dependent`."""
    got = ctc_received(1, 30_000, child_age=17)
    assert got == pytest.approx(500.0, abs=1.0), (
        f"expected the $500 other-dependent credit, got {got}"
    )
