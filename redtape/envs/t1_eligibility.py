"""T1 / T1b as a `verifiers.v1` environment.

**This is the only module in the project that imports `verifiers`** (CLAUDE.md). The
generator, the oracle and every scoring function take plain data and return plain values,
so a future v1 API change touches this file alone and the scorers stay unit-testable
without a rollout.

Answer keys are baked at generation time and carried in `TaskData`. The oracle is never
called during a rollout - a cold PolicyEngine import is ~30s and calling it per rollout
would also let an engine version bump silently change the key mid-run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from verifiers.v1.configs.task import TaskConfig
from verifiers.v1.configs.taskset import TasksetConfig
from verifiers.v1.state import State
from verifiers.v1.task import Task, TaskData
from verifiers.v1.taskset import Taskset
from verifiers.v1.trace import Trace
from verifiers.v1.utils.decorators import metric, reward

from redtape.scoring.core import (
    score_abstention,
    score_amounts,
    score_antihack,
    score_eligibility,
    score_periods,
)
from redtape.scoring.parsing import ParseFailure, parse_answer
from redtape.schemas import Determinability, T1Answer

SYSTEM_PROMPT = (
    "You are a benefits eligibility analyst. Given a household case file, determine "
    "SNAP, EITC and Child Tax Credit outcomes.\n\n"
    "Answer with a single JSON object and nothing else. Every amount states the period "
    "it covers: SNAP is a MONTHLY benefit for the month named in the case file; EITC "
    "and CTC are ANNUAL amounts for the tax year. Report what the household actually "
    "RECEIVES, not what it is nominally entitled to.\n\n"
    "If a fact required to determine a program's outcome is missing from the case file, "
    "list that program in `cannot_determine` with the missing fact, instead of guessing. "
    "If a fact is missing but the outcome does not depend on it, answer normally - a "
    "needless abstention is scored as wrong as a wrong number."
)


class T1TaskConfig(TaskConfig):
    tolerance: float = 1.0


class T1Data(TaskData):
    """Task payload. `Task.hash` is a content hash of this, so it is the task identity."""

    household_id: str
    seed: int
    index: int
    answer_key_json: str
    determinability: str = Determinability.DETERMINATE.value
    deciding_programs: tuple[str, ...] = ()
    withheld_fact: str = ""
    pair_id: str = ""
    """Set for deliberately paired cases so pair-consistency can be reported."""
    engine_version: str = ""
    python_version: str = ""

    @property
    def answer_key(self) -> T1Answer:
        return T1Answer.model_validate_json(self.answer_key_json)


class T1Config(TasksetConfig):
    task: T1TaskConfig = T1TaskConfig()
    split_path: str = "data/dev/t1.jsonl"


class T1Task(Task[T1Data, State, T1TaskConfig]):
    NEEDS_CONTAINER = False

    # ---------------------------------------------------------------- helpers
    def _parsed(self, trace: Trace):
        """Parse once; cache on the trace so every scorer sees the same result."""
        cached = trace.info.get("_parsed")
        if cached is None:
            reply = trace.last_reply or ""
            text = reply if isinstance(reply, str) else str(reply)
            cached = parse_answer(text)
            trace.info["_parsed"] = cached
            trace.info["parse_failure"] = cached.failure.value
            if not cached.ok:
                trace.info["parse_detail"] = cached.detail
        return cached

    def _record(self, trace: Trace, name: str, scored) -> float:
        """Record detail and, crucially, scorer_error as its own metric."""
        trace.info[f"{name}_detail"] = scored.detail
        if scored.error:
            # A scorer that raised must be visible in the results file, not merely in a
            # traceback. A run with any scorer_error is not publishable.
            trace.info[f"{name}_error"] = scored.error
            trace.record_metric("scorer_error", 1.0)
        return scored.value

    # ---------------------------------------------------------------- metrics
    @metric
    async def parsed_ok(self, trace: Trace) -> float:
        """Format failures are reported SEPARATELY from wrong answers (SPEC 4)."""
        return 1.0 if self._parsed(trace).ok else 0.0

    @metric
    async def malformed_json(self, trace: Trace) -> float:
        p = self._parsed(trace)
        return 1.0 if p.failure in (ParseFailure.MALFORMED_JSON, ParseFailure.NO_JSON_FOUND) else 0.0

    @metric
    async def schema_invalid(self, trace: Trace) -> float:
        return 1.0 if self._parsed(trace).failure is ParseFailure.SCHEMA_INVALID else 0.0

    @metric
    async def scorer_error(self, trace: Trace) -> float:
        """Always emitted, so a clean run records a zero rather than nothing."""
        return 0.0

    # ---------------------------------------------------------------- rewards
    @reward(weight=0.30)
    async def amounts(self, trace: Trace) -> float:
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        return self._record(
            trace, "amounts",
            score_amounts(p.answer, self.data.answer_key, self.config.tolerance),
        )

    @reward(weight=0.20)
    async def eligibility(self, trace: Trace) -> float:
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        return self._record(trace, "eligibility", score_eligibility(p.answer, self.data.answer_key))

    @reward(weight=0.10)
    async def periods(self, trace: Trace) -> float:
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        return self._record(trace, "periods", score_periods(p.answer, self.data.answer_key))

    @reward(weight=0.35)
    async def abstention(self, trace: Trace) -> float:
        """The core of what v0 adds. Weighted highest of the four."""
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        return self._record(
            trace, "abstention",
            score_abstention(
                p.answer,
                Determinability(self.data.determinability),
                tuple(self.data.deciding_programs),
            ),
        )

    @reward(weight=0.05)
    async def antihack(self, trace: Trace) -> float:
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        return self._record(
            trace, "antihack",
            score_antihack(
                p.answer, self.data.answer_key, tuple(self.data.deciding_programs)
            ),
        )


class T1Taskset(Taskset[T1Task, T1Config]):
    task_type = T1Task

    def load(self) -> Iterator[T1Task]:
        from pathlib import Path

        path = Path(self.config.split_path)
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield T1Task(T1Data.model_validate(json.loads(line)), self.config.task)
