"""Run a split through the real scoring path and write a results file.

Everything - baselines, tool conditions, live models - is scored by calling the actual
`T1Task.score()` on a real `Trace`. There is no second scoring implementation for
baselines, because a baseline scored by different code is not a baseline.

Run it as a MODULE, not as a file path. `python eval/run_eval.py` puts `eval/` itself on
sys.path instead of the repo root, so `from eval.baselines import ...` fails with
ModuleNotFoundError - the invocation this docstring used to document never worked, and
nobody found out because the file had never been run.

    # five trivial baselines on the dev split
    ./.venv/bin/python -m eval.run_eval baselines --split data/dev/t1.jsonl

    # the three tool conditions on a sample, using the scripted agent (no API key)
    ./.venv/bin/python -m eval.run_eval conditions --split data/dev/t1.jsonl --sample 12

    # one live call to confirm the end-to-end path
    ./.venv/bin/python -m eval.run_eval live --split data/dev/t1.jsonl --limit 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

from eval.baselines import BASELINES, PAIR_DIAGNOSTICS
from eval.metrics import TaskRecord, assert_publishable, build_results, redact
from eval.tools import UNKNOWN, calculate, tool_schema
from redtape.config import load_dotenv

MODEL = "claude-opus-5"

CONDITIONS = ("tool_less", "tool_equipped", "tool_equipped_unknowns")


# ------------------------------------------------------------------ task loading
def load_tasks(path: str, limit: int | None = None, sample: int | None = None,
               seed: int = 0):
    from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig

    cfg = T1TaskConfig()
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    if sample:
        rows = _stratified_sample(rows, sample, seed)
    if limit:
        rows = rows[:limit]

    return [
        T1Task(T1Data.model_validate({k: v for k, v in r.items()
                                      if k not in ("task_hash", "task_key",
                                                   "unscored_deciding_programs",
                                                   "pair_truth_differs")}), cfg)
        for r in rows
    ]



def _stratified_sample(rows: list[dict], sample: int, seed: int) -> list[dict]:
    """Deterministic stratified sample: whole pairs, class mix preserved, exact size.

    The previous version returned fewer rows than asked for - `--sample 12` gave 8 - and
    could split a matched pair. Both matter:

    * **Short samples are silently wrong.** The caller believes it measured 12 tasks. Every
      headline is then computed over an `n` nobody chose, and the shortfall does not appear
      anywhere in the results file.
    * **A split pair is worse than a dropped one.** `pair_consistency` counts a pair with a
      missing member as INCONSISTENT, so truncating mid-pair does not lose a measurement,
      it manufactures a false negative.

    The bug was arithmetic: the per-class quota recomputed `(sample - len(picked)) //
    len(by_class)` on every iteration while `picked` was growing, so each class got a
    smaller share than the last, and integer division discarded the remainder on top.
    """
    rng = random.Random(f"redtape/sample/{seed}/{sample}")
    if sample >= len(rows):
        return rows

    pairs: dict[str, list] = {}
    singles: list[dict] = []
    for r in rows:
        if r.get("pair_id"):
            pairs.setdefault(r["pair_id"], []).append(r)
        else:
            singles.append(r)

    # Whole pairs only, and never more than the budget can hold intact.
    picked: list[dict] = []
    pair_groups = sorted(pairs.values(), key=lambda g: g[0]["pair_id"])
    rng.shuffle(pair_groups)
    for group in pair_groups:
        if len(picked) + len(group) > max(2, sample // 6):
            break
        picked.extend(group)

    remaining = sample - len(picked)
    by_class: dict[str, list] = {}
    for r in singles:
        by_class.setdefault(r["determinability"], []).append(r)
    for group in by_class.values():
        rng.shuffle(group)

    # Largest-remainder allocation, so the quotas sum to `remaining` exactly instead of
    # losing a row per class to integer division.
    total = sum(len(g) for g in by_class.values()) or 1
    exact = {cls: remaining * len(g) / total for cls, g in by_class.items()}
    quota = {cls: int(v) for cls, v in exact.items()}
    for cls in sorted(exact, key=lambda c: exact[c] - quota[c], reverse=True):
        if sum(quota.values()) >= remaining:
            break
        quota[cls] += 1

    for cls in sorted(by_class):
        picked.extend(by_class[cls][: quota[cls]])

    # A class can be smaller than its quota. Top up from whatever is left so the caller
    # gets the size it asked for rather than a silent shortfall.
    if len(picked) < sample:
        chosen = {id(r) for r in picked}
        leftovers = [r for r in singles if id(r) not in chosen]
        rng.shuffle(leftovers)
        picked.extend(leftovers[: sample - len(picked)])

    return picked


def _trace(task, reply: str):
    from verifiers.v1.graph import MessageNode
    from verifiers.v1.trace import AgentInfo, Trace, TraceTask, WireAgentConfig
    from verifiers.v1.types import AssistantMessage

    t = Trace(
        task=TraceTask(type=type(task).__name__, key=task.key, hash=task.hash, data=task.data),
        agent=AgentInfo(config=WireAgentConfig(), name="eval"),
    )
    t.nodes.append(MessageNode(message=AssistantMessage(content=reply), sampled=True))
    return t


def score_one(task, reply: str) -> TaskRecord:
    """Score one reply through the real v1 path and flatten it into a TaskRecord."""
    trace = _trace(task, reply)
    asyncio.run(task.score(trace))

    parsed = trace.info.get("_parsed")
    answer = parsed.answer if (parsed is not None and parsed.ok) else None
    errors = [v for k, v in trace.info.items() if k.endswith("_error") and v]

    return TaskRecord(
        task_hash=task.hash,
        household_id=task.data.household_id,
        determinability=task.data.determinability,
        gate_passed=bool(trace.metrics.get("gate_passed")),
        exact_match=bool(trace.metrics.get("exact_match")),
        abstention_correct=bool(trace.metrics.get("abstention_correct")),
        parse_failure=trace.info.get("parse_failure", "none"),
        scorer_error="; ".join(str(e) for e in errors),
        composite=trace.reward,
        rewards={k: (r.score if r else None) for k, r in trace.rewards.items()},
        pair_id=task.data.pair_id,
        pair_role=task.data.pair_role,
        is_eligibility_flip=bool(task.data.is_eligibility_flip),
        withheld_fact=task.data.withheld_fact,
        answer=answer,
        answer_key=task.data.answer_key,
    )


def write(results: dict, out: Path, *, seed: int) -> None:
    """Write the full results file, and beside it a redacted one that is safe to publish.

    The redacted sibling is written ALWAYS, not only for held-out runs. Making it
    conditional would mean deciding, at write time, whether this particular run is the
    sensitive one - and a check that has to fire on the right run is a check that
    eventually does not. The dev seed is public, so redacting a dev run costs nothing;
    the habit of quoting numbers out of `.public.json` is what is actually being bought.

    `assert_publishable` runs on the redacted copy before it is written, so a future field
    formatted from the seed fails here rather than in a published file.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    public = redact(results)
    assert_publishable(public, seed)
    public_out = out.with_suffix(".public.json")
    public_out.write_text(json.dumps(public, indent=2), encoding="utf-8")

    h = results
    print(f"  -> {out}")
    print(f"  -> {public_out}  (redacted; quote numbers from this one)")
    for field in ("t1_exact_match_determinate", "t1b_abstention_accuracy", "pair_consistency"):
        v = h[field]["value"]
        n = h[field].get("n", h[field].get("n_pairs"))
        print(f"     {field:<32} {'n/a' if v is None else f'{v:.3f}'}  (n={n})")
    print(f"     {'composite (secondary)':<32} {h['composite']['value']:.3f}")
    if not h["diagnostics"]["publishable"]:
        print(f"     !! scorer_error on {h['diagnostics']['scorer_error_count']} tasks "
              f"- NOT PUBLISHABLE")


# ------------------------------------------------------------------ agents
def baseline_agent(name, registry=None):
    fn = (registry or BASELINES)[name]

    def agent(task) -> str:
        return fn(task.data.prompt).model_dump_json()

    return agent


def oracle_agent(task) -> str:
    """Answers from the baked key, and NEVER abstains. Wiring check only.

    Its abstention score is therefore capped well below 1.0 by construction - it is right
    only on the incomplete-determinate cases, where answering is the correct move. Do not
    read its number as a ceiling on the abstention metric; `perfect_agent` is that.
    """
    return task.data.answer_key.model_dump_json()


def perfect_agent(task) -> str:
    """The CEILING check: answers from the key AND abstains on exactly the deciding
    programs. Uses answer-key information, so it is never a reported score.

    This exists because `oracle_agent` cannot establish what it appears to establish. Its
    exact-match of 1.000 reads as "the scoring path is validated", but it never emits a
    `cannot_determine`, so the abstention half - the novel claim of this whole benchmark -
    was covered by nothing at all. A metric no achievable answer can score 1.0 on is broken,
    and until this agent existed there was no way to find that out.

    If this does not score 1.0 on all three headlines, the defect is in the scoring, not in
    the agent. See CLAUDE.md, "Every green signal must be checked for what it is NOT
    measuring".
    """
    from redtape.schemas import CannotDetermine, Determinability

    answer = task.data.answer_key
    if task.data.determinability == Determinability.INDETERMINATE.value:
        answer = answer.model_copy(update={
            "cannot_determine": tuple(
                CannotDetermine(program=p, missing_fact=task.data.withheld_fact)
                for p in task.data.deciding_programs
            )
        })
    return answer.model_dump_json()


def scripted_tool_agent(condition: str):
    """A scripted stand-in for a model in the tool conditions, with no API key.

    It does what a perfect extractor would do: read the case file, call the tool with what
    the file states, and answer from the tool's reply. That is exactly the upper bound the
    tool-equipped condition is meant to measure, so this exercises the whole path -
    including the real engine call inside the tool - without spending a token.

    It reads the household from the task's own record rather than from the narrative,
    because the point of this run is to prove the TOOL path works, not to test extraction.
    A live model does the extraction itself.
    """
    from eval.baselines import read_narrative

    allow_unknown = condition == "tool_equipped_unknowns"

    def agent(task) -> str:
        r = read_narrative(task.data.prompt)
        payload = {
            "month": r.month,
            "tax_year": r.year,
            "housing_cost": (r.monthly_shelter * 12) if r.shelter_stated else "unknown",
            "dependent_care_cost": 0.0,
            # One entry per person LINE. Building from r.ages dropped anyone whose age
            # was withheld - which emptied the list entirely for a one-person household
            # and crashed the engine with "No person found" - and misaligned the rest.
            "people": [
                {
                    "person_id": p["person_id"],
                    "age": p["age"] if p["age"] is not None else 40,
                    "employment_income": (p["employment_income"]
                                          if p["employment_income"] is not None else 0.0),
                    "immigration_status": p["immigration_status"] or "CITIZEN",
                }
                for p in r.people
            ],
        }
        if not payload["people"]:
            payload["people"] = [{"person_id": "p1", "age": 40,
                                  "employment_income": 0.0,
                                  "immigration_status": "CITIZEN"}]

        # Mark the ONE fact the narrative does not state. This used to consider only
        # housing_cost, so on the four other facts the generator withholds
        # (p1.employment_income, p1.age, p1.immigration_status, p1.is_higher_ed_student)
        # the unknowns condition silently degraded into an ordinary tool_equipped run and
        # produced identical numbers - which read as "the third condition adds nothing"
        # when the truth was "the third condition barely ran". The tool sweeps one fact per
        # call and errors on more than one, which matches generation withholding exactly
        # one, so first match wins.
        if allow_unknown:
            if r.any_age_withheld:
                payload["people"][0]["age"] = UNKNOWN
            elif r.any_income_withheld:
                payload["people"][0]["employment_income"] = UNKNOWN
            elif r.any_status_withheld:
                payload["people"][0]["immigration_status"] = UNKNOWN

        if not allow_unknown and payload["housing_cost"] == UNKNOWN:
            payload["housing_cost"] = 0.0

        out = calculate(payload, allow_unknown=allow_unknown)
        if "decides_programs" in out:
            abstain = out["decides_programs"]
            # Fall back to the engine's own default for whichever fact was swept, so the
            # numeric answer is still produced. Previously this reset housing_cost
            # unconditionally, which left an "unknown" age or status in the payload and
            # made the second call fail.
            if payload["housing_cost"] == UNKNOWN:
                payload["housing_cost"] = 0.0
            for person in payload["people"]:
                if person.get("age") == UNKNOWN:
                    person["age"] = 40
                if person.get("employment_income") == UNKNOWN:
                    person["employment_income"] = 0.0
                if person.get("immigration_status") == UNKNOWN:
                    person["immigration_status"] = "CITIZEN"
            out = calculate(payload, allow_unknown=False)
        else:
            abstain = []
        if "error" in out:
            return json.dumps({"error": out["error"]})

        answer = {
            "snap": {"period": "month", "period_label": out["snap"]["period"],
                     "eligible": out["snap"]["eligible"],
                     "benefit": out["snap"]["monthly_benefit"]},
            "medicaid": {"period": "year", "period_label": str(r.year),
                         "person_eligible": {f"p{i + 1}": False for i in range(r.n_people)},
                         "scored": False},
            "eitc": {"period": "year", "period_label": out["eitc"]["period"],
                     "amount": out["eitc"]["annual_amount"]},
            "ctc": {"period": "year", "period_label": out["ctc"]["period"],
                    "amount": out["ctc"]["annual_amount_received"],
                    "gross_entitlement": out["ctc"]["gross_entitlement"]},
            "cannot_determine": [{"program": p, "missing_fact": "housing_cost"}
                                 for p in abstain],
        }
        return json.dumps(answer)

    return agent


# ------------------------------------------------------------------ the live client
def live_agent(condition: str, model: str = MODEL, max_tool_turns: int = 6):
    """A real Claude client. The API key comes from the environment - never from disk.

    Uses the SDK's default credential resolution (`ANTHROPIC_API_KEY`, then an
    `ant auth login` profile). This process neither reads nor writes a key file.
    """
    import anthropic

    from redtape.envs.t1_eligibility import SYSTEM_PROMPT

    client = anthropic.Anthropic()
    allow_unknown = condition == "tool_equipped_unknowns"
    tools = ([tool_schema(allow_unknown=allow_unknown)]
             if condition != "tool_less" else [])

    def agent(task) -> str:
        messages = [{"role": "user", "content": task.data.prompt}]
        for _ in range(max_tool_turns):
            kwargs = dict(
                model=model,
                max_tokens=8_000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                messages=messages,
            )
            if tools:
                kwargs["tools"] = tools
            response = client.messages.create(**kwargs)

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                return json.dumps({"refusal": getattr(detail, "category", "unknown")})

            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                return "\n".join(b.text for b in response.content if b.type == "text")

            results = []
            for block in calls:
                out = calculate(block.input, allow_unknown=allow_unknown)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out)})
            messages.append({"role": "user", "content": results})

        return json.dumps({"error": "tool loop did not terminate"})

    return agent


# ------------------------------------------------------------------ entry points
def run(tasks, agent, *, model: str, split: str, condition: str, out: Path, seed=None,
        extra=None):
    # The seed is REQUIRED downstream by assert_publishable, and no call site was passing
    # it. Deriving it from the tasks themselves means it cannot be forgotten again, and
    # disagreement is a real problem worth stopping for: a results file covering two seeds
    # has no single provenance to record or to redact against.
    if seed is None:
        seeds = {t.data.seed for t in tasks}
        if len(seeds) != 1:
            raise ValueError(
                f"tasks span {len(seeds)} different seeds; a results file must describe "
                f"exactly one split"
            )
        seed = seeds.pop()

    records = [score_one(t, agent(t)) for t in tasks]
    results = build_results(records, model=model, split=split, condition=condition,
                            seed=seed, extra=extra)
    write(results, out, seed=seed)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode",
                    choices=["baselines", "conditions", "live", "oracle", "perfect",
                             "pairdiag"])
    ap.add_argument("--split", default="data/dev/t1.jsonl")
    ap.add_argument("--results", default="results")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    split_name = Path(args.split).stem
    results_dir = Path(args.results)
    tasks = load_tasks(args.split, limit=args.limit, sample=args.sample)
    print(f"{len(tasks)} tasks from {args.split}")

    if args.mode == "baselines":
        for name in BASELINES:
            print(f"\nbaseline: {name}")
            run(tasks, baseline_agent(name), model=f"baseline:{name}", split=split_name,
                condition="tool_less", out=results_dir / f"{split_name}.baseline.{name}.json")

    elif args.mode == "oracle":
        print("\noracle (wiring check, not a reported score)")
        run(tasks, oracle_agent, model="oracle", split=split_name, condition="tool_less",
            out=results_dir / f"{split_name}.oracle.json")

    elif args.mode == "pairdiag":
        # Tests the METRIC, not a model. Both of these should land near 0.5 once ground
        # truth differs in half the pairs; a high score for either means pair_consistency
        # is still not discriminating.
        for name, agent in PAIR_DIAGNOSTICS.items():
            print(f"\npair diagnostic: {name}")
            run(tasks, baseline_agent(name, PAIR_DIAGNOSTICS), model=f"pairdiag:{name}",
                split=split_name, condition="tool_less",
                out=results_dir / f"{split_name}.pairdiag.{name}.json")

    elif args.mode == "perfect":
        print("\nperfect (CEILING check, not a reported score)")
        run(tasks, perfect_agent, model="perfect", split=split_name, condition="tool_less",
            out=results_dir / f"{split_name}.perfect.json")

    elif args.mode == "conditions":
        for condition in CONDITIONS:
            print(f"\ncondition: {condition} (scripted agent)")
            agent = (baseline_agent("rules_only") if condition == "tool_less"
                     else scripted_tool_agent(condition))
            run(tasks, agent, model="scripted", split=split_name, condition=condition,
                out=results_dir / f"{split_name}.scripted.{condition}.json")

    elif args.mode == "live":
        # Auto-load .env so a key placed there is found. This NEVER creates, requests
        # or writes a key - it only reads an environment the human has already set up.
        load_dotenv()
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment.\n"
                  "Export one in this shell before running `live`. This script never "
                  "creates, requests or writes a key.", file=sys.stderr)
            return 2
        tasks = tasks[: (args.limit or 1)]
        print(f"\nLIVE: {args.model}, {len(tasks)} task(s), tool_less")
        run(tasks, live_agent("tool_less", args.model), model=args.model,
            split=split_name, condition="tool_less",
            out=results_dir / f"{split_name}.live.{args.model}.tool_less.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
