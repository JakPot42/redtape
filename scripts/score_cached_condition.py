"""Score a live tool condition from whatever is cached, dropping uncached tasks."""
import collections
import json
import pathlib
import sys

sys.path.insert(0, "/home/jak/redtape")

from redtape.config import load_dotenv  # noqa: E402

load_dotenv()

from eval.run_eval import cached_subset, live_agent, run  # noqa: E402
from redtape.envs.t1_eligibility import T1Data, T1Task, T1TaskConfig  # noqa: E402

SPLIT = pathlib.Path("/home/jak/redtape/data/dev/t1.jsonl")
RESULTS = pathlib.Path("/home/jak/redtape/results")
QUOTA = {"determinate": 150, "indeterminate": 60,
         "incomplete_determinate": 50, "eligibility_flip": 40}
condition = sys.argv[1]


def cls(r):
    return "eligibility_flip" if r.get("is_eligibility_flip") else r["determinability"]


rows = [json.loads(ln) for ln in SPLIT.open(encoding="utf-8") if ln.strip()]
by = collections.defaultdict(list)
for r in rows:
    if not r.get("pair_id"):
        by[cls(r)].append(r)
picked = []
for c, want in QUOTA.items():
    g = sorted(by[c], key=lambda r: r["index"])
    picked.extend(g[::max(1, len(g) // want)][:want])

cfg = T1TaskConfig()
tasks = [T1Task(T1Data.model_validate(
    {k: v for k, v in r.items()
     if k not in ("task_hash", "task_key", "unscored_deciding_programs",
                  "pair_truth_differs")}), cfg) for r in picked]

tasks = cached_subset(tasks, "claude-opus-5", condition)
run(tasks, live_agent(condition, "claude-opus-5"), model="claude-opus-5",
    split="t1_live300", condition=condition,
    out=RESULTS / f"t1_live300.live.{condition}.json", progress_every=150)
