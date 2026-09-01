"""The stratified sampler returns the size it was asked for and never splits a pair.

`--sample 12` used to return 8. A short sample is silently wrong: every headline is then
computed over an `n` nobody chose, and the shortfall appears nowhere in the results file.
Splitting a pair is worse than dropping one, because `pair_consistency` scores a pair with
a missing member as INCONSISTENT - so a truncated pair does not lose a measurement, it
manufactures a false negative.
"""

from __future__ import annotations

import pytest

from eval.run_eval import _stratified_sample

CLASSES = ("determinate", "indeterminate", "incomplete_determinate")


def _rows(n_singles: int = 200, n_pairs: int = 20) -> list[dict]:
    rows = []
    for i in range(n_singles):
        rows.append({
            "household_id": f"hh-1-{i:05d}",
            "determinability": CLASSES[i % len(CLASSES)],
            "pair_id": "",
        })
    for j in range(n_pairs):
        for role in ("with_disability", "without_disability"):
            rows.append({
                "household_id": f"hh-1-9{j:04d}-{role}",
                "determinability": "determinate",
                "pair_id": f"pair-1-{j:05d}",
            })
    return rows


@pytest.mark.parametrize("sample", [6, 12, 25, 40, 97, 150])
def test_sample_returns_exactly_the_requested_size(sample):
    """The headline bug: --sample 12 returned 8."""
    got = _stratified_sample(_rows(), sample, seed=0)
    assert len(got) == sample, f"asked for {sample}, got {len(got)}"


@pytest.mark.parametrize("sample", [6, 12, 25, 40, 97, 150])
def test_pairs_are_never_split(sample):
    """A half-pair is a manufactured false negative, not a lost measurement."""
    got = _stratified_sample(_rows(), sample, seed=0)
    counts: dict[str, int] = {}
    for r in got:
        if r["pair_id"]:
            counts[r["pair_id"]] = counts.get(r["pair_id"], 0) + 1
    broken = {k: v for k, v in counts.items() if v != 2}
    assert not broken, f"pairs present with the wrong member count: {broken}"


def test_sampling_is_deterministic():
    a = _stratified_sample(_rows(), 40, seed=0)
    b = _stratified_sample(_rows(), 40, seed=0)
    assert [r["household_id"] for r in a] == [r["household_id"] for r in b]


def test_a_different_seed_gives_a_different_sample():
    a = _stratified_sample(_rows(), 40, seed=0)
    b = _stratified_sample(_rows(), 40, seed=1)
    assert [r["household_id"] for r in a] != [r["household_id"] for r in b]


def test_every_class_is_represented_when_the_budget_allows():
    """Stratified means stratified: a class must not vanish because of rounding."""
    got = _stratified_sample(_rows(), 60, seed=0)
    present = {r["determinability"] for r in got if not r["pair_id"]}
    assert set(CLASSES) <= present, f"missing classes: {set(CLASSES) - present}"


def test_asking_for_everything_returns_everything():
    rows = _rows()
    assert len(_stratified_sample(rows, len(rows) + 10, seed=0)) == len(rows)


def test_a_split_with_no_pairs_still_samples_exactly():
    rows = _rows(n_singles=50, n_pairs=0)
    assert len(_stratified_sample(rows, 17, seed=0)) == 17
