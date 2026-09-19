"""Report one live results file beside the committed Opus 5 run.

    ./.venv/bin/python scripts/model_report.py results/t1.live.gpt-5.6-sol.tool_less.json

Computes nothing the results file does not contain except the class/fact join against the
split (same join as scripts/phase3_report.py). Prints the failure-mode counts FIRST, because
a degenerate headline is a harness bug until proven otherwise (CLAUDE.md, LIMITS §25/§27):
read the stop reasons and parse failures before reading any accuracy.

With --match-tasks the Opus column is restricted to the SAME task hashes as the file being
reported, so a 10-task probe is compared against Opus on those 10 tasks, not on 1,200.
"""
import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPUS = ROOT / "results" / "t1.live.claude-opus-5.tool_less.json"
SPLIT = ROOT / "data" / "dev" / "t1.jsonl"

ap = argparse.ArgumentParser()
ap.add_argument("results")
ap.add_argument("--match-tasks", action="store_true")
args = ap.parse_args()

new = json.loads(pathlib.Path(args.results).read_text(encoding="utf-8"))
opus = json.loads(OPUS.read_text(encoding="utf-8"))
rows = {r["household_id"]: r for r in
        (json.loads(ln) for ln in SPLIT.open(encoding="utf-8") if ln.strip())}

hashes = {t["task_hash"] for t in new["per_task"]}
opus_tasks = [t for t in opus["per_task"] if not args.match_tasks or t["task_hash"] in hashes]
name = new["run"]["model"]


def fmt(ok, n):
    return f"{ok / n:.3f} ({ok}/{n})" if n else "n/a"


def headlines(tasks):
    det = [t for t in tasks if t["determinability"] == "determinate"]
    t1b = [t for t in tasks if t["determinability"] != "determinate"]
    return (sum(t["exact_match"] for t in det), len(det),
            sum(t["abstention_correct"] for t in t1b), len(t1b))


def breakdown(tasks):
    by_class = collections.defaultdict(lambda: [0, 0])
    by_fact = collections.defaultdict(lambda: [0, 0])
    for rec in tasks:
        src = rows.get(rec["household_id"])
        if src is None or src["determinability"] == "determinate":
            continue
        cls = "eligibility_flip" if src.get("is_eligibility_flip") else src["determinability"]
        ok = int(bool(rec["abstention_correct"]))
        by_class[cls][0] += ok
        by_class[cls][1] += 1
        by_fact[src["withheld_fact"] or "(none)"][0] += ok
        by_fact[src["withheld_fact"] or "(none)"][1] += 1
    return by_class, by_fact


print("=" * 92)
print(f"{name}  -  {len(new['per_task'])} tasks  (Opus column: "
      f"{'same task hashes' if args.match_tasks else 'full committed run'}, "
      f"{len(opus_tasks)} tasks)")
print("=" * 92)

d = new["diagnostics"]
pf = d.get("parse_failures", {})
u = new["run"].get("usage", {}) or new.get("usage", {})
print("FAILURE MODES - read these first")
print(f"  parse failures     {pf}")
print(f"  scorer_error       {d['scorer_error_count']}   publishable={d['publishable']}")
print(f"  gate_pass_rate     {d['gate_pass_rate']:.3f}")
print(f"  stop reasons       {u.get('stop_reasons')}")
print(f"  served by          {u.get('served_by')}")
if u:
    b = u.get("billed", {})
    print(f"  cost               ${u.get('usd_actually_spent', 0):.4f} billed this run, "
          f"${u.get('usd_if_uncached', 0):.4f} from cold  "
          f"({b.get('input_tokens', 0):,} in / {b.get('output_tokens', 0):,} out billed)")
    n_all = u["billed"]["n"] + u["cached"]["n"]
    if n_all:
        print(f"  per task           ${u['usd_if_uncached'] / n_all:.4f}")

# Read from the cached REPLIES. per_task carries no answer, so the first version of this
# line counted a field that is never written and printed a confident 0/10 on a probe where
# the model had abstained three times - a degenerate number from the report, not the model.
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from eval.cache import get as cache_get, partition  # noqa: E402
from eval.run_eval import load_tasks, request_identity, task_cache_key  # noqa: E402
from redtape.scoring.parsing import parse_answer  # noqa: E402

cfg, system, tools = request_identity("tool_less", name)
abst = n_cached = 0
for t in load_tasks(str(SPLIT)):
    if t.hash not in hashes:
        continue
    hit = cache_get(task_cache_key(t, cfg, system, tools), partition(t.data.seed))
    if hit is None:
        continue
    n_cached += 1
    # The project's own parser, not json.loads: replies it accepts (fenced, or wrapped in
    # prose) are replies the scorer saw, and a raw json.loads silently skipped them.
    parsed = parse_answer(hit["reply"])
    abst += bool(parsed.answer is not None and parsed.answer.cannot_determine)
print(f"  replies with a non-empty cannot_determine: {abst}/{n_cached} (from cached replies)")

print("\nHEADLINES (reported separately; no composite)")
e1, n1, a1, m1 = headlines(new["per_task"])
e2, n2, a2, m2 = headlines(opus_tasks)
print(f"  {'':<34}{name:>26}{'claude-opus-5':>26}")
print(f"  {'T1 exact-match (determinate)':<34}{fmt(e1, n1):>26}{fmt(e2, n2):>26}")
print(f"  {'T1b abstention':<34}{fmt(a1, m1):>26}{fmt(a2, m2):>26}")
pc = new["pair_consistency"]
pc_new = "n/a" if pc["value"] is None else f"{pc['value']:.3f} ({pc.get('n_pairs')} pairs)"
pc_opus = ("(full run only)" if args.match_tasks
           else f"{opus['pair_consistency']['value']:.3f} (200 pairs)")
print(f"  {'pair-consistency':<34}{pc_new:>26}{pc_opus:>26}")

c1, f1 = breakdown(new["per_task"])
c2, f2 = breakdown(opus_tasks)
print("\nABSTENTION BY CLASS  (the category-vs-quantity question)")
for cls in ("eligibility_flip", "indeterminate", "incomplete_determinate"):
    print(f"  {cls:<34}{fmt(*c1.get(cls, [0, 0])):>26}{fmt(*c2.get(cls, [0, 0])):>26}")

print("\nABSTENTION BY WITHHELD FACT")
for fact in sorted(set(f1) | set(f2), key=lambda f: -f2.get(f, [0, 0])[1]):
    print(f"  {fact:<34}{fmt(*f1.get(fact, [0, 0])):>26}{fmt(*f2.get(fact, [0, 0])):>26}")
