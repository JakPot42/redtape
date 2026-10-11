"""Facts the published pages state about this repository must match the repository.

`tests/test_published_figures.py` holds README.md and docs/HUB_README.md to the committed
results. Nothing held them to the repository itself, and on 2026-10-10 a review found three
things wrong on those two pages:

  * README.md gave the held-out seed fingerprint as `b80dea37628d57fe`, a value the project
    had declared void and replaced on 2026-09-03. The current one was in docs/LIMITS.md and
    in the held-out manifest the whole time.
  * The pages quoted three test counts (255, 242 and 255). The suite collected 404.
  * They quoted two counts of the sections in docs/LIMITS.md (44 and 30). It had 49.

Each check below compares a page with something the page does not control.

WHAT IS CHECKED
  fingerprint  The fingerprint README.md gives for the held-out seed is
               CURRENT_HELDOUT_FINGERPRINT, docs/LIMITS.md records the same value, and no
               page contains a fingerprint listed as void. Where the held-out manifest
               exists it must agree too. The manifest is gitignored, so it is absent from a
               clean checkout: that one test SKIPS there and says why, and the others still
               run. The check does not rest on a file CI never has.
  test counts  Every "N tests" on either page equals the number of tests the suite
               collects, and every "P passed, S skipped" adds up to that number.
  sections     Every "N sections" on either page equals the number of numbered sections in
               docs/LIMITS.md.

WHAT IS NOT CHECKED, so nobody reads more into a green run than is there
  * How a run splits into passed and skipped. That depends on what is on disk, and a test
    cannot see the outcome of the run it is part of. Only the sum is checked: adding or
    removing a test turns this red until the figures are measured again, but one test
    changing from passed to skipped would not.
  * A count written in words ("three of them"), or phrased in a form the patterns below do
    not match. A page with no recognisable count at all FAILS, so the check cannot pass by
    finding nothing.

This file never reads `.env` and never reads or prints a seed. It reads one key of the
held-out manifest, `seed_fingerprint`, which is the value meant for publication.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["README.md", "docs/HUB_README.md"]
LIMITS = ROOT / "docs" / "LIMITS.md"
HELDOUT_MANIFEST = ROOT / "data" / "heldout" / "t1.manifest.json"


def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """One line with single spaces and no block-quote markers, so a phrase that wraps across
    two lines of a page is still found."""
    lines = (re.sub(r"^\s*>\s?", "", line) for line in text.splitlines())
    return " ".join(" ".join(lines).split())


def _number(s: str) -> int:
    return int(s.replace(",", ""))


# ------------------------------------------------------- 1. the held-out seed fingerprint

# The held-out seed in use, identified by sha256(str(seed))[:16]. The fingerprint discloses
# nothing about the seed (redtape/config.py::seed_fingerprint). Rotating the seed is a
# deliberate act in three steps: move this value into VOID_HELDOUT_FINGERPRINTS, put the new
# one here, and bring README.md and docs/LIMITS.md into line.
CURRENT_HELDOUT_FINGERPRINT = "9a608a27bead4c03"

# Fingerprints of the two earlier held-out seeds, both rotated out in September 2026. No
# number from a split built on either may be published, and neither may be presented as
# the seed in use.
VOID_HELDOUT_FINGERPRINTS = {"ab72ee672111f7fe", "b80dea37628d57fe"}

HEX16 = re.compile(r"(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])")


def stated_fingerprints(text: str) -> set[str]:
    """Every 16-hex-digit value on a line that says "fingerprint"."""
    return {h for line in text.splitlines() if "fingerprint" in line.lower()
            for h in HEX16.findall(line)}


def void_fingerprints_in(text: str) -> set[str]:
    return {v for v in VOID_HELDOUT_FINGERPRINTS if v in text}


def test_readme_states_the_current_heldout_fingerprint():
    assert stated_fingerprints(_page("README.md")) == {CURRENT_HELDOUT_FINGERPRINT}, (
        "README.md must give exactly one held-out seed fingerprint, the current one. If the "
        "seed was rotated, follow the three steps above CURRENT_HELDOUT_FINGERPRINT."
    )


@pytest.mark.parametrize("page", PAGES)
def test_no_published_page_contains_a_void_fingerprint(page):
    text = _page(page)
    assert void_fingerprints_in(text) == set()
    assert stated_fingerprints(text) <= {CURRENT_HELDOUT_FINGERPRINT}


def test_limits_records_the_current_heldout_fingerprint():
    """The value above is a constant in a test, and a constant can drift alone. The log is
    the second tracked place it is written down."""
    assert CURRENT_HELDOUT_FINGERPRINT in LIMITS.read_text(encoding="utf-8")
    assert CURRENT_HELDOUT_FINGERPRINT not in VOID_HELDOUT_FINGERPRINTS


@pytest.mark.skipif(
    not HELDOUT_MANIFEST.exists(),
    reason="the held-out manifest is gitignored and absent from a clean checkout; the pages "
           "are still checked against the tracked record by the tests above",
)
def test_heldout_manifest_agrees_with_the_published_fingerprint():
    """Where the held-out split exists, its manifest is the source the fingerprint comes
    from. Only the fingerprint is read."""
    recorded = json.loads(HELDOUT_MANIFEST.read_text(encoding="utf-8")).get("seed_fingerprint")
    assert recorded == CURRENT_HELDOUT_FINGERPRINT, (
        "the held-out manifest was built from a different seed than the one README.md and "
        "this file name"
    )


def test_teeth_a_void_fingerprint_on_the_page_is_caught():
    """The defect this file was written for, put back: the void value where the current one
    belongs."""
    page = _page("README.md")
    for void in sorted(VOID_HELDOUT_FINGERPRINTS):
        stale = page.replace(CURRENT_HELDOUT_FINGERPRINT, void)
        assert stale != page, "fixture drifted: README.md no longer holds the fingerprint"
        assert stated_fingerprints(stale) == {void}
        assert void_fingerprints_in(stale) == {void}


# ------------------------------------------------------------------- 2. the test counts

RUN_OUTCOME = re.compile(r"(?<![\w.,])(\d[\d,]*) passed, (\d[\d,]*) skipped\b")
COUNT = re.compile(r"(?<![\w.,])(\d[\d,]*) (tests|collected|passed|skipped|failed|errors?)\b")

# A count quoted as history, not as a claim about the suite today. Exact strings, each with
# its reason; adding one is a decision.
HISTORICAL_COUNTS = {
    "202 passed, 5 skipped": "the first CI run, quoted in README.md as the reason skip "
                             "counts are stated at all",
}


def _is_whole_suite(request) -> bool:
    """True when this session collected the whole suite and left nothing out.

    A session can be partial in three ways, and each would hand this check a number that is
    not the suite's: paths or node ids on the command line; `-k`, `-m` or `--deselect`; and
    `--lf` or `--ignore`, which leave whole files out without reporting anything as
    deselected. The last is caught by asking whether every test file on disk has at least
    one test in the session.
    """
    config, session = request.config, request.session
    if config.args_source is not pytest.Config.ArgsSource.TESTPATHS:
        return False
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or reporter.stats.get("deselected"):
        return False
    on_disk = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    return on_disk <= {item.path.name for item in session.items}


def _collect_in_a_child_process() -> int:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800,
    )
    m = re.search(r"^(\d+) tests? collected in ", r.stdout, re.M)
    assert r.returncode == 0 and m, (
        "could not count the suite in a child process:\n" + r.stdout[-2000:] + r.stderr[-2000:]
    )
    return int(m.group(1))


@pytest.fixture(scope="session")
def collected(request) -> int:
    """How many tests the whole suite collects.

    A session running the whole suite already knows. A partial one (a single file, a node
    id, `-k`, `--lf`) does not, so it asks a child process to collect everything. That costs
    the import time once, and it means this check never compares a page with the size of
    whatever subset happened to be selected.
    """
    if _is_whole_suite(request):
        return request.session.testscollected
    return _collect_in_a_child_process()


def count_problems(text: str, collected: int) -> list[str]:
    flat = _flat(text)
    for phrase in HISTORICAL_COUNTS:
        flat = flat.replace(phrase, " ")
    problems = []
    for m in RUN_OUTCOME.finditer(flat):
        total = _number(m.group(1)) + _number(m.group(2))
        if total != collected:
            problems.append(f'"{m.group(0)}" adds up to {total}; the suite collects {collected}')
    totals = 0
    for m in COUNT.finditer(RUN_OUTCOME.sub(" ", flat)):
        if m.group(2) in ("tests", "collected"):
            totals += 1
            if _number(m.group(1)) != collected:
                problems.append(f'"{m.group(0)}", but the suite collects {collected}')
        else:
            problems.append(
                f'"{m.group(0)}" is a run outcome this check cannot place: write it as '
                f'"P passed, S skipped", or add it to HISTORICAL_COUNTS with the reason'
            )
    if totals == 0:
        problems.append("no test count found, so nothing was compared")
    return problems


@pytest.mark.parametrize("page", PAGES)
def test_quoted_test_counts_match_the_suite(page, collected):
    problems = count_problems(_page(page), collected)
    assert not problems, (
        f"{page} quotes a test count the suite does not have. Measure again (the full "
        f"summary line and the exit code, in a clean checkout and in the working folder) "
        f"and update the page:\n" + "\n".join(f"  {p}" for p in problems)
    )


def test_historical_counts_are_still_quoted():
    """A stale allowlist entry would excuse a future stale count of the same text."""
    corpus = " ".join(_flat(_page(p)) for p in PAGES)
    assert [k for k in HISTORICAL_COUNTS if k not in corpus] == []


def test_teeth_a_stale_test_count_is_caught(collected):
    """The figures the 2026-10-10 review found, plus the ways a count can hide."""
    ok = f"The suite collects {collected:,} tests."
    assert count_problems(ok, collected) == []
    for stale in {255, 242, collected - 1, collected + 1} - {collected}:
        assert count_problems(ok.replace(f"{collected:,}", f"{stale:,}"), collected), stale
    # a count wrapped across two lines of a block quote
    assert count_problems(f"> it has {collected + 7}\n> tests in all", collected)
    # a run outcome that does not add up, and one that does
    assert count_problems(f"{ok} A run gives {collected - 2} passed, 1 skipped.", collected)
    assert count_problems(f"{ok} A run gives {collected - 2} passed, 2 skipped.", collected) == []
    # an outcome in a form that cannot be checked is refused, not waved through
    assert count_problems(f"{ok} Last time 3 failed.", collected)
    # history is exempt only as the exact allowlisted phrase
    assert count_problems(f'{ok} An earlier run was "202 passed, 5 skipped".', collected) == []
    assert count_problems(f'{ok} An earlier run was "202 passed, 6 skipped".', collected)
    # and a page that quotes no count at all does not pass by default
    assert count_problems("No figure here.", collected)


# -------------------------------------------------- 3. the sections of docs/LIMITS.md

SECTION_HEADING = re.compile(r"^## (\d+)\. ", re.M)
QUOTED_SECTIONS = re.compile(r"(?<![\w.,])(\d[\d,]*) sections\b")


def limits_section_count() -> int:
    numbers = [int(n) for n in SECTION_HEADING.findall(LIMITS.read_text(encoding="utf-8"))]
    assert numbers == list(range(1, len(numbers) + 1)), (
        "docs/LIMITS.md is expected to number its sections 1..N in order; if that changed, "
        "decide what 'N sections' means before trusting this count"
    )
    return len(numbers)


def section_problems(text: str, sections: int) -> list[str]:
    quoted = [m.group(0) for m in QUOTED_SECTIONS.finditer(_flat(text))]
    problems = [f'"{q}", but docs/LIMITS.md has {sections}' for q in quoted
                if _number(q.split()[0]) != sections]
    if not quoted:
        problems.append("no section count found, so nothing was compared")
    return problems


@pytest.mark.parametrize("page", PAGES)
def test_quoted_section_counts_match_limits(page):
    problems = section_problems(_page(page), limits_section_count())
    assert not problems, f"{page}:\n" + "\n".join(f"  {p}" for p in problems)


def test_teeth_a_stale_section_count_is_caught():
    n = limits_section_count()
    assert n >= 49
    assert section_problems(f"It is {n} sections long.", n) == []
    for stale in (44, 30, n + 1):
        assert section_problems(f"It is {stale} sections long.", n), stale
    assert section_problems("It is long.", n)
