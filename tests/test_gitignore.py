"""The .gitignore is a control, so it gets a test that fails when it stops covering a path.

Third time the ignore file failed to cover what it claimed (CLAUDE.md, LIMITS §33): an editor
swap of `.env` matched neither `.env` pattern; `.env.save` from nano exists beside the live file;
and raw results were ignored only one folder deep, so `results/probe10/*.json` was committable.

Checked with `git check-ignore --no-index`, so paths need not exist and tracked state is
irrelevant: the question is only what the rules say.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

MUST_IGNORE = [
    ".env",
    ".env.save",
    ".env.local",
    "..env.swp",
    "data/heldout/t1.jsonl",
    "cache/responses/heldout/ab/abcd.json",
    "results/t1.live.m.tool_less.json",
    "results/probe10/t1.live.m.tool_less.json",
    "results/a/b/c/t1.raw.json",
    "results/t1_heldout.live.m.tool_less.public.json",
    "results/sub/t1_heldout.live.m.tool_less.public.json",
    # Internal working notes and unsent drafts. Representative paths, like the ones above:
    # the rule is what is tested, not any one file.
    "CLAUDE.md",
    "docs/EXAMPLE_DRAFT.md",
]

MUST_TRACK = [
    "results/t1.live.m.tool_less.public.json",
    "results/probe10/t1.live.m.tool_less.public.json",
    "data/dev/t1.jsonl",
    "cache/responses/dev/ab/abcd.json",
    # The rule for drafts must not swallow a published document.
    "docs/DECISION_RULE.md",
    "docs/HUB_README.md",
]


def _ignored(path: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT)
    if r.returncode not in (0, 1):
        pytest.fail(f"git check-ignore failed on {path!r} (exit {r.returncode})")
    return r.returncode == 0


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_sensitive_or_raw_paths_are_ignored(path):
    assert _ignored(path), f"{path} is NOT ignored"


@pytest.mark.parametrize("path", MUST_TRACK)
def test_publishable_paths_are_not_ignored(path):
    assert not _ignored(path), f"{path} is ignored but must be committable"


INTERNAL_ONLY = [":(glob)**/CLAUDE.md", ":(glob)docs/*_DRAFT.md"]


def test_internal_working_files_are_not_tracked():
    """An ignore rule keeps `git add -A` from picking a file up. It does nothing about a
    file that is already tracked, and `git add -f` walks straight past it. So the rules
    above are half of the control, and this is the other half: it asks git what is in
    the index."""
    r = subprocess.run(["git", "ls-files", "--", *INTERNAL_ONLY], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [], "tracked, and meant to stay out of the public tree"
