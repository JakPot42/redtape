"""Stub agent: drive the full v1 scoring path with a controlled reply.

A stub is the right tool for testing the scoring path, because the input is exact and a
scoring bug cannot hide behind model variance. A real client call is still required
before Checkpoint 2 closes - stubs have a way of passing while the integration does not.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from verifiers.v1.trace import Trace
from verifiers.v1.types import AssistantMessage

from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig


def make_trace(task: "T1Task", reply: str) -> Trace:
    """A trace carrying one sampled assistant message - what `last_reply` reads.

    Trace requires `task` and `agent`, so the stub supplies the same shapes the runtime
    would: a TraceTask carrying the task's wire data and identity, and a minimal
    AgentInfo. Building it through the real types rather than a mock means the scoring
    path we exercise is the scoring path that runs in production.
    """
    from verifiers.v1.graph import MessageNode
    from verifiers.v1.trace import AgentInfo, TraceTask, WireAgentConfig

    trace = Trace(
        task=TraceTask(
            type=type(task).__name__,
            key=task.key,
            hash=task.hash,
            data=task.data,
        ),
        agent=AgentInfo(config=WireAgentConfig(), name="stub"),
    )
    trace.nodes.append(MessageNode(message=AssistantMessage(content=reply), sampled=True))
    return trace


async def score_one(task: T1Task, reply: str) -> Trace:
    trace = make_trace(task, reply)
    await task.score(trace)
    return trace


def load_tasks(path: str = "data/dev/t1_smoke.jsonl") -> list[T1Task]:
    cfg = T1TaskConfig()
    tasks = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                tasks.append(T1Task(T1Data.model_validate(json.loads(line)), cfg))
    return tasks


def report(label: str, trace: Trace) -> None:
    print(f"\n--- {label} ---")
    print(f"  rewards : {dict(trace.rewards)}")
    print(f"  metrics : {dict(trace.metrics)}")
    interesting = {
        k: v for k, v in trace.info.items()
        if not k.startswith("_") and ("error" in k or "parse" in k or "abstention" in k)
    }
    for k, v in interesting.items():
        print(f"  {k}: {v}")


async def main() -> None:
    tasks = load_tasks()
    task = tasks[0]
    key = task.data.answer_key

    print("=" * 78)
    print(f"TASK {task.data.household_id}   hash={task.hash[:16]}...")
    print(f"  determinability = {task.data.determinability}")
    print(f"  withheld        = {task.data.withheld_fact or '(none)'}")
    print("=" * 78)

    # 1. Exactly correct.
    report("perfect answer", await score_one(task, key.model_dump_json()))

    # 2. Wrong amounts, right shape.
    wrong = key.model_copy(
        update={"snap": key.snap.model_copy(update={"benefit": key.snap.benefit + 250})}
    )
    report("wrong SNAP amount", await score_one(task, wrong.model_dump_json()))

    # 3. Needless abstention on a determinate task.
    abstained = key.model_copy(
        update={"cannot_determine": ({"program": "snap", "missing_fact": "housing_cost"},)}
    )
    report("needless abstention", await score_one(task, abstained.model_dump_json()))

    # 4. Malformed JSON - must be reported separately from a wrong answer.
    report("malformed JSON", await score_one(task, "the household gets about $500 I think"))

    # 5. JSON wrapped in prose and a fence - should still parse.
    fenced = f"Here is my analysis.\n\n```json\n{key.model_dump_json()}\n```\nHope that helps."
    report("fenced JSON in prose", await score_one(task, fenced))

    # 6. An INDETERMINATE task answered with a correct abstention.
    ind = next(
        (t for t in tasks if t.data.determinability == "indeterminate"), None
    )
    if ind is not None:
        k = ind.data.answer_key
        good = k.model_copy(
            update={
                "cannot_determine": tuple(
                    {"program": p, "missing_fact": ind.data.withheld_fact}
                    for p in ind.data.deciding_programs
                )
            }
        )
        print("\n" + "=" * 78)
        print(f"INDETERMINATE TASK {ind.data.household_id}")
        print(f"  withheld = {ind.data.withheld_fact}   deciding = {list(ind.data.deciding_programs)}")
        print("=" * 78)
        report("correct abstention", await score_one(ind, good.model_dump_json()))
        report("failed to abstain", await score_one(ind, k.model_dump_json()))


if __name__ == "__main__":
    asyncio.run(main())
