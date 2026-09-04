"""Drive REAL model output through `parse_answer`. Closes the gap in docs/LIMITS.md 25.

Every other test in this suite builds a `T1Answer` in Python and serialises it, which cannot
be wrong in the way real model output is wrong: the fixture and the consumer are the same
code. That gap let two defects reach a scored run.

  1. The prompt named no fields, so the model invented `monthly_benefit` / `annual_amount` /
     `period`. All ten sampled replies were rejected and the first live run scored 0.000.
  2. The schema required a float where the prompt told the model to abstain instead of
     guessing. A model that abstained correctly wrote `null` and was rejected as malformed -
     47 of 1,200 responses, 34 of them abstentions that would otherwise have scored CORRECT.

Both were invisible to a suite whose inputs were constructed on the far side of the parser.
The fixtures here are verbatim strings Claude Opus 5 actually produced, taken from the
committed response cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redtape.envs.t1_eligibility import SYSTEM_PROMPT
from redtape.schemas import T1Answer
from redtape.scoring.core import score_amounts, score_antihack, score_eligibility
from redtape.scoring.parsing import ParseFailure, parse_answer

FIXTURES = Path(__file__).resolve().parent / "data" / "model_replies.json"


@pytest.fixture(scope="module")
def replies() -> dict:
    if not FIXTURES.is_file():
        pytest.fail(
            f"{FIXTURES} missing. Regenerate with "
            f"./.venv/bin/python scripts/build_reply_fixtures.py"
        )
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


# ----------------------------------------------------------------- the happy path, for real
def test_real_valid_replies_parse(replies):
    """Not a round-trip: these strings were produced by a model, not by our serialiser."""
    bad = []
    for reply in replies["valid"]:
        r = parse_answer(reply)
        if not r.ok:
            bad.append((r.failure.value, reply[:120]))
    assert not bad, f"{len(bad)} real valid replies failed to parse: {bad[:3]}"


def test_the_corpus_is_not_trivially_small(replies):
    """A fixture set too small to contain the failure modes is not coverage."""
    assert len(replies["valid"]) >= 20
    assert len(replies["abstaining_with_null"]) >= 10


# ------------------------------------------------------- the abstention/null regression
def test_real_abstentions_with_null_amounts_parse(replies):
    """The LIMITS 27 defect, asserted against the exact replies that were rejected.

    A model that abstains cannot state the amount it just said it could not determine.
    These are real replies that named a program in `cannot_determine` and left that
    program's value null. Every one was rejected before the fix.
    """
    assert replies["abstaining_with_null"], "no null-bearing fixtures captured"
    for reply in replies["abstaining_with_null"]:
        r = parse_answer(reply)
        assert r.ok, (
            f"a correct abstention was rejected as {r.failure.value}: {reply[:160]}"
        )
        ans = r.answer
        abstained = {c.program for c in ans.cannot_determine}
        assert abstained, "fixture should carry an abstention"
        if ans.snap.benefit is None:
            assert "snap" in abstained
        if ans.eitc.amount is None:
            assert "eitc" in abstained
        if ans.ctc.amount is None:
            assert "ctc" in abstained


def test_null_without_abstention_is_still_rejected():
    """The fix must not become a hole. Null is legitimate only alongside an abstention."""
    payload = {
        "snap": {"period": "month", "period_label": "2025-09",
                 "eligible": None, "benefit": None},
        "medicaid": {"period": "year", "period_label": "2025",
                     "person_eligible": {"p1": True}, "scored": False},
        "eitc": {"period": "year", "period_label": "2025", "amount": 0.0},
        "ctc": {"period": "year", "period_label": "2025", "amount": 0.0},
        "cannot_determine": [],
    }
    r = parse_answer(json.dumps(payload))
    assert not r.ok, "null with no abstention must not parse"
    assert r.failure is ParseFailure.SCHEMA_INVALID


def test_scorers_do_not_raise_on_a_null_answer(replies):
    """A null answer used to raise TypeError inside the scorers.

    The guard turns that into a scorer_error, and any run with a non-zero scorer_error count
    is unpublishable - so a correct abstention would have made the whole run unpublishable
    rather than merely mis-scored.
    """
    truth = parse_answer(replies["valid"][0]).answer
    for reply in replies["abstaining_with_null"][:10]:
        given = parse_answer(reply).answer
        for fn in (score_amounts, score_eligibility):
            s = fn(given, truth)
            assert s.ok, f"{fn.__name__} raised on a null answer: {s.error}"
        s = score_antihack(given, truth, ("snap",))
        assert s.ok, f"score_antihack raised on a null answer: {s.error}"


# -------------------------------------------------------------- the original field-name bug
@pytest.mark.parametrize("field,value", [
    ("monthly_benefit", 298.0),
    ("annual_amount", 0.0),
])
def test_pre_fix_field_names_are_still_rejected(field, value):
    """The shape the model invented when the prompt named no fields.

    If this ever parses, the schema has been loosened enough to accept the answer the
    original bug produced, and the 0.000 run could recur without anything going red.
    """
    payload = {
        "snap": {"period": "month", "period_label": "2025-09",
                 "eligible": True, field: value},
        "medicaid": {"period": "year", "period_label": "2025",
                     "person_eligible": {"p1": True}, "scored": False},
        "eitc": {"period": "year", "period_label": "2025", "amount": 0.0},
        "ctc": {"period": "year", "period_label": "2025", "amount": 0.0},
    }
    r = parse_answer(json.dumps(payload))
    assert not r.ok
    assert r.failure is ParseFailure.SCHEMA_INVALID


def test_period_instead_of_period_label_is_rejected():
    """`period` is a fixed literal; the model used it as the label. Must not be accepted."""
    payload = {
        "snap": {"period": "November 2025 (monthly)", "eligible": True, "benefit": 1.0},
        "medicaid": {"period": "year", "period_label": "2025",
                     "person_eligible": {"p1": True}, "scored": False},
        "eitc": {"period": "year", "period_label": "2025", "amount": 0.0},
        "ctc": {"period": "year", "period_label": "2025", "amount": 0.0},
    }
    assert not parse_answer(json.dumps(payload)).ok


# --------------------------------------------------------------------- prose and fencing
def test_json_inside_a_markdown_fence_is_recovered(replies):
    """No cached reply used a fence, but the prompt is the only thing preventing it, and a
    prompt is not a guarantee. Assert the behaviour rather than rely on the instruction."""
    inner = replies["valid"][0]
    fenced = f"Here is the determination:\n\n```json\n{inner}\n```\n"
    r = parse_answer(fenced)
    assert r.ok, f"fenced JSON should be recovered, got {r.failure.value}"


def test_empty_output_is_no_json_found(replies):
    for reply in replies["empty"]:
        r = parse_answer(reply)
        assert not r.ok
        assert r.failure is ParseFailure.NO_JSON_FOUND


def test_prose_with_no_json_is_no_json_found():
    r = parse_answer("I cannot determine this household's benefits from the case file.")
    assert not r.ok
    assert r.failure is ParseFailure.NO_JSON_FOUND


# ------------------------------------------------------------------------- the prompt itself
def test_the_prompt_still_states_every_required_field():
    """Teeth for the LIMITS 25 defect.

    The prompt renders its shape from `T1Answer`, so this should hold automatically - but
    that is exactly the kind of automatic guarantee worth asserting, because the failure it
    replaced was silent and cost a full scored run.
    """
    required = ("period_label", "benefit", "amount", "gross_entitlement",
                "cannot_determine", "missing_fact", "person_eligible")
    missing = [f for f in required if f not in SYSTEM_PROMPT]
    assert not missing, f"SYSTEM_PROMPT no longer names {missing}"


def test_the_prompt_example_is_itself_a_valid_answer():
    """The example shown to the model must parse. If it does not, we are instructing the
    model to produce something the parser rejects."""
    start = SYSTEM_PROMPT.index("{")
    end = SYSTEM_PROMPT.rindex("}") + 1
    r = parse_answer(SYSTEM_PROMPT[start:end])
    assert r.ok, f"the schema example in the prompt does not parse: {r.failure.value}"
    assert isinstance(r.answer, T1Answer)
