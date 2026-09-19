"""The closed fact vocabulary: the prompt, the schema and the split must agree.

Exact identifier matching in `score_abstention` is only fair if the model is told the exact
identifiers. Scoring names nobody was given is the LIMITS §25 schema bug moved to fact
names. These tests fail if the vocabulary, the prompt and the facts the split can withhold
drift apart, and if the prompt's worked example ever names a real fact again. It used to
name `p1.employment_income`, the fact Opus 5 flagged most often (LIMITS §35).
"""
import importlib.util
import re
from pathlib import Path

from redtape.schemas import (
    HOUSEHOLD_FACTS,
    OTHER_FACT_PREFIX,
    PERSON_FACTS,
    Household,
    Person,
)

ROOT = Path(__file__).resolve().parent.parent


def _build_split_facts():
    spec = importlib.util.spec_from_file_location("build_split", ROOT / "scripts/build_split.py")
    src = (ROOT / "scripts/build_split.py").read_text()
    # Read the constants without importing the script (it pulls in the engine).
    t1b = re.search(r"T1B_FACTS = \((.*?)\)", src, re.S).group(1)
    flip = re.search(r'FLIP_FACT = "([^"]+)"', src).group(1)
    assert spec is not None
    return tuple(re.findall(r'"([^"]+)"', t1b)) + (flip,)


def test_person_facts_are_real_person_attributes():
    for f in PERSON_FACTS:
        assert f in Person.model_fields or isinstance(getattr(Person, f, None), property), f


def test_household_facts_are_real_household_fields():
    for f in HOUSEHOLD_FACTS:
        assert f in Household.model_fields, f


def test_every_withholdable_fact_is_expressible_in_the_vocabulary():
    for fact in _build_split_facts():
        pid, _, field = fact.partition(".")
        if field:
            assert re.fullmatch(r"p\d+", pid) and field in PERSON_FACTS, fact
        else:
            assert fact in HOUSEHOLD_FACTS, fact


def test_every_identifier_reaches_the_prompt():
    from redtape.envs.t1_eligibility import SYSTEM_PROMPT

    for f in PERSON_FACTS + HOUSEHOLD_FACTS:
        assert re.search(rf"\b{re.escape(f)}\b", SYSTEM_PROMPT), f
    assert OTHER_FACT_PREFIX in SYSTEM_PROMPT


def test_the_worked_example_names_no_real_fact():
    """The confound guard. Any real identifier in the example is privileged over the rest."""
    from redtape.envs.t1_eligibility import _answer_shape

    shape = _answer_shape()
    named = re.findall(r'"missing_fact":\s*"([^"]*)"', shape)
    assert named, "the example should still show the field"
    for n in named:
        field = n.partition(".")[2] or n
        assert field not in PERSON_FACTS + HOUSEHOLD_FACTS, n
