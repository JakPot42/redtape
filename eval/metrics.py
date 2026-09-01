"""Headline metrics and the results-file shape.

**Three headline metrics, reported separately. The composite is not the headline.**

A single blended figure always invites "what's in it", and the answer - a weighted sum
over four components with a gate in front - is unsatisfying however carefully it was
chosen. Worse, a composite lets a weight change move the published number without any
model changing. So the composite is reported, clearly labelled secondary, and these three
are the numbers on the leaderboard:

  (a) `t1_exact_match_determinate` - all-correct rate on DETERMINATE tasks. What the
      prior art (PolicyBench, TaxCalcBench) measures, so it is our comparability anchor.
  (b) `t1b_abstention_accuracy`    - correct abstention decisions on T1b tasks, both
      classes together: abstaining when the fact decides, and answering when it does not.
      This is the novel claim, and it is the one number that must not be diluted.
  (c) `pair_consistency`           - on matched disability pairs, whether the model's two
      answers differ exactly where ground truth differs.

All three are written as **top-level fields** in every results file, each carrying its own
`n` and its definition string, so nobody has to derive them and no two readers can derive
them differently.

Why (c) is a separate headline rather than part of (a): a model can reach a decent
exact-match rate while being blind to the deciding fact, if the fact rarely moves the
answer far. Pairs isolate that. A pair is consistent only when the model's *difference
pattern* matches ground truth's - so both "gave identical answers to a pair that differs"
and "invented a difference in a pair that does not" are failures. Neither always-differ
nor never-differ can score well.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version

from redtape.schemas import SCORED_PROGRAMS, Determinability, T1Answer

TOLERANCE = 1.0

HEADLINE_DEFINITIONS = {
    "t1_exact_match_determinate": (
        "Fraction of DETERMINATE tasks answered all-correct: every scored amount within "
        "tolerance, SNAP eligibility exact, every period label exact. Gate failures "
        "(unparseable or degenerate responses) count as incorrect, never as excluded."
    ),
    "t1b_abstention_accuracy": (
        "Fraction of T1b tasks (indeterminate + incomplete-determinate) whose abstention "
        "decision is fully correct: abstaining on exactly the deciding scored programs "
        "when the withheld fact decides one, and not abstaining when it decides none. "
        "Gate failures count as incorrect."
    ),
    "pair_consistency": (
        "Fraction of matched disability pairs where the model's answers differ on exactly "
        "the scored fields on which ground truth differs. Both members must be present "
        "and pass the gate; a pair with a missing or gated member counts as inconsistent."
    ),
}


@dataclass
class TaskRecord:
    """One scored task. Everything the headlines need, and nothing derived."""

    task_hash: str
    household_id: str
    determinability: str
    gate_passed: bool
    exact_match: bool
    abstention_correct: bool
    parse_failure: str = "none"
    scorer_error: str = ""
    composite: float = 0.0
    rewards: dict = field(default_factory=dict)
    pair_id: str = ""
    pair_role: str = ""
    is_eligibility_flip: bool = False
    withheld_fact: str = ""
    answer: T1Answer | None = None
    answer_key: T1Answer | None = None


def _scored_fields(a: T1Answer) -> dict:
    """The comparable content of an answer, restricted to what v0 scores."""
    out = {
        "snap.eligible": a.snap.eligible,
        "snap.benefit": a.snap.benefit,
        "eitc.amount": a.eitc.amount,
        "ctc.amount": a.ctc.amount,
    }
    claimed = {c.program for c in a.cannot_determine} & set(SCORED_PROGRAMS)
    out["abstained"] = tuple(sorted(claimed))
    return out


def _differs(x, y, tol: float = TOLERANCE) -> bool:
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > tol
    return x != y


def _difference_pattern(a: T1Answer, b: T1Answer, tol: float = TOLERANCE) -> tuple[str, ...]:
    """Which scored fields differ between two answers. The unit of pair consistency."""
    fa, fb = _scored_fields(a), _scored_fields(b)
    return tuple(sorted(k for k in fa if _differs(fa[k], fb[k], tol)))


def _mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def headline_exact_match(records) -> dict:
    hits = [r.exact_match and r.gate_passed for r in records
            if r.determinability == Determinability.DETERMINATE.value]
    return {
        "value": _mean(1.0 if h else 0.0 for h in hits),
        "n": len(hits),
        "definition": HEADLINE_DEFINITIONS["t1_exact_match_determinate"],
    }


def headline_abstention(records) -> dict:
    t1b = [r for r in records
           if r.determinability != Determinability.DETERMINATE.value]
    hits = [r.abstention_correct and r.gate_passed for r in t1b]
    by_class = {}
    for cls in (Determinability.INDETERMINATE, Determinability.INCOMPLETE_DETERMINATE):
        sub = [r for r in t1b if r.determinability == cls.value]
        by_class[cls.value] = {
            "value": _mean(1.0 if (r.abstention_correct and r.gate_passed) else 0.0 for r in sub),
            "n": len(sub),
        }
    return {
        "value": _mean(1.0 if h else 0.0 for h in hits),
        "n": len(hits),
        # Split by class as well as pooled: a model that always abstains scores 1.0 on the
        # indeterminate half and 0.0 on the incomplete-determinate half, and the pooled
        # number alone would hide that.
        "by_class": by_class,
        "definition": HEADLINE_DEFINITIONS["t1b_abstention_accuracy"],
    }


def headline_pair_consistency(records, tol: float = TOLERANCE) -> dict:
    pairs: dict[str, list[TaskRecord]] = {}
    for r in records:
        if r.pair_id:
            pairs.setdefault(r.pair_id, []).append(r)

    consistent = 0
    incomplete = 0
    truth_differs = 0
    both_exact = 0
    details = []
    for pid, members in sorted(pairs.items()):
        if len(members) != 2 or any(m.answer is None or m.answer_key is None for m in members):
            incomplete += 1
            details.append({"pair_id": pid, "verdict": "incomplete", "n_members": len(members)})
            continue
        a, b = sorted(members, key=lambda m: m.pair_role)
        truth_pattern = _difference_pattern(a.answer_key, b.answer_key, tol)
        if truth_pattern:
            truth_differs += 1
        if not (a.gate_passed and b.gate_passed):
            details.append({"pair_id": pid, "verdict": "gated", "truth_pattern": truth_pattern})
            continue
        model_pattern = _difference_pattern(a.answer, b.answer, tol)
        ok = model_pattern == truth_pattern
        consistent += ok
        both_exact += a.exact_match and b.exact_match
        details.append({
            "pair_id": pid,
            "verdict": "consistent" if ok else "inconsistent",
            "truth_pattern": truth_pattern,
            "model_pattern": model_pattern,
        })

    n = len(pairs)
    return {
        "value": (consistent / n) if n else None,
        "n_pairs": n,
        "n_pairs_where_truth_differs": truth_differs,
        "n_pairs_incomplete": incomplete,
        "both_members_exact_match": (both_exact / n) if n else None,
        "definition": HEADLINE_DEFINITIONS["pair_consistency"],
        "pairs": details,
    }


def diagnostics(records) -> dict:
    """Everything that must be checked before a run is publishable."""
    n = len(records)
    classes: dict[str, int] = {}
    for r in records:
        classes[r.determinability] = classes.get(r.determinability, 0) + 1
    parse = {}
    for r in records:
        if r.parse_failure != "none":
            parse[r.parse_failure] = parse.get(r.parse_failure, 0) + 1
    return {
        "n_tasks": n,
        "scorer_error_count": sum(1 for r in records if r.scorer_error),
        "publishable": all(not r.scorer_error for r in records),
        "gate_pass_rate": _mean(1.0 if r.gate_passed else 0.0 for r in records),
        "parse_failures": parse,
        "class_mix": {k: {"n": v, "fraction": v / n if n else None} for k, v in sorted(classes.items())},
        "n_eligibility_flip": sum(1 for r in records if r.is_eligibility_flip),
    }


def build_results(records, *, model: str, split: str, condition: str = "tool_less",
                  seed: int | None = None, extra: dict | None = None) -> dict:
    """Assemble a results file. The three headlines are TOP-LEVEL fields, by design."""
    composites = [r.composite for r in records]
    return {
        "schema_version": "2",
        # ---- the three headline metrics, top level, each with its own n -------------
        "t1_exact_match_determinate": headline_exact_match(records),
        "t1b_abstention_accuracy": headline_abstention(records),
        "pair_consistency": headline_pair_consistency(records),
        # ---- reported, explicitly secondary ----------------------------------------
        "composite": {
            "value": _mean(composites),
            "n": len(composites),
            "note": (
                "Weighted sum (amounts 0.325, eligibility 0.215, periods 0.110, "
                "abstention 0.350) behind a pass/fail gate. Reported for completeness. "
                "It is NOT the headline: quote the three headline fields instead."
            ),
        },
        "diagnostics": diagnostics(records),
        "run": {
            "model": model,
            "split": split,
            "condition": condition,
            "seed": seed,
            "policyengine_us": version("policyengine-us"),
            "python": platform.python_version(),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **(extra or {}),
        },
        "per_task": [
            {
                "task_hash": r.task_hash,
                "household_id": r.household_id,
                "determinability": r.determinability,
                "gate_passed": r.gate_passed,
                "exact_match": r.exact_match,
                "abstention_correct": r.abstention_correct,
                "composite": r.composite,
                "rewards": r.rewards,
                "parse_failure": r.parse_failure,
                "scorer_error": r.scorer_error,
                "pair_id": r.pair_id,
                "pair_role": r.pair_role,
                "withheld_fact": r.withheld_fact,
                "is_eligibility_flip": r.is_eligibility_flip,
            }
            for r in records
        ],
    }


# ------------------------------------------------------------------ publishing held-out
#
# A results file from a held-out run carries the private seed in three places, none of
# them obvious on inspection:
#
#   run.seed                   the seed, verbatim
#   per_task[].household_id    "hh-{seed}-{index:05d}"   - the seed is the first component
#   per_task[].pair_id         "pair-{seed}-{index:05d}" - the same
#
# Gitignoring `results/*heldout*` stops the file being committed by accident, but ignoring
# a file is a backstop and not a mechanism: the file still exists, and publishing a number
# means somebody eventually copies something out of it. `redact` is the mechanism. It emits
# `task_hash` - the identity CLAUDE.md's contamination story already asks for, produced by
# the library rather than invented here, and not invertible without regenerating the
# household through the oracle - and drops every seed-derived field.
#
# Answer keys were never serialised into `per_task`, so held-out *answers* were never at
# risk here. The seed was.

SEED_DERIVED_TASK_FIELDS = ("household_id", "pair_id")
"""Per-task fields formatted from the seed. A new field of this shape must be added here;
`assert_publishable` is what makes forgetting loud rather than silent."""


class SeedLeak(AssertionError):
    """Something about to be published carries the private seed, or is derived from it."""


def redact(results: dict) -> dict:
    """Return a publishable copy of a results file: no seed, no seed-derived identifier.

    Every headline, diagnostic and per-task score survives. What goes is identity that
    could be run backwards to the seed.

    Pairs are **renumbered** rather than hashed. A hash of `pair-{seed}-{index}` would be
    invertible by anyone willing to enumerate seeds and hash a short string, and the index
    range is small and known - so hashing would look like protection while offering
    roughly none. Sequential labels in first-appearance order keep pair grouping legible
    and carry no seed material at all.
    """
    out = json.loads(json.dumps(results))  # deep copy; results are plain JSON already

    run = out.setdefault("run", {})
    run.pop("seed", None)
    run["redacted"] = True
    run["redaction_note"] = (
        "Seed and all seed-derived identifiers removed. Tasks are identified by "
        "task_hash, the library's content hash of the task's wire data."
    )

    # Pair labels are assigned from the ORIGINAL rows, so grouping survives field removal.
    pair_labels: dict[str, str] = {}
    for original, row in zip(results.get("per_task", []), out.get("per_task", []),
                             strict=True):
        pid = original.get("pair_id") or ""
        if pid:
            row["pair"] = pair_labels.setdefault(pid, f"pair-{len(pair_labels) + 1:04d}")
        for name in SEED_DERIVED_TASK_FIELDS:
            row.pop(name, None)

    return out


def assert_publishable(results: dict, seed: int | None = None) -> None:
    """Raise `SeedLeak` unless `results` is free of the seed and everything derived from it.

    Call this on anything about to leave the machine. When the seed is supplied the check
    is a scan of the serialised text rather than a field checklist, deliberately: a
    checklist only covers the fields somebody remembered, and the failure this guards
    against is precisely a field nobody thought of.
    """
    if seed is not None and str(seed) in json.dumps(results):
        raise SeedLeak(
            "the seed appears somewhere in this results file. It must not be published. "
            "Use redact() and publish its output instead."
        )

    if "seed" in results.get("run", {}):
        raise SeedLeak("run.seed is present. Use redact() before publishing.")

    for row in results.get("per_task", []):
        for name in SEED_DERIVED_TASK_FIELDS:
            if name in row:
                raise SeedLeak(
                    f"per_task carries {name!r}, which is formatted from the seed "
                    f"(hh-{{seed}}-{{index}}). Use redact() before publishing."
                )
