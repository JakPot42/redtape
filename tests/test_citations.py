"""Every test, script and module named in the project's rules and limits must exist.

CLAUDE.md, "A documented control needs a test that fails when it is absent": a citation is
part of the claim. LIMITS §1 cited `tests/test_period_semantics.py` as the lock on period
semantics for weeks, and the file had never existed. A reader checking it would conclude
the control was missing, and one nearly did (LIMITS §33).

Mentions that exist only to RECORD a past wrong citation are allowlisted by exact string,
each with its reason. Adding to the allowlist is a decision, the same as EXPLAINED in
test_extreme_sweep.py.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["CLAUDE.md", "docs/LIMITS.md", "README.md", ".github/workflows/tests.yml"]

HISTORICAL = {
    "tests/test_period_semantics.py": "records the wrong citation fixed in LIMITS §1/§33",
    "test_period_semantics": "same",
    "redtape/eval/cache.py": "records the wrong path fixed in LIMITS §28/§33",
    "tests/test_x.py": "placeholder in the CLAUDE.md rule text",
}

PATH_RE = re.compile(r"(?:tests|scripts|eval|redtape)/[\w/]+\.(?:py|json|jsonl|yml|yaml)")
TEST_RE = re.compile(r"\btest_[a-z0-9_]{6,}\b")


def _dangling() -> list[str]:
    tests_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("*.py"))
    out = []
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for path in sorted(set(PATH_RE.findall(text))):
            if path not in HISTORICAL and not (ROOT / path).exists():
                out.append(f"{doc}: {path}")
        for fn in sorted(set(TEST_RE.findall(text))):
            if fn in HISTORICAL:
                continue
            if f"def {fn}" not in tests_src and not (ROOT / "tests" / f"{fn}.py").exists():
                out.append(f"{doc}: {fn}")
    return out


def test_every_cited_test_and_path_exists():
    assert _dangling() == []


def test_allowlist_entries_are_still_mentioned():
    """A stale allowlist entry would silently excuse a future real miss of the same name."""
    corpus = "\n".join((ROOT / d).read_text(encoding="utf-8") for d in DOCS)
    assert [k for k in HISTORICAL if k not in corpus] == []
