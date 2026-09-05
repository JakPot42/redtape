"""Print the EXACT file list `prime env push` would upload, using the CLI's own collector.

Usage: preflight_push.py [ENV_PATH]        (default: the repo itself)

The wheel audit (scripts/audit_wheel.py) checks the built artifact. It does NOT check the
push archive, which is a different thing: `prime env push` uploads a SOURCE tree selected by
prime_cli.commands.env._collect_archive_files, filtered by a third-party gitignore_parser
reading of the root .gitignore - not by git, and not by the hatch build config that decides
what goes in the wheel. Two artifacts, two selection mechanisms, two audits. See
docs/LIMITS.md 30, which exists because a clean wheel audit was briefly taken to answer this.

This imports the real collector rather than reimplementing its rules, so the answer cannot
drift from what the CLI actually does.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/jak/.local/share/uv/tools/prime/lib/python3.13/site-packages")
from prime_cli.commands.env import _collect_archive_files  # noqa: E402

ENV_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/jak/redtape")
files = _collect_archive_files(ENV_PATH)
rel = [str(p.relative_to(ENV_PATH)) for p in files]

print(f"prime env push would upload {len(rel)} files from {ENV_PATH}\n")
for r in rel:
    print("  ", r)

print("\n" + "=" * 72)

# ---- 1. secrets ------------------------------------------------------------------------
# cache/responses/dev is PUBLIC (public seed, committed deliberately because it is paid for)
# - it is the WRONG ARTIFACT for a hub push, not a secret. Bulk is checked separately below:
# conflating "would leak" with "does not belong" makes both easier to wave through.
FORBIDDEN = re.compile(r"(^|/)\.env($|\.)|heldout|\.pem$|\.key$|id_ed25519|id_rsa", re.I)
bad = [r for r in rel if FORBIDDEN.search(r)]
print(f"1. secret / held-out paths          : {len(bad)}")
for b in bad:
    print("     ***", b)

# ---- 2. bulk that does not belong in an environment -------------------------------------
BULK = re.compile(r"^cache/|^results/|^CLAUDE\.md$")
junk = [r for r in rel if BULK.search(r)]
print(f"2. non-environment bulk             : {len(junk)}")
for j in junk[:4]:
    print("     -", j)
if len(junk) > 4:
    print(f"     - ... and {len(junk) - 4} more")

# ---- 3. teeth: did the collector actually look inside subdirectories? --------------------
# Without this, every silent failure mode - wrong path, empty tree, a collector that only
# globs the root - produces the same output as a genuinely clean artifact. A scan that
# cannot tell "found nothing" from "looked nowhere" is not evidence. (LIMITS 29.)
must_find = ["pyproject.toml", "README.md", "docs/LIMITS.md", "data/dev/t1.jsonl"]
missing = [m for m in must_find if m not in rel]
nested = [r for r in rel if "/" in r]
print(f"3. positive control                 : {len(must_find) - len(missing)}/{len(must_find)} expected files found")
for m in missing:
    print("     *** expected but MISSING:", m)
print(f"   nested paths seen                : {len(nested)}  (0 would mean traversal never ran)")

print()
if bad:
    sys.exit("PREFLIGHT FAILED - secret material would be uploaded")
if junk:
    sys.exit(f"PREFLIGHT FAILED - {len(junk)} files that do not belong in an environment")
if missing or not nested:
    sys.exit("PREFLIGHT INCONCLUSIVE - positive control did not fire; this scan proved nothing")
print("PREFLIGHT CLEAN - no secrets, no bulk, and the positive control fired.")
