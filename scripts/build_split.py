"""Build a T1/T1b split at scale. Answer keys are baked here and never recomputed.

    ./.venv/bin/python scripts/build_split.py --n 1200 --out data/dev/t1.jsonl

**Class mix is targeted, not observed.** Candidates are generated in index order, probed,
and accepted into whichever of four buckets they land in until that bucket is full; the
rest are discarded. So the split's class mix is a construction, and the manifest records
how many candidates were consumed to reach it. This is a deliberate trade: a uniform
sample of households would give whatever indeterminate rate the generator happens to
produce, which is not a quantity anyone chose.

Four disjoint buckets:

  * `eligibility_flip`       - the withheld fact flips SNAP *eligibility*, not an amount.
                               These are indeterminate too, but counted separately because
                               they are the scarcest and most valuable class (LIMITS 22).
  * `indeterminate`          - the withheld fact moves an amount past tolerance.
  * `incomplete_determinate` - a fact is withheld and nothing moves. Answer anyway.
  * `determinate`            - complete facts.

Matched disability pairs are carved out of the determinate budget, so they do not disturb
the three T1b targets.

Everything is deterministic in `(seed, index)`: a candidate's role, its withheld fact, its
label and its answer key depend on nothing else, so the same seed gives the same split
whatever the worker count.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

from redtape.generator.households import generate, withhold
from redtape.generator.narratives import render
from redtape.oracle.determinability import probe
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import SCORED_PROGRAMS, Determinability
from redtape.scoring.invariants import assert_split_scorable

# Facts rotated through the withhold stream. `is_higher_ed_student` is NOT here: it gets
# its own stream, because it is the only confirmed eligibility-flipping fact (LIMITS 22)
# and the flip bucket would otherwise never fill.
T1B_FACTS = (
    "housing_cost",
    "p1.employment_income",
    "p1.immigration_status",
    "p1.age",
    "dependent_care_cost",
)
FLIP_FACT = "p1.is_higher_ed_student"

# Candidate roles by index residue. The flip stream gets a large share because the
# eligibility-flip rate is the binding constraint (see scripts/probe_flip_rate.py).
ROLE_FLIP, ROLE_WITHHOLD, ROLE_DETERMINATE = "flip", "withhold", "determinate"
_ROLE_CYCLE = (
    ROLE_FLIP, ROLE_WITHHOLD, ROLE_DETERMINATE, ROLE_WITHHOLD,
    ROLE_FLIP, ROLE_WITHHOLD, ROLE_DETERMINATE, ROLE_DETERMINATE,
)

# The disability fact used for matched pairs. A *status*, not a benefit: declaring
# `is_permanently_disabled_veteran` establishes elderly-or-disabled for SNAP (7 CFR 271.2)
# WITHOUT adding income, so the pair differs on exactly one channel - the excess shelter
# cap exemption. Declaring an SSDI amount would move both the income and the exemption at
# once, and the pair would no longer isolate anything (CLAUDE.md, "How disability actually
# works for SNAP").
PAIR_STATUS = "is_permanently_disabled_veteran"
PAIR_INDEX_OFFSET = 1_000_000


def _snap_eligibility_flips(label) -> bool:
    """Did SNAP *eligibility* move across the sweep, as opposed to only the amount?"""
    snap = next((v for v in label.per_program if v.program == "snap"), None)
    if snap is None:
        return False
    return len({o.split(" benefit=")[0] for o in snap.observed}) > 1


def _row(hh, narrative, key, label_value, deciding, withheld, *, pair_id="", pair_role="",
         is_flip=False, unscored_deciding=()):
    return {
        "prompt": narrative,
        "name": hh.household_id,
        "household_id": hh.household_id,
        "seed": hh.seed,
        "index": hh.index,
        "answer_key_json": key.answer.model_dump_json(),
        "determinability": label_value,
        # Only scored programs, enforced by redtape/scoring/invariants.py. A task must
        # never expect an abstention on a program the scorer does not score.
        "deciding_programs": [p for p in deciding if p in SCORED_PROGRAMS],
        "withheld_fact": withheld,
        "pair_id": pair_id,
        "pair_role": pair_role,
        "is_eligibility_flip": is_flip,
        "engine_version": key.engine_version,
        "python_version": key.python_version,
        # Audit only. A fact that decides Medicaid alone leaves the task determinate as
        # far as v0 can score it; recorded so the decision is visible, never used to label.
        "unscored_deciding_programs": list(unscored_deciding),
    }


def classify(args) -> dict:
    """Worker: turn one candidate index into a fully-labelled row. Deterministic."""
    seed, index = args
    role = _ROLE_CYCLE[index % len(_ROLE_CYCLE)]
    hh = generate(seed, index)

    if role == ROLE_DETERMINATE:
        key = compute(hh)
        return _row(hh, render(hh), key, Determinability.DETERMINATE.value, (), "")

    fact = FLIP_FACT if role == ROLE_FLIP else T1B_FACTS[index % len(T1B_FACTS)]
    holed = withhold(hh, fact)
    label = probe(holed, fact)
    # The key is computed with the fact at its TRUE value: ground truth is what the
    # household's real circumstances imply, not what the narrative happens to state.
    key = compute(hh)
    return _row(
        hh, render(holed), key, label.label.value, label.deciding_programs, fact,
        is_flip=_snap_eligibility_flips(label),
        unscored_deciding=label.unscored_deciding_programs,
    )


def build_pair(args) -> list[dict]:
    """Worker: one matched disability pair from a base household, both members."""
    seed, index = args
    base = generate(seed, index)
    pair_id = f"pair-{seed}-{index:05d}"

    p1 = base.people[0].model_copy(update={"declared_statuses": (PAIR_STATUS,)})
    with_disability = base.model_copy(
        update={
            "household_id": f"hh-{seed}-{PAIR_INDEX_OFFSET + index:05d}",
            "index": PAIR_INDEX_OFFSET + index,
            "people": (p1,) + tuple(base.people[1:]),
        }
    )

    out = []
    for hh, role in ((base, "without_disability"), (with_disability, "with_disability")):
        key = compute(hh)
        out.append(
            _row(hh, render(hh), key, Determinability.DETERMINATE.value, (), "",
                 pair_id=pair_id, pair_role=role)
        )
    return out


def _pair_candidate(seed: int, index: int) -> bool:
    """A pair only isolates the disability channel if the channel can bite.

    p1 must be an adult (the status is meaningless on a child) and the household must have
    shelter costs, since the exemption it triggers is the excess-shelter cap. Households
    failing this are skipped rather than made into pairs whose two members are identical
    by construction.
    """
    hh = generate(seed, index)
    return hh.people[0].age is not None and hh.people[0].age >= 18 and (hh.housing_cost or 0) > 0


def build(seed, n, targets, n_pairs, workers, chunk, max_candidates, log=print):
    """Fill every bucket, then carve pairs out of the determinate budget."""
    n_pair_tasks = n_pairs * 2
    budget = {
        "eligibility_flip": targets["flip"],
        "indeterminate": targets["indeterminate"],
        "incomplete_determinate": targets["incomplete_determinate"],
        "determinate": n - targets["flip"] - targets["indeterminate"]
        - targets["incomplete_determinate"] - n_pair_tasks,
    }
    if budget["determinate"] < 0:
        raise SystemExit("targets plus pairs exceed n")

    accepted = {k: [] for k in budget}
    consumed = discarded = 0
    ctx = mp.get_context("fork")
    t0 = time.time()

    # Which buckets a candidate of each role can possibly fill. Once none of a role's
    # buckets is short, its candidates are skipped rather than computed and discarded -
    # the eligibility-flip bucket needs roughly twenty candidates per acceptance, so
    # computing full sweeps for buckets that are already full would dominate the run.
    ROLE_FILLS = {
        ROLE_FLIP: ("eligibility_flip", "indeterminate", "incomplete_determinate"),
        ROLE_WITHHOLD: ("indeterminate", "incomplete_determinate"),
        ROLE_DETERMINATE: ("determinate",),
    }

    def _useful(i: int) -> bool:
        role = _ROLE_CYCLE[i % len(_ROLE_CYCLE)]
        return any(len(accepted[b]) < budget[b] for b in ROLE_FILLS[role])

    with ctx.Pool(workers) as pool:
        index = 0
        while any(len(accepted[k]) < budget[k] for k in budget) and consumed < max_candidates:
            batch = []
            while len(batch) < chunk and index < max_candidates * 4:
                if _useful(index):
                    batch.append((seed, index))
                index += 1
            if not batch:
                break
            for row in pool.imap(classify, batch, chunksize=1):
                consumed += 1
                bucket = (
                    "eligibility_flip" if row["is_eligibility_flip"]
                    else row["determinability"]
                )
                if bucket in accepted and len(accepted[bucket]) < budget[bucket]:
                    accepted[bucket].append(row)
                else:
                    discarded += 1
            done = sum(len(v) for v in accepted.values())
            want = sum(budget.values())
            short = {k: budget[k] - len(accepted[k]) for k in budget if len(accepted[k]) < budget[k]}
            log(f"  {consumed:>5} candidates -> {done:>4}/{want} accepted "
                f"({discarded} discarded, {time.time() - t0:.0f}s) still short: {short}")

        # Pairs, from indices past the candidate stream so no household is reused.
        pair_rows: list[dict] = []
        pair_index = index + chunk
        limit = pair_index + max_candidates
        while len(pair_rows) < n_pair_tasks and pair_index < limit:
            wanted = (n_pair_tasks - len(pair_rows)) // 2
            cands = []
            while len(cands) < wanted and pair_index < limit:
                if _pair_candidate(seed, pair_index):
                    cands.append((seed, pair_index))
                pair_index += 1
            if not cands:
                break
            for pair in pool.imap(build_pair, cands, chunksize=1):
                pair_rows.extend(pair)
            log(f"  pairs: {len(pair_rows) // 2}/{n_pairs} ({time.time() - t0:.0f}s)")

    rows = []
    for k in ("determinate", "indeterminate", "incomplete_determinate", "eligibility_flip"):
        rows.extend(accepted[k])
    rows.extend(pair_rows)
    rows.sort(key=lambda r: (r["index"], r["household_id"]))

    stats = {
        "candidates_consumed": consumed,
        "candidates_discarded": discarded,
        "seconds": round(time.time() - t0, 1),
        "buckets": {k: {"target": budget[k], "achieved": len(accepted[k])} for k in budget},
        "pairs": len(pair_rows) // 2,
    }
    return rows, stats


def with_hashes(rows):
    """Attach `Task.hash` to every row. The library's identity, not one we invented.

    This is the contamination and rotation story: a hash proves a held-out task is the one
    that was scored, and that a rotation changed the task set, without publishing a single
    held-out answer.
    """
    from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig

    cfg = T1TaskConfig()
    out = []
    for row in rows:
        payload = {k: v for k, v in row.items() if k != "unscored_deciding_programs"}
        task = T1Task(T1Data.model_validate(payload), cfg)
        out.append({**row, "task_hash": task.hash, "task_key": task.key})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None,
                    help="omit to read REDTAPE_SEED, then fall back to the public seed")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--indeterminate", type=float, default=0.15)
    ap.add_argument("--incomplete", type=float, default=0.12)
    ap.add_argument("--flip", type=float, default=0.08)
    ap.add_argument("--pairs", type=int, default=40)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--max-candidates", type=int, default=20_000)
    ap.add_argument("--out", default="data/dev/t1.jsonl")
    args = ap.parse_args()

    seed = args.seed
    if seed is None:
        env = os.environ.get("REDTAPE_SEED")
        seed = int(env) if env else 20260828

    targets = {
        "flip": round(args.n * args.flip),
        "indeterminate": round(args.n * args.indeterminate),
        "incomplete_determinate": round(args.n * args.incomplete),
    }
    print(f"building n={args.n} seed={seed} workers={args.workers}", flush=True)
    print(f"  targets: {targets}, pairs={args.pairs}", flush=True)

    # Warm the engine in the parent so forked workers inherit it instead of each paying
    # the ~2-minute cold import.
    t0 = time.time()
    compute(generate(seed, 0))
    print(f"  engine warm ({time.time() - t0:.0f}s)", flush=True)

    rows, stats = build(seed, args.n, targets, args.pairs, args.workers, args.chunk,
                        args.max_candidates,
                        log=lambda m: print(m, flush=True))

    # The standing invariant. A scope change makes this fail the build, loudly, rather
    # than silently mis-scoring every task that touches the removed program.
    assert_split_scorable(rows)
    rows = with_hashes(rows)
    if len({r["task_hash"] for r in rows}) != len(rows):
        raise SystemExit("duplicate task hashes; tasks are not distinct")

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    n = len(rows)
    mix = {}
    for r in rows:
        k = "eligibility_flip" if r["is_eligibility_flip"] else r["determinability"]
        mix[k] = mix.get(k, 0) + 1
    manifest = {
        "seed": seed,
        "n": n,
        "targets": {
            "indeterminate": args.indeterminate,
            "incomplete_determinate": args.incomplete,
            "eligibility_flip": args.flip,
            "pairs": args.pairs,
        },
        "achieved": {k: {"n": v, "fraction": round(v / n, 4)} for k, v in sorted(mix.items())},
        "generation": stats,
        "policyengine_us": version("policyengine-us"),
        "python": platform.python_version(),
        "task_hashes": [r["task_hash"] for r in rows],
    }
    manifest_path = path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nwrote {n} tasks -> {path}")
    print(f"  manifest -> {manifest_path}")
    print(f"  engine {manifest['policyengine_us']}  python {manifest['python']}")
    for k, v in sorted(mix.items()):
        print(f"  {k:<26} {v:>5}  ({100 * v / n:.1f}%)")
    print(f"  candidates consumed {stats['candidates_consumed']}, "
          f"discarded {stats['candidates_discarded']}, {stats['seconds']}s")


if __name__ == "__main__":
    sys.exit(main())
