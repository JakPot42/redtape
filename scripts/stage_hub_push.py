"""Build the staging tree that `prime env push --path` uploads.

WHY A STAGING TREE AND NOT THE REPO ITSELF (docs/LIMITS.md 30):
`prime env push` uploads a SOURCE tree, filtered only by the root .gitignore. This repo
deliberately COMMITS cache/responses/dev/ - it derives from the public seed, it was paid
for, and losing it costs money - so pushing the repo directly would upload 1,584 cached
model responses as the environment's source. Not a leak; simply the wrong artifact.

Gitignoring the cache to fix the push would change a deliberate repository decision to suit
a packaging tool. Staging keeps the two concerns separate.

WHAT SHIPS is what someone needs to RUN the environment and judge it:
  redtape/   the taskset, oracle, schemas and scoring
  eval/      the baselines and the ceiling agent - all of which run with no API key
  tests/     the suite, so the claims can be checked rather than believed
  data/dev/  the split itself, public seed 20260828
  docs/LIMITS.md   linked from the README, and the thing worth reading first

WHAT DOES NOT: cache/ (our evaluation record), results/ (in the repo, linked), scripts/,
CLAUDE.md (our process log), and anything hidden.
"""

import shutil
import sys
from pathlib import Path

SRC = Path("/home/jak/redtape")
DST = Path("/tmp/hub-staging/redtape")

# rules/ is the verification-requirements table the scoring lints against - environment data,
# not process. scripts/ ships because the test suite drives the real CLIs as subprocesses:
# without it, tests/test_seed_discipline.py fails, and one of its assertions would otherwise
# PASS for the wrong reason (a non-zero exit from a missing file rather than from
# MissingHeldoutSeed). Shipping a tree whose suite does not pass, while the README claims a
# passing suite, is not an option.
DIRS = ["redtape", "eval", "tests", "rules", "scripts"]
FILES = ["pyproject.toml"]

# Refuse to stage if the source tree is not the one we think it is.
if not (SRC / "redtape" / "envs" / "t1_eligibility.py").exists():
    sys.exit(f"source tree does not look like redtape: {SRC}")

if DST.exists():
    shutil.rmtree(DST)
DST.mkdir(parents=True)


def prune(path: Path) -> None:
    """Drop caches, hidden files and anything held-out - belt and braces."""
    for p in sorted(path.rglob("*"), reverse=True):
        if p.name == "__pycache__" or p.name.startswith("."):
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        elif "heldout" in p.name.lower():
            sys.exit(f"refusing to stage held-out material: {p}")


for d in DIRS:
    shutil.copytree(SRC / d, DST / d)
for f in FILES:
    shutil.copy2(SRC / f, DST / f)

# The split, at the path pyproject's force-include expects.
(DST / "data" / "dev").mkdir(parents=True)
for name in ["t1.jsonl", "t1.manifest.json", "t1_smoke.jsonl", "t1_smoke.manifest.json"]:
    shutil.copy2(SRC / "data" / "dev" / name, DST / "data" / "dev" / name)

# LIMITS ships. It is the document the README tells people to read first, and a link into
# GitHub is not the same as having it in the artifact.
(DST / "docs").mkdir()
shutil.copy2(SRC / "docs" / "LIMITS.md", DST / "docs" / "LIMITS.md")

prune(DST)

# The hub README is written separately: it is the environment's page, not the repo's.
shutil.copy2(SRC / "docs" / "HUB_README.md", DST / "README.md")

n = sum(1 for p in DST.rglob("*") if p.is_file())
print(f"staged {n} files at {DST}")
for p in sorted(DST.rglob("*")):
    if p.is_file() and p.suffix != ".json" or p.parent.name == "dev":
        print("  ", p.relative_to(DST))
