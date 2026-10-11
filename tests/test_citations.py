"""Every test, script and module named in the project's rules and limits must exist.

CLAUDE.md, "A documented control needs a test that fails when it is absent": a citation is
part of the claim. LIMITS §1 cited `tests/test_period_semantics.py` as the lock on period
semantics for weeks, and the file had never existed. A reader checking it would conclude
the control was missing, and one nearly did (LIMITS §33).

Mentions that exist only to RECORD a past wrong citation are allowlisted by exact string,
each with its reason. Adding to the allowlist is a decision, the same as EXPLAINED in
test_extreme_sweep.py.

TWO KINDS OF DOCUMENT, since 2026-10-10. The published ones (LIMITS, README, the CI
workflow) are tracked, exist in every checkout, and are always checked: if one is missing
the test errors, it does not pass. CLAUDE.md is the project's internal working notes. It
left the public tree on 2026-10-10 and is ignored by git, so it exists in a working folder
and nowhere else. Its citations are checked wherever the file is, by the two tests at the
bottom. In a checkout without it those two tests SKIP and say why. A skip is a hole in
the claim, and this one is stated: a clean checkout verifies the published documents and
says nothing about the internal notes.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DOCS = ["docs/LIMITS.md", "README.md", ".github/workflows/tests.yml"]
INTERNAL_DOC = "CLAUDE.md"

HISTORICAL = {
    "tests/test_period_semantics.py": "records the wrong citation fixed in LIMITS §1/§33",
    "test_period_semantics": "same",
    "redtape/eval/cache.py": "records the wrong path fixed in LIMITS §28/§33",
    "test_opus_cache_keys_unchanged_for_every_committed_dev_task":
        "replaced 2026-09-19; LIMITS §32 records the replacement",
}

# Mentioned in the internal notes only, so it can be neither excused nor required anywhere
# the notes are absent.
HISTORICAL_INTERNAL = {
    "tests/test_x.py": "placeholder in the CLAUDE.md rule text",
}

PATH_RE = re.compile(r"(?:tests|scripts|eval|redtape)/[\w/]+\.(?:py|json|jsonl|yml|yaml)")
TEST_RE = re.compile(r"\btest_[a-z0-9_]{6,}\b")

needs_internal_notes = pytest.mark.skipif(
    not (ROOT / INTERNAL_DOC).exists(),
    reason="CLAUDE.md is internal working notes, kept out of the public tree, so its "
           "citations are checked only where the file exists; the published documents are "
           "checked by the tests above in every checkout",
)


def _dangling(docs: list[str], allowlist: dict[str, str]) -> list[str]:
    tests_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("*.py"))
    out = []
    for doc in docs:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for path in sorted(set(PATH_RE.findall(text))):
            if path not in allowlist and not (ROOT / path).exists():
                out.append(f"{doc}: {path}")
        for fn in sorted(set(TEST_RE.findall(text))):
            if fn in allowlist:
                continue
            if f"def {fn}" not in tests_src and not (ROOT / "tests" / f"{fn}.py").exists():
                out.append(f"{doc}: {fn}")
    return out


def _unmentioned(docs: list[str], allowlist: dict[str, str]) -> list[str]:
    corpus = "\n".join((ROOT / d).read_text(encoding="utf-8") for d in docs)
    return [k for k in allowlist if k not in corpus]


def test_every_cited_test_and_path_exists():
    assert _dangling(PUBLIC_DOCS, HISTORICAL) == []


def test_allowlist_entries_are_still_mentioned():
    """A stale allowlist entry would silently excuse a future real miss of the same name."""
    assert _unmentioned(PUBLIC_DOCS, HISTORICAL) == []


def test_the_citation_scan_detects_a_missing_file(tmp_path):
    """Positive control. The scan was split in two on 2026-10-10, and a scan that reads the
    wrong list, or nothing, would pass forever. A document citing a test file and a test
    function that do not exist must come back with both."""
    doc = tmp_path / "doc.md"
    doc.write_text("see `tests/test_no_such_file_here.py` and `test_no_such_function_here`\n",
                   encoding="utf-8")
    assert _dangling([str(doc)], {}) == [
        f"{doc}: tests/test_no_such_file_here.py",
        f"{doc}: test_no_such_file_here",
        f"{doc}: test_no_such_function_here",
    ]
    assert _dangling([str(doc)], {"tests/test_no_such_file_here.py": "x",
                                  "test_no_such_file_here": "x",
                                  "test_no_such_function_here": "x"}) == []


@needs_internal_notes
def test_every_test_and_path_cited_in_the_internal_notes_exists():
    assert _dangling([INTERNAL_DOC], {**HISTORICAL, **HISTORICAL_INTERNAL}) == []


@needs_internal_notes
def test_internal_allowlist_entries_are_still_mentioned():
    assert _unmentioned([INTERNAL_DOC], HISTORICAL_INTERNAL) == []
