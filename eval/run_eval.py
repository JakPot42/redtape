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
from eval.cache import (
    cache_key,
    cost_usd,
    get as cache_get,
    partition,
    put as cache_put,
)
from eval.budget import Budget, BudgetExhausted, NoBudget
from eval.providers import credential_env, get_model, is_unbilled_error, make_provider
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
            "household_type": r.household_type,
            "pays_heating_cooling": r.pays_heating_cooling,
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
                    "weekly_hours": (p["weekly_hours"]
                                     if p["weekly_hours"] is not None else 0.0),
                    "immigration_status": p["immigration_status"] or "CITIZEN",
                    "is_higher_ed_student": bool(p["is_higher_ed_student"]),
                    "student_full_time": p["student_full_time"],
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
        # The identifier the abstention names is the fact actually marked unknown. It was
        # hard-coded "housing_cost" for every abstention, which exact fact matching
        # (2026-09-19) would score as a wrong-fact abstention on four of the five facts.
        marked = "housing_cost"
        if allow_unknown:
            if r.any_age_withheld:
                payload["people"][0]["age"] = UNKNOWN
                marked = "p1.age"
            elif r.any_income_withheld:
                payload["people"][0]["employment_income"] = UNKNOWN
                marked = "p1.employment_income"
            elif r.any_status_withheld:
                payload["people"][0]["immigration_status"] = UNKNOWN
                marked = "p1.immigration_status"

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
            "cannot_determine": [{"program": p, "missing_fact": marked}
                                 for p in abstain],
        }
        return json.dumps(answer)

    return agent


# ------------------------------------------------------------------ the live client
class _Ledger:
    """Running token and cost total for a live run.

    Cached responses are counted separately from billed ones. Reporting a single blended
    figure would make a resumed run look cheaper than it was and a fully-cached re-score
    look free, and neither is the number anyone actually wants: the question is usually
    "what did this run cost me" AND "what would it cost from cold".

    Also tallies how each response ENDED (`stops`) and which upstream served it
    (`served_by`). A response that hit the token limit is not the same event as a response
    that answered badly, and a results file that cannot tell them apart would report a
    harness budget as a model failure.
    """

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.billed = {"n": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        self.cached = {"n": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        self.stops: dict[str, int] = {}
        self.served_by: dict[str, int] = {}
        # Set False after a pre-warm pass. Pre-warm has already counted every response once
        # (billed or hit); the scoring pass then reads each one AGAIN from the cache, and
        # counting that second read doubled every hit. Committed evidence:
        # results/t1_live300.live.tool_less.json records 600 cache hits for 300 tasks.
        # Billed requests in the scoring pass (retries) are still counted.
        self.count_hits = True

    def record(self, model: str, usage: dict, *, cached: bool, stop: str | None = None,
               served_by: str | None = None, reported_usd: float | None = None):
        if cached and not self.count_hits:
            return
        # The larger of our computation and the provider's own figure, so an error in our
        # price table can only make the ledger (and the cap) more conservative.
        usd = max(cost_usd(model, usage), reported_usd or 0.0)
        with self._lock:
            bucket = self.cached if cached else self.billed
            bucket["n"] += 1
            bucket["input_tokens"] += usage.get("input_tokens", 0)
            bucket["output_tokens"] += usage.get("output_tokens", 0)
            bucket["usd"] += usd
            s = stop or "unrecorded"
            self.stops[s] = self.stops.get(s, 0) + 1
            if served_by:
                self.served_by[served_by] = self.served_by.get(served_by, 0) + 1

    def summary(self) -> dict:
        return {
            "billed": dict(self.billed),
            "cached": dict(self.cached),
            "usd_actually_spent": round(self.billed["usd"], 4),
            "usd_if_uncached": round(self.billed["usd"] + self.cached["usd"], 4),
            "stop_reasons": dict(self.stops),
            "served_by": dict(self.served_by),
        }

    def line(self) -> str:
        b, c = self.billed, self.cached
        return (f"billed {b['n']:>4} req  {b['input_tokens']:>8,} in  "
                f"{b['output_tokens']:>9,} out  ${b['usd']:.4f}"
                f"   |  cache hits {c['n']:>4}  (${c['usd']:.4f} avoided)")


LEDGER = _Ledger()


def request_identity(condition: str, model: str = MODEL, system_prompt: str | None = None):
    """(cfg, system, tools) - everything that goes into a cache key except the task prompt.

    ONE function, used by both `live_agent` and `cached_subset`. They used to build the
    params dict independently, as two literal copies; a change to one would have made the
    cached-only scorer look for keys the live agent never wrote, and report a fully paid run
    as "0 cached".
    """
    from redtape.envs.t1_eligibility import SYSTEM_PROMPT as _DEFAULT_PROMPT

    cfg = get_model(model)
    system = system_prompt if system_prompt is not None else _DEFAULT_PROMPT
    allow_unknown = condition == "tool_equipped_unknowns"
    tools = ([tool_schema(allow_unknown=allow_unknown)]
             if condition != "tool_less" else [])
    return cfg, system, tools


def task_cache_key(task, cfg, system, tools) -> str:
    return cache_key(model=cfg.cache_model, system=system, prompt=task.data.prompt,
                     tools=tools, params=cfg.params)


def live_agent(condition: str, model: str = MODEL, max_tool_turns: int = 6,
               system_prompt: str | None = None, budget: Budget | None = None):
    """A real model client, for any registered model. Credentials come from the environment.

    The provider is built LAZILY, on the first cache miss. Re-scoring a fully cached run
    therefore needs no credential and constructs no client - which is what makes it safe to
    re-score paid runs freely.

    Every billed request goes through `budget`: its worst case is reserved before sending,
    and a request that could cross the cap is never sent (`eval/budget.py`). With no budget,
    a cache hit is served and a cache miss raises `NoBudget` - there is no uncapped path.
    """
    # An override exists so a prompt variant can be A/B'd against the shipped prompt on the
    # same tasks. It is hashed into the cache key like everything else, so the two arms
    # cannot collide and arm A costs nothing when its responses are already cached.
    cfg, SYSTEM_PROMPT, tools = request_identity(condition, model, system_prompt)
    allow_unknown = condition == "tool_equipped_unknowns"
    state = {"provider": None}

    def provider():
        if state["provider"] is None:
            state["provider"] = make_provider(cfg)
        return state["provider"]

    def agent(task) -> str:
        key = task_cache_key(task, cfg, SYSTEM_PROMPT, tools)
        split = partition(task.data.seed)
        hit = cache_get(key, split)
        if hit is not None:
            LEDGER.record(cfg.key, hit["usage"], cached=True, stop=hit.get("stop"),
                          served_by=hit.get("served_by"),
                          reported_usd=hit.get("reported_cost_usd"))
            return hit["reply"]

        if budget is None:
            raise NoBudget(f"cache miss for {cfg.key} with no --max-usd; refusing to bill")

        p = provider()
        messages = [p.user_message(task.data.prompt)]
        usage_total = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        meta = {"stop": None, "raw_stop": None, "served_by": None, "reported_cost_usd": None}

        def _finish(reply: str) -> str:
            cache_put(key, {"reply": reply, "usage": usage_total, "model": cfg.cache_model,
                            "condition": condition, "task_hash": task.hash, **meta}, split)
            LEDGER.record(cfg.key, usage_total, cached=False, stop=meta["stop"],
                          served_by=meta["served_by"],
                          reported_usd=meta["reported_cost_usd"])
            return reply

        for _ in range(max_tool_turns):
            request_text = SYSTEM_PROMPT + json.dumps(messages, default=str)
            reserved = budget.reserve(request_text)     # raises BudgetExhausted; nothing sent
            try:
                turn = p.complete(SYSTEM_PROMPT, messages, tools)
            except BaseException as exc:
                # A refusal (401/403/429, or a connection that never opened) generated
                # nothing and billed nothing, so its reservation is released. Anything else
                # keeps the conservative charge, because billing is unknown.
                if is_unbilled_error(exc):
                    budget.release_unbilled(reserved)
                else:
                    budget.charge_failed(reserved)
                raise
            actual = max(cost_usd(cfg.key, turn.usage), turn.reported_cost_usd or 0.0)
            budget.settle(reserved, actual)

            for k in usage_total:
                usage_total[k] += turn.usage.get(k, 0) or 0
            if turn.reported_cost_usd is not None:
                meta["reported_cost_usd"] = (meta["reported_cost_usd"] or 0.0) + turn.reported_cost_usd
            meta.update(stop=turn.stop, raw_stop=turn.raw_stop,
                        served_by=turn.served_by or meta["served_by"])

            if turn.stop == "refusal":
                return _finish(json.dumps({"refusal": turn.refusal or "unknown"}))

            if not turn.tool_calls:
                return _finish(turn.text)

            messages.append(turn.assistant_message)
            messages.append(p.tool_results_message(
                [(c.id, calculate(c.input, allow_unknown=allow_unknown))
                 for c in turn.tool_calls]))

        meta["stop"] = "tool_loop_exhausted"
        return _finish(json.dumps({"error": "tool loop did not terminate"}))

    return agent


# ------------------------------------------------------------------ entry points
def cached_subset(tasks, model: str, condition: str = "tool_less"):
    """The tasks whose response is already cached, and - for pairs - only whole ones.

    A partially-fetched run cannot be scored as if it were complete. `pair_consistency`
    counts a pair with a missing member as INCONSISTENT, so scoring a 94%-complete run
    would report the missing 6% as model failures and depress the pair headline by the
    fetch shortfall rather than by anything the model did. Dropping incomplete pairs keeps
    the metric honest about what it measured; the reduced `n_pairs` is what tells the
    reader the run was partial.
    """
    cfg, system, tools = request_identity(condition, model)

    def cached(t):
        return cache_get(task_cache_key(t, cfg, system, tools),
                         partition(t.data.seed)) is not None

    kept = [t for t in tasks if cached(t)]

    # Drop any pair that lost a member, or the metric reports a fetch gap as a model failure.
    by_pair = {}
    for t in kept:
        if t.data.pair_id:
            by_pair.setdefault(t.data.pair_id, []).append(t)
    broken = {pid for pid, members in by_pair.items() if len(members) != 2}
    kept = [t for t in kept if not (t.data.pair_id and t.data.pair_id in broken)]

    dropped = len(tasks) - len(kept)
    print(f"  cached-only: scoring {len(kept)}/{len(tasks)} tasks "
          f"({dropped} not cached, incl. {len(broken)} incomplete pairs dropped)")
    return kept



def prewarm(tasks, agent, *, workers: int = 8, log_every: int = 25) -> None:
    """Fetch every response concurrently into the cache, then return.

    Scoring stays SEQUENTIAL afterwards and reads only cache hits, so ordering, the ledger
    and the results file are identical to a serial run - concurrency touches the network
    and nothing else. That matters: a parallel scoring loop would interleave `trace.info`
    writes and reorder `per_task`, which would change published numbers for a reason that
    has nothing to do with the model.

    Failures are swallowed here on purpose. An exception during pre-warm leaves that task
    simply uncached, and the sequential pass that follows retries it and raises properly
    with the task in hand. Losing the whole run at task 900 because one request 500'd would
    throw away everything already paid for.
    """
    import concurrent.futures as cf
    import threading

    done = threading.Lock()
    state = {"n": 0, "errors": 0}
    total = len(tasks)

    def one(task):
        try:
            agent(task)
        except BudgetExhausted:
            # Not an error, and NOT swallowed into the error count: the cap refused to send
            # this request. Later tasks still run, but only cache hits can succeed - every
            # miss is refused before anything is sent.
            with done:
                state["refused"] = state.get("refused", 0) + 1
        except Exception as exc:
            with done:
                state["errors"] += 1
                if state["errors"] <= 3:
                    print(f"  prewarm error: {type(exc).__name__}: {str(exc)[:300]}",
                          flush=True)
        finally:
            with done:
                state["n"] += 1
                n = state["n"]
            if log_every and (n % log_every == 0 or n == total):
                print(f"  prewarm [{n:>5}/{total}] {LEDGER.line()}"
                      f"{'  errors ' + str(state['errors']) if state['errors'] else ''}",
                      flush=True)

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, tasks))

    if state["errors"]:
        print(f"  prewarm finished with {state['errors']} error(s); "
              f"the scoring pass will retry those", flush=True)
    # Every task's response has now been counted once, billed or hit. The scoring pass that
    # follows re-reads each from the cache; counting those reads doubled every hit.
    LEDGER.count_hits = False
    if state.get("refused"):
        print(f"  !! BUDGET CAP REACHED: {state['refused']} request(s) refused before "
              f"sending", flush=True)


def run(tasks, agent, *, model: str, split: str, condition: str, out: Path, seed=None,
        extra=None, progress_every: int = 0):
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

    # Deliberately NOT reset here. `prewarm` does the actual buying, and resetting at the
    # top of run() discarded its totals - so a prewarmed run reported the cost of its
    # scoring pass, which is always zero because every response is a cache hit by then.
    # The results file's `usage` block was understating spend for exactly that reason.
    # Callers reset before the work they want measured.
    records = []
    for i, t in enumerate(tasks, 1):
        records.append(score_one(t, agent(t)))
        if progress_every and (i % progress_every == 0 or i == len(tasks)):
            print(f"  [{i:>5}/{len(tasks)}] {LEDGER.line()}", flush=True)
    extra = dict(extra or {})
    if LEDGER.billed["n"] or LEDGER.cached["n"]:
        extra["usage"] = LEDGER.summary()
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
    ap.add_argument("--progress-every", type=int, default=25,
                    help="print a running cost line every N tasks")
    ap.add_argument("--cached-only", action="store_true",
                    help="score only tasks already in the cache, dropping pairs that "
                         "lost a member; for reporting a partially-fetched run")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent API requests during cache pre-warm; scoring is "
                         "always sequential")
    ap.add_argument("--max-usd", type=float, default=None,
                    help="HARD spending cap for a live run. Required whenever any request "
                         "would be billed; no default. Enforced before every request.")
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
        cfg = get_model(args.model)
        if args.limit:
            tasks = tasks[: args.limit]
        if args.cached_only:
            tasks = cached_subset(tasks, args.model)

        # How many requests this run would actually bill, BEFORE anything else. A fully
        # cached run needs neither a credential nor a cap; anything else needs both.
        _, system, tools = request_identity("tool_less", args.model)
        misses = [t for t in tasks
                  if cache_get(task_cache_key(t, cfg, system, tools),
                               partition(t.data.seed)) is None]
        print(f"\nLIVE: {cfg.key} via {cfg.provider} ({cfg.api_model}), "
              f"{len(tasks)} task(s), tool_less, {len(misses)} uncached")

        budget = None
        if misses:
            probe = Budget(1e9, price_in=cfg.price_in, price_out=cfg.price_out,
                           max_output_tokens=cfg.max_output_tokens)
            worst = sum(probe.worst_case(system + t.data.prompt) for t in misses)
            print(f"  worst case if every uncached request used all "
                  f"{cfg.max_output_tokens:,} output tokens: ${worst:.2f}")
            if args.max_usd is None:
                print("  refusing to run: a paid run needs an explicit --max-usd cap "
                      "(CLAUDE.md, 'Paid runs'). There is no default.", file=sys.stderr)
                return 2
            # Auto-load .env so a key placed there is found. This NEVER creates, requests
            # or writes a key - it only reads an environment the human has already set up.
            load_dotenv()
            env = credential_env(cfg)
            if not any(os.environ.get(k) for k in env):
                print(f"no {' / '.join(env)} in the environment. Export one before "
                      f"running `live`. This script never creates, requests or writes a "
                      f"key.", file=sys.stderr)
                return 2
            budget = Budget(args.max_usd, price_in=cfg.price_in, price_out=cfg.price_out,
                            max_output_tokens=cfg.max_output_tokens)
            print(f"  HARD CAP ${budget.cap:.2f}, enforced before every request")

        agent = live_agent("tool_less", args.model, budget=budget)
        LEDGER.reset()
        # Always pre-warm, even with one worker: it is where the cap can stop the run
        # cleanly, leaving whatever was bought in the cache.
        prewarm(tasks, agent, workers=max(1, args.workers), log_every=args.progress_every)
        if budget is not None and budget.exhausted:
            print(f"  STOPPED AT CAP ({budget.line()}); scoring only what was fetched")
            tasks = cached_subset(tasks, args.model)

        extra = {"provider": {"key": cfg.key, "provider": cfg.provider,
                              "api_model": cfg.api_model, "params": cfg.params}}
        if budget is not None:
            extra["budget"] = {"cap_usd": budget.cap, "spent_usd": round(budget.spent, 4),
                               "refused_requests": budget.refused,
                               "provider_refusals_unbilled": budget.refused_unbilled}
        try:
            run(tasks, agent, model=cfg.key,
                split=split_name, condition="tool_less",
                out=results_dir / f"{split_name}.live.{cfg.key}.tool_less.json",
                progress_every=args.progress_every, extra=extra)
        except BudgetExhausted:
            print(f"  STOPPED AT CAP during scoring retries ({budget.line()}); "
                  f"scoring only what was fetched")
            run(cached_subset(tasks, args.model), agent, model=cfg.key,
                split=split_name, condition="tool_less",
                out=results_dir / f"{split_name}.live.{cfg.key}.tool_less.json",
                progress_every=args.progress_every, extra=extra)
        print("\n  " + LEDGER.line())
        if budget is not None:
            print(f"  {budget.line()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
