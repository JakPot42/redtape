"""The narrative reader must keep one entry per person, in order, whatever is withheld.

`read_narrative` used to expose only `ages`, a list of the ages that PARSED. A withheld age
left no gap - it shifted every later age one position earlier and silently reattached it to
the wrong person - and for a one-person household it produced an empty list, which built a
tool payload with no people and crashed the engine with "No person found. At least one
person must be defined to run a simulation."

That crash killed a real conditions run midway. It is the same shape as everything else
found this session: the absent thing left no trace, and the code downstream could not tell
"no people" from "people we failed to parse".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.baselines import read_narrative
from redtape.config import DEV_SEED
from redtape.generator.households import generate, withhold
from redtape.generator.narratives import render

SPLIT = Path(__file__).resolve().parent.parent / "data" / "dev" / "t1_smoke.jsonl"

WITHHOLDABLE = ("p1.age", "p1.employment_income", "p1.immigration_status", "housing_cost")


@pytest.mark.parametrize("fact", WITHHOLDABLE)
def test_every_person_survives_a_withheld_fact(fact):
    """One entry per person line, no matter which fact is missing."""
    for index in range(10):
        hh = generate(DEV_SEED, index)
        r = read_narrative(render(withhold(hh, fact)))
        assert len(r.people) == len(hh.people), (
            f"household {index}, withholding {fact}: narrative has {len(hh.people)} "
            f"people but the reader found {len(r.people)}"
        )
        assert r.people, "a payload built from this would have no people at all"


def test_person_ids_stay_aligned_when_an_age_is_withheld():
    """The subtle half: a missing age must leave a gap, not shift its neighbours."""
    for index in range(10):
        hh = generate(DEV_SEED, index)
        if len(hh.people) < 2:
            continue
        r = read_narrative(render(withhold(hh, "p1.age")))
        by_id = {p["person_id"]: p for p in r.people}
        assert by_id["p1"]["age"] is None, "p1's age was withheld and must read as unknown"
        # Everyone else keeps the age the narrative actually states for them.
        for person in hh.people[1:]:
            if person.person_id in by_id:
                assert by_id[person.person_id]["age"] == person.age


def test_a_withheld_age_reads_as_none_not_as_a_number():
    """None means 'not stated'. A default here would be the LIMITS 3 mistake again."""
    hh = generate(DEV_SEED, 0)
    r = read_narrative(render(withhold(hh, "p1.age")))
    assert r.any_age_withheld
    assert r.people[0]["age"] is None


def test_a_stated_zero_income_is_not_confused_with_a_withheld_one():
    """"has no earnings" is a stated 0.0; silence is None. They must not collapse."""
    hh = generate(DEV_SEED, 0)
    withheld = read_narrative(render(withhold(hh, "p1.employment_income")))
    assert withheld.people[0]["employment_income"] is None
    assert withheld.any_income_withheld

    stated = read_narrative(render(hh))
    assert stated.people[0]["employment_income"] is not None
    assert not stated.any_income_withheld


@pytest.mark.skipif(not SPLIT.is_file(), reason="smoke split not built")
def test_every_row_of_the_committed_split_yields_at_least_one_person():
    """The crash, asserted over real data rather than a constructed case."""
    empty = []
    with SPLIT.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if not read_narrative(row["prompt"]).people:
                empty.append(row["household_id"])
    assert not empty, f"rows whose narrative yields no people: {empty}"
