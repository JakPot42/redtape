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
    score_exact_match,
    score_periods,
)
from redtape.schemas import (
    AnnualAmount,
    CannotDetermine,
    MedicaidAnswer,
    SnapAnswer,
    T1Answer,
)
from redtape.scoring.parsing import ParseFailure, parse_answer
from redtape.schemas import Determinability

def _answer_shape() -> str:
    """The exact answer shape, rendered from `T1Answer` itself.

    Hand-writing the schema into the prompt is how the prompt and the parser drift apart.
    The first live run scored 0.000 on every headline because the prompt said only "a
    single JSON object" and named no fields: the model produced a well-reasoned answer
    using `monthly_benefit`, `annual_amount` and `period` where the schema wanted
    `benefit`, `amount` and `period_label`, and all 10 responses were rejected as
    `schema_invalid`. That measured field-name telepathy, not determinability.

    Nothing caught it because every baseline and scripted agent constructs `T1Answer` in
    Python and never goes through the prompt at all - so the one path a real model takes
    was the one path nothing exercised.

    Generating this from the model means a schema change updates the prompt automatically.
    It also changes `Task.hash` and every response-cache key, which is correct: a different
    prompt is a different task and must not reuse cached answers.
    """
    example = T1Answer(
        snap=SnapAnswer(period_label="2025-11", eligible=True, benefit=298.0),
        medicaid=MedicaidAnswer(period_label="2025", person_eligible={"p1": True}),
        eitc=AnnualAmount(period_label="2025", amount=0.0),
        ctc=AnnualAmount(period_label="2025", amount=4400.0, gross_entitlement=4400.0),
        cannot_determine=(
            CannotDetermine(program="snap", missing_fact="p1.employment_income"),
        ),
    )
    return example.model_dump_json(indent=2)


SYSTEM_PROMPT = (
    "You are a benefits eligibility analyst. Given a household case file, determine "
    "SNAP, EITC and Child Tax Credit outcomes.\n\n"
    "Answer with a single JSON object and nothing else - no prose, no markdown fence. "
    "Use EXACTLY these field names; any other shape is rejected unread:\n\n"
    + _answer_shape()
    + "\n\n"
    "Field notes. `period_label` is the period the figure covers: \"YYYY-MM\" for SNAP, "
    "\"YYYY\" for EITC and CTC. `benefit` is SNAP for that ONE MONTH, never annualized. "
    "`amount` is what the household actually RECEIVES; put any larger nominal entitlement "
    "in `gross_entitlement` (a zero-income family with two children is entitled to $4,400 "
    "of CTC and receives $0). `medicaid.person_eligible` maps each person id to a boolean "
    "and is recorded but NOT scored. Omit `cannot_determine` entirely, or give it as an "
    "empty list, when nothing is missing.\n\n"
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
    pair_role: str = ""
    """Which member of the pair: "with_disability" / "without_disability"."""
    is_eligibility_flip: bool = False
    """The withheld fact flips SNAP *eligibility*, not merely an amount."""
    engine_version: str = ""
    python_version: str = ""

    @property
    def answer_key(self) -> T1Answer:
        return T1Answer.model_validate_json(self.answer_key_json)


class T1Config(TasksetConfig):
    task: T1TaskConfig = T1TaskConfig()
    split_path: str = "data/dev/t1.jsonl"
    """Path to the split. Relative paths resolve against the working directory first (so a
    repo checkout works unchanged), then against the copy shipped inside the package."""


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

    def _gate(self, trace: Trace) -> bool:
        """The pass/fail filter that precedes all scoring.

        Two conditions, both structural rather than substantive:

        * **format compliance** - the reply parses into a valid `T1Answer`;
        * **degenerate-answer detection** - `score_antihack` finds nothing.

        A response failing either has not made a scoreable attempt, so it earns nothing on
        any component. This is the change from Milestone 1, where antihack was 5% of a
        weighted sum: a degenerate answer could bank the other 95%, which is precisely
        backwards. Gating also keeps the composite honest, because the components now only
        ever describe responses that were real attempts.

        The gate result is cached on the trace so every reward sees one verdict.
        """
        cached = trace.info.get("_gate")
        if cached is not None:
            return cached

        p = self._parsed(trace)
        if not p.ok:
            trace.info["_gate"] = False
            trace.info["gate_failure"] = f"format:{p.failure.value}"
            return False

        scored = score_antihack(
            p.answer, self.data.answer_key, tuple(self.data.deciding_programs)
        )
        self._record(trace, "antihack", scored)
        passed = scored.ok and scored.value == 1.0
        trace.info["_gate"] = passed
        if not passed:
            trace.info["gate_failure"] = "antihack:" + ",".join(
                scored.detail.get("failed", []) or ["scorer_error"]
            )
        return passed

    # ---------------------------------------------------------------- metrics
    @metric
    async def parsed_ok(self, trace: Trace) -> float:
        """Format failures are reported SEPARATELY from wrong answers (SPEC 4)."""
        return 1.0 if self._parsed(trace).ok else 0.0

    @metric
    async def gate_passed(self, trace: Trace) -> float:
        """Format compliance AND degenerate-answer detection, as a pass/fail filter."""
        return 1.0 if self._gate(trace) else 0.0

    @metric
    async def antihack_pass(self, trace: Trace) -> float:
        """Antihack alone, so a gate failure can be attributed to format or to hacking."""
        p = self._parsed(trace)
        if not p.ok:
            return 0.0
        s = score_antihack(p.answer, self.data.answer_key, tuple(self.data.deciding_programs))
        return 1.0 if (s.ok and s.value == 1.0) else 0.0

    # -------------------------------------------------- headline metric inputs
    # The three headline numbers (CLAUDE.md, "Three headline metrics") are computed from
    # these per-task metrics by eval/metrics.py. They are metrics, not rewards, because a
    # headline must not move when a weight is retuned.

    @metric
    async def exact_match(self, trace: Trace) -> float:
        """All-correct on this task. Headline (a) averages this over DETERMINATE tasks."""
        if not self._gate(trace):
            return 0.0
        return self._record(
            trace, "exact_match",
            score_exact_match(self._parsed(trace).answer, self.data.answer_key,
                              self.config.tolerance),
        )

    @metric
    async def abstention_correct(self, trace: Trace) -> float:
        """Fully right on the abstention decision. Headline (b) averages this over T1b."""
        if not self._gate(trace):
            return 0.0
        s = score_abstention(
            self._parsed(trace).answer,
            Determinability(self.data.determinability),
            tuple(self.data.deciding_programs),
        )
        return 1.0 if (s.ok and s.value == 1.0) else 0.0

    @metric
    async def is_determinate(self, trace: Trace) -> float:
        """Task class, carried into the results file so headlines can be recomputed."""
        return 1.0 if self.data.determinability == Determinability.DETERMINATE.value else 0.0

    @metric
    async def is_t1b(self, trace: Trace) -> float:
        """A fact was withheld: indeterminate OR incomplete-determinate."""
        return 0.0 if self.data.determinability == Determinability.DETERMINATE.value else 1.0

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
    #
    # Weights sum to 1.0 over four components. Antihack is no longer among them - it is
    # the gate above. Its vacated 0.05 was redistributed across amounts, eligibility and
    # periods in proportion to their existing weights, holding **abstention at exactly
    # 0.35**, because 0.35 is a deliberate signal about where the novelty of this
    # benchmark lies and it should not drift as a side-effect of a bookkeeping change.
    #
    #     amounts       0.30 -> 0.325
    #     eligibility   0.20 -> 0.215
    #     periods       0.10 -> 0.110
    #     abstention    0.35 -> 0.350   (unchanged, by decision)
    #     antihack      0.05 -> gate
    #
    # The composite is reported but is NOT the headline. See eval/metrics.py.

    @reward(weight=0.325)
    async def amounts(self, trace: Trace) -> float:
        if not self._gate(trace):
            return 0.0
        return self._record(
            trace, "amounts",
            score_amounts(self._parsed(trace).answer, self.data.answer_key,
                          self.config.tolerance),
        )

    @reward(weight=0.215)
    async def eligibility(self, trace: Trace) -> float:
        if not self._gate(trace):
            return 0.0
        return self._record(
            trace, "eligibility",
            score_eligibility(self._parsed(trace).answer, self.data.answer_key),
        )

    @reward(weight=0.110)
    async def periods(self, trace: Trace) -> float:
        if not self._gate(trace):
            return 0.0
        return self._record(
            trace, "periods",
            score_periods(self._parsed(trace).answer, self.data.answer_key),
        )

    @reward(weight=0.350)
    async def abstention(self, trace: Trace) -> float:
        """The core of what v0 adds. Weighted highest of the four, by decision."""
        if not self._gate(trace):
            return 0.0
        return self._record(
            trace, "abstention",
            score_abstention(
                self._parsed(trace).answer,
                Determinability(self.data.determinability),
                tuple(self.data.deciding_programs),
            ),
        )


def _resolve_split(split_path):
    """Find the split whether we are in a checkout or an installed wheel.

    The default is a repo-relative path, which is right for development and wrong for an
    installed package - a stranger who `pip install`s this has no `data/dev/` in their
    working directory. Rather than force an absolute path on every caller, resolve in the
    order that makes both work, and fail with the paths actually tried rather than a bare
    FileNotFoundError.
    """
    from pathlib import Path

    given = Path(split_path)
    tried = [given]
    if given.is_file():
        return given

    # Shipped alongside the package: see [tool.hatch.build.targets.wheel.force-include].
    packaged = Path(__file__).resolve().parent.parent / "data" / Path(split_path).name
    tried.append(packaged)
    if packaged.is_file():
        return packaged

    raise FileNotFoundError(
        "could not find the split. Tried:\n  "
        + "\n  ".join(str(t) for t in tried)
        + "\n\nIf you installed this as a package, leave `split_path` at its default and "
          "the packaged dev split will be used. To build a split yourself you need the "
          "generation extra: pip install 'redtape[generate]', then "
          "python scripts/build_split.py --split dev"
    )


class T1Taskset(Taskset[T1Task, T1Config]):
    task_type = T1Task

    def load(self) -> Iterator[T1Task]:
        with _resolve_split(self.config.split_path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield T1Task(T1Data.model_validate(json.loads(line)), self.config.task)
