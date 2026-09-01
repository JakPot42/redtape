"""Describe a built split: class mix against target, pairs, hashes, provenance.

    ./.venv/bin/python scripts/describe_split.py data/dev/t1.jsonl

Prints nothing seed-derived for a held-out split beyond the fingerprint already in its
manifest, so the output is safe to paste into a report.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def describe(path: Path) -> int:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    manifest_path = path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    n = len(rows)
    split_kind = manifest.get("split", "?")
    print(f"{path}  ({split_kind})")
    print(f"  n = {n}")
    print(f"  engine {manifest.get('policyengine_us')}  python {manifest.get('python')}")
    print(f"  seed fingerprint {manifest.get('seed_fingerprint')}")
    if split_kind == "dev":
        print(f"  seed {manifest.get('seed')} (public)")
    print()

    mix = collections.Counter(
        "eligibility_flip" if r.get("is_eligibility_flip") else r["determinability"]
        for r in rows
    )
    targets = manifest.get("targets", {})
    target_frac = {
        "eligibility_flip": targets.get("eligibility_flip"),
        "indeterminate": targets.get("indeterminate"),
        "incomplete_determinate": targets.get("incomplete_determinate"),
    }

    print(f"  {'class':<24} {'n':>6} {'achieved':>10} {'target':>10} {'delta':>8}")
    print(f"  {'-' * 24} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 8}")
    for cls in ("determinate", "indeterminate", "incomplete_determinate",
                "eligibility_flip"):
        got = mix.get(cls, 0)
        frac = got / n if n else 0.0
        tgt = target_frac.get(cls)
        if tgt is None:
            print(f"  {cls:<24} {got:>6} {frac:>9.1%} {'-':>10} {'-':>8}")
        else:
            print(f"  {cls:<24} {got:>6} {frac:>9.1%} {tgt:>9.1%} "
                  f"{frac - tgt:>+8.1%}")
    print(f"  {'TOTAL':<24} {sum(mix.values()):>6}")
    print()

    # Pairs
    pairs = collections.defaultdict(list)
    for r in rows:
        if r.get("pair_id"):
            pairs[r["pair_id"]].append(r)
    roles = collections.Counter(r.get("pair_role", "") for r in rows if r.get("pair_id"))
    bad = {k: len(v) for k, v in pairs.items() if len(v) != 2}
    print(f"  matched pairs: {len(pairs)} (target {targets.get('pairs')})")
    print(f"    roles: {dict(roles)}")
    print(f"    pairs without exactly 2 members: {bad or 'none'}")
    print()

    # Withheld facts
    facts = collections.Counter(r["withheld_fact"] for r in rows if r["withheld_fact"])
    print("  withheld facts:")
    for fact, count in facts.most_common():
        print(f"    {fact:<32} {count:>5}")
    print()

    # Identity
    hashes = [r.get("task_hash") for r in rows]
    missing = sum(1 for h in hashes if not h)
    print(f"  task_hash recorded: {n - missing}/{n}")
    print(f"  distinct task hashes: {len(set(hashes))}")
    print(f"  manifest records {len(manifest.get('task_hashes', []))} hashes")

    gen = manifest.get("generation", {})
    print(f"  candidates consumed {gen.get('candidates_consumed')}, "
          f"discarded {gen.get('candidates_discarded')}, {gen.get('seconds')}s")

    problems = []
    if missing:
        problems.append(f"{missing} rows without a task_hash")
    if len(set(hashes)) != n:
        problems.append("duplicate task hashes")
    if bad:
        problems.append(f"{len(bad)} malformed pairs")
    if problems:
        print()
        print("  PROBLEMS: " + "; ".join(problems))
        return 1
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rc = 0
    for arg in sys.argv[1:]:
        rc |= describe(Path(arg))
        print()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
