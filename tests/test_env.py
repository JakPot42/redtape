"""Phase 2: parsing, scoring, and the v1 environment's scoring path.

Includes the empirical `boundary()` verification: a scorer is forced to raise inside a
real `Task.score()` call and must surface as a typed `TaskError`, not a silent zero.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redtape.scoring.core import (
    Scored,
    score_abstention,
    score_amounts,
    score_antihack,
    score_eligibility,
)
from redtape.scoring.parsing import ParseFailure, parse_answer
from redtape.schemas import (
    AnnualAmount,
    CannotDetermine,
    Determinability,
    MedicaidAnswer,
    SnapAnswer,
    T1Answer,
)

SPLIT = Path(__file__).resolve().parents[1] / "data" / "dev" / "t1_smoke.jsonl"


def _answer(snap=500.0, eligible=True, eitc=1000.0, ctc=2200.0, cd=()):
    return T1Answer(
        snap=SnapAnswer(period_label="2025-08", eligible=eligible, benefit=snap),
        medicaid=MedicaidAnswer(period_label="2025", person_eligible={"p1": True}),
        eitc=AnnualAmount(period_label="2025", amount=eitc),
        ctc=AnnualAmount(period_label="2025", amount=ctc),
        cannot_determine=tuple(CannotDetermine(program=p, missing_fact=f) for p, f in cd),
    )


# ------------------------------------------------------------------ parsing
def test_parses_bare_json():
    assert parse_answer(_answer().model_dump_json()).ok


def test_parses_json_in_a_fenced_block_with_prose():
    text = f"Here you go.\n\n```json\n{_answer().model_dump_json()}\n```\nDone."
    r = parse_answer(text)
    assert r.ok and r.failure is ParseFailure.NONE


def test_prose_with_no_json_is_malformed_not_wrong():
    r = parse_answer("I think they get about five hundred dollars.")
    assert not r.ok
    assert r.failure is ParseFailure.NO_JSON_FOUND


def test_broken_json_is_reported_as_malformed():
    r = parse_answer('{"snap": {"benefit": 500,}}')
    assert not r.ok
    assert r.failure in (ParseFailure.MALFORMED_JSON, ParseFailure.SCHEMA_INVALID)


def test_valid_json_wrong_shape_is_schema_invalid_not_malformed():
    r = parse_answer('{"snap": 500, "eitc": 1000}')
    assert not r.ok
    assert r.failure is ParseFailure.SCHEMA_INVALID
    assert r.detail


def test_empty_output_is_not_a_crash():
    assert parse_answer("").failure is ParseFailure.NO_JSON_FOUND


# ------------------------------------------------------------------ scoring
def test_amounts_within_tolerance_count_as_correct():
    truth = _answer()
    given = _answer(snap=500.9)
    assert score_amounts(given, truth).value == 1.0


def test_amounts_beyond_tolerance_do_not():
    assert score_amounts(_answer(snap=520.0), _answer()).value < 1.0


def test_abstention_indeterminate_requires_abstaining():
    truth_label = Determinability.INDETERMINATE
    assert score_abstention(_answer(), truth_label, ("snap",)).value == 0.0
    good = _answer(cd=(("snap", "p1.employment_income"),))
    assert score_abstention(good, truth_label, ("snap",)).value == 1.0


def test_abstention_on_the_wrong_programs_is_partial_not_full():
    good_but_broad = _answer(cd=(("snap", "x"), ("eitc", "x")))
    assert score_abstention(good_but_broad, Determinability.INDETERMINATE, ("snap",)).value == 0.5


def test_needless_abstention_on_an_incomplete_determinate_task_scores_zero():
    """Class 3 is scored as strictly as class 2 - this is what stops always-abstain."""
    given = _answer(cd=(("snap", "housing_cost"),))
    assert score_abstention(given, Determinability.INCOMPLETE_DETERMINATE, ()).value == 0.0


def test_answering_an_incomplete_determinate_task_scores_full():
    assert score_abstention(_answer(), Determinability.INCOMPLETE_DETERMINATE, ()).value == 1.0


def test_full_abstention_is_not_antihacked_when_everything_is_deciding():
    """Abstaining on all three scored programs is correct if all three turn on the fact."""
    given = _answer(cd=(("snap", "x"), ("eitc", "x"), ("ctc", "x")))
    assert score_antihack(given, _answer(), ("snap", "eitc", "ctc")).value == 1.0


def test_full_abstention_is_antihacked_when_nothing_is_deciding():
    given = _answer(cd=(("snap", "x"), ("eitc", "x"), ("ctc", "x")))
    assert score_antihack(given, _answer(), ()).value == 0.0


def test_a_raising_scorer_returns_an_explicit_error_not_a_silent_zero():
    """The rubric must never be what catches a scorer bug."""
    result = score_amounts(None, _answer())  # type: ignore[arg-type]
    assert isinstance(result, Scored)
    assert result.value == 0.0
    assert result.error, "a crashed scorer must carry an explicit error"
    assert not result.ok


# ------------------------------------------------------------------ environment
pytest.importorskip("verifiers.v1", reason="v1 requires Linux")


def _tasks():
    from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig

    if not SPLIT.exists():
        pytest.skip("smoke split not built")
    cfg = T1TaskConfig()
    with SPLIT.open(encoding="utf-8") as fh:
        return [
            T1Task(T1Data.model_validate(json.loads(line)), cfg)
            for line in fh
            if line.strip()
        ]


def _trace(task, reply: str):
    from verifiers.v1.graph import MessageNode
    from verifiers.v1.trace import AgentInfo, Trace, TraceTask, WireAgentConfig
    from verifiers.v1.types import AssistantMessage

    t = Trace(
        task=TraceTask(type=type(task).__name__, key=task.key, hash=task.hash, data=task.data),
        agent=AgentInfo(config=WireAgentConfig(), name="stub"),
    )
    t.nodes.append(MessageNode(message=AssistantMessage(content=reply), sampled=True))
    return t


def test_task_hash_is_stable_and_content_derived():
    tasks = _tasks()
    assert tasks[0].hash == tasks[0].hash
    assert len({t.hash for t in tasks}) == len(tasks), "task hashes must be unique"


def test_perfect_answer_scores_full_on_every_channel():
    tasks = _tasks()
    task = tasks[0]
    trace = _trace(task, task.data.answer_key.model_dump_json())
    asyncio.run(task.score(trace))
    for name, reward in trace.rewards.items():
        assert reward.score == pytest.approx(1.0), f"{name} scored {reward.score}"
    assert trace.metrics["parsed_ok"] == 1.0
    assert trace.metrics["scorer_error"] == 0.0


def test_malformed_output_is_reported_separately_from_a_wrong_answer():
    task = _tasks()[0]
    trace = _trace(task, "about five hundred dollars I think")
    asyncio.run(task.score(trace))
    assert trace.metrics["parsed_ok"] == 0.0
    assert trace.metrics["malformed_json"] == 1.0
    assert trace.metrics["schema_invalid"] == 0.0
    assert all(r.score == 0.0 for r in trace.rewards.values())


def test_boundary_surfaces_a_raising_scorer_as_a_typed_TaskError():
    """Empirical check that v1's boundary() re-raises rather than swallowing.

    Legacy's Rubric returns 0.0 on a scorer exception, which is indistinguishable from a
    model answering wrong and fails *toward* "the model is bad". v1 must not.

    The failure is induced through real data rather than a monkeypatch: the `@reward`
    decorator captures the function at class-definition time, so replacing the attribute
    does not reach what `score()` invokes. A corrupt `answer_key_json` is also the more
    realistic fault - it is what a damaged dataset row would do - and it raises inside a
    reward on the genuine scoring path.
    """
    from verifiers.v1.errors import TaskError

    from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig

    good = _tasks()[0]
    broken_data = T1Data.model_validate(
        good.data.model_dump() | {"answer_key_json": "{not valid json"}
    )
    task = T1Task(broken_data, T1TaskConfig())
    trace = _trace(task, good.data.answer_key.model_dump_json())

    with pytest.raises(TaskError) as exc:
        asyncio.run(task.score(trace))
    assert "T1Task scoring" in str(exc.value), (
        f"expected a typed TaskError naming the boundary, got: {exc.value}"
    )


def test_a_scorer_error_is_not_silently_zeroed():
    """The complement: a scorer that fails must never look like a wrong answer.

    Guards the property that matters even if boundary() were to change upstream - a
    failure produces an error, never a quiet 0.0 indistinguishable from a bad model.
    """
    from redtape.scoring.core import score_amounts

    result = score_amounts(None, _tasks()[0].data.answer_key)  # type: ignore[arg-type]
    assert result.value == 0.0 and result.error and not result.ok
