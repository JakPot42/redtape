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

from redtape.config import DEV, SPLIT_KINDS, resolve_seed, seed_fingerprint
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
         is_flip=False, unscored_deciding=(), pair_truth_differs=None):
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
        # Build-time metadata, stripped before a task is constructed: whether declaring the
        # status actually moved the scored answer. Recorded so the achieved ratio is a
        # measured property of the split rather than something re-derived downstream.
        "pair_truth_differs": pair_truth_differs,
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

    keys = {}
    for hh, role in ((base, "without_disability"), (with_disability, "with_disability")):
        keys[role] = (hh, compute(hh))

    differs = _scored_differs(keys["without_disability"][1].answer,
                              keys["with_disability"][1].answer)

    out = []
    for role, (hh, key) in keys.items():
        out.append(
            _row(hh, render(hh), key, Determinability.DETERMINATE.value, (), "",
                 pair_id=pair_id, pair_role=role, pair_truth_differs=differs)
        )
    return out


def _scored_differs(a, b, tol: float = 1.0) -> bool:
    """Does ground truth differ across a pair, by the SAME rule the metric applies?

    `eval/metrics.py::_scored_fields` compares snap.eligible, snap.benefit, eitc.amount and
    ctc.amount, with a $1 tolerance on floats. Binning pairs by any other rule would let a
    pair be filed as "differing" while `pair_consistency` scored it as identical, so the
    two definitions are deliberately the same one.
    """
    if a.snap.eligible != b.snap.eligible:
        return True
    for x, y in ((a.snap.benefit, b.snap.benefit),
                 (a.eitc.amount, b.eitc.amount),
                 (a.ctc.amount, b.ctc.amount)):
        if abs(x - y) > tol:
            return True
    return False


def _pair_candidate(seed: int, index: int) -> bool:
    """A pair only isolates the disability channel if the channel can bite.

    p1 must be an adult (the status is meaningless on a child) and the household must have
    shelter costs, since the exemption it triggers is the excess-shelter cap. Households
    failing this are skipped rather than made into pairs whose two members are identical
    by construction.
    """
    hh = generate(seed, index)
    return hh.people[0].age is not None and hh.people[0].age >= 18 and (hh.housing_cost or 0) > 0


def build(seed, n, targets, n_pairs, workers, chunk, max_candidates,
          pair_differ_fraction=0.5, log=print):
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
        #
        # Filled into TWO buckets to an explicit ratio, because the ratio is what makes the
        # metric discriminating and it must not be left to whatever the selector happens to
        # produce. At 40 pairs selected only on "adult p1 with shelter costs", ground truth
        # differed in 4 - so a baseline that never differs banked 36/40 = 0.900 for free and
        # the metric's stated property, that neither always-differ nor never-differ can
        # score well, was false as measured.
        #
        #   differing pairs     test whether the model tracks the mechanism;
        #   non-differing pairs stop it learning "declared disability always changes it".
        #
        # Both are needed, so both are targets.
        pair_buckets: dict[str, list] = {"differ": [], "same": []}
        pair_budget = {
            "differ": round(n_pairs * pair_differ_fraction),
            "same": n_pairs - round(n_pairs * pair_differ_fraction),
        }
        pair_index = index + chunk
        limit = pair_index + max_candidates * 4
        pair_discarded = 0

        def _pairs_short():
            return {k: pair_budget[k] - len(pair_buckets[k])
                    for k in pair_budget if len(pair_buckets[k]) < pair_budget[k]}

        while _pairs_short() and pair_index < limit:
            cands = []
            while len(cands) < chunk and pair_index < limit:
                if _pair_candidate(seed, pair_index):
                    cands.append((seed, pair_index))
                pair_index += 1
            if not cands:
                break
            for pair in pool.imap(build_pair, cands, chunksize=1):
                bucket = "differ" if pair[0]["pair_truth_differs"] else "same"
                if len(pair_buckets[bucket]) < pair_budget[bucket]:
                    pair_buckets[bucket].append(pair)
                else:
                    pair_discarded += 1
            done = sum(len(v) for v in pair_buckets.values())
            log(f"  pairs: {done}/{n_pairs} ({pair_discarded} discarded, "
                f"{time.time() - t0:.0f}s) still short: {_pairs_short()}")

        pair_rows: list[dict] = []
        for bucket in ("differ", "same"):
            for pair in pair_buckets[bucket]:
                pair_rows.extend(pair)

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
        "pair_mix": {
            k: {"target": pair_budget[k], "achieved": len(pair_buckets[k])}
            for k in pair_budget
        },
        "pair_candidates_discarded": pair_discarded,
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
        payload = {k: v for k, v in row.items()
                   if k not in ("unscored_deciding_programs", "pair_truth_differs")}
        task = T1Task(T1Data.model_validate(payload), cfg)
        out.append({**row, "task_hash": task.hash, "task_key": task.key})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=SPLIT_KINDS, default=DEV,
                    help="dev uses the PUBLIC seed; heldout requires REDTAPE_HELDOUT_SEED "
                         "and never falls back")
    ap.add_argument("--seed", type=int, default=None,
                    help="dev only. A held-out build refuses an explicit seed.")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--indeterminate", type=float, default=0.15)
    ap.add_argument("--incomplete", type=float, default=0.12)
    ap.add_argument("--flip", type=float, default=0.08)
    ap.add_argument("--pairs", type=int, default=200,
                    help="matched disability pairs. 40 gave roughly +/-8pp on a headline "
                         "metric; 200 is near +/-3.5pp.")
    ap.add_argument("--pair-differ-fraction", type=float, default=0.5,
                    help="fraction of pairs where ground truth actually differs")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--max-candidates", type=int, default=20_000)
    ap.add_argument("--out", default=None,
                    help="defaults to data/<split>/t1.jsonl")
    args = ap.parse_args()

    # Fail closed. `resolve_seed` raises MissingHeldoutSeed rather than defaulting, so a
    # held-out build with no seed STOPS instead of quietly producing a split with public
    # provenance. See redtape/config.py for why the two seeds have separate variable names.
    seed = resolve_seed(args.split, override=args.seed)

    out = Path(args.out) if args.out else Path(f"data/{args.split}/t1.jsonl")
    # A held-out split must not be writable into the committed dev tree. The directory is
    # the only thing standing between a private artifact and `git add data/dev`.
    expected_dir = Path("data") / args.split
    if out.parent != expected_dir:
        raise SystemExit(
            f"a {args.split} split must be written under {expected_dir}/, not "
            f"{out.parent}/. The output directory is what keeps held-out data out of the "
            f"committed tree."
        )

    targets = {
        "flip": round(args.n * args.flip),
        "indeterminate": round(args.n * args.indeterminate),
        "incomplete_determinate": round(args.n * args.incomplete),
    }
    # A held-out seed is never printed. The fingerprint identifies the run without
    # putting the seed into a terminal, a CI log, or a screenshot.
    shown = seed if args.split == DEV else f"<{args.split}, fp {seed_fingerprint(seed)}>"
    print(f"building n={args.n} split={args.split} seed={shown} "
          f"workers={args.workers}", flush=True)
    print(f"  targets: {targets}, pairs={args.pairs}", flush=True)

    # Warm the engine in the parent so forked workers inherit it instead of each paying
    # the ~2-minute cold import.
    t0 = time.time()
    compute(generate(seed, 0))
    print(f"  engine warm ({time.time() - t0:.0f}s)", flush=True)

    rows, stats = build(seed, args.n, targets, args.pairs, args.workers, args.chunk,
                        args.max_candidates,
                        pair_differ_fraction=args.pair_differ_fraction,
                        log=lambda m: print(m, flush=True))

    # The standing invariant. A scope change makes this fail the build, loudly, rather
    # than silently mis-scoring every task that touches the removed program.
    assert_split_scorable(rows)
    rows = with_hashes(rows)
    if len({r["task_hash"] for r in rows}) != len(rows):
        raise SystemExit("duplicate task hashes; tasks are not distinct")

    path = out
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
        "split": args.split,
        # The dev seed is public and worth recording verbatim; the held-out seed is not.
        # Regeneration reads .env either way, so the manifest never needs the real value.
        "seed": seed if args.split == DEV else None,
        "seed_fingerprint": seed_fingerprint(seed),
        "n": n,
        "targets": {
            "indeterminate": args.indeterminate,
            "incomplete_determinate": args.incomplete,
            "eligibility_flip": args.flip,
            "pairs": args.pairs,
            "pair_differ_fraction": args.pair_differ_fraction,
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
