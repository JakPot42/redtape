"""Every headline figure a published page quotes must exist in the committed results.

docs/LIMITS.md §47. The v0.1.3 and v0.1.4 Hub pages carried a retraction notice at the top
while their bodies still quoted the withdrawn numbers as current: "The result" (0.396 /
0.050), a metrics table with the v0.1.0 Opus 5 row (0.514 / 0.438 / 0.570), and always-abstain
"scores 0.131". Nothing compared the page we ship against `results/`. This does.

WHAT COUNTS AS A HEADLINE FIGURE. A three-decimal value of one of the three headline metrics
(exact-match, abstention, pair-consistency):
  - in a markdown table, when the row label or the column header names one of them;
  - in prose, the first such value within a short window after the metric's name (or
    "scores").
Bracketed intervals, signed differences and p-values are not headline figures and are not
checked. That scope is deliberate: the three stale figures were all headline values, and
checking every decimal on the page would flag correct derived analysis (stratified tables,
confidence bounds) that is not in the results files by design.

WHAT A FIGURE MAY MATCH. Any value in a CURRENT-CORPUS `results/**/*.public.json`, outside
per-task and per-pair records, plus the same metrics recomputed on the task set two current
live runs have in common, which is how the cross-model tables are reported. A results file is
current only if every task hash it scored is in the committed dev split: the corpus rebuild
changed every hash, so a superseded run (e.g. the v0.1.0 Opus 5 run, whose 0.514 is exactly
the stale figure §47 records) cannot vouch for a figure. The first version of this test read
every results file, and its teeth failed for exactly that reason.

WHAT IS EXEMPT. A section whose heading, or any parent heading, says "superseded"; and a
paragraph that itself says the figure is retracted, withdrawn or superseded. The second is
needed because a retraction notice has to quote the number it retracts. It is also the
weakness of this check: a stale figure inside a paragraph that uses one of those words
passes. Keep retraction paragraphs about the retraction.
"""

import itertools
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/HUB_README.md"]
METRIC = re.compile(r"exact[- ]match|abstention|pair[- ]consistency", re.I)
PROSE_TRIGGER = re.compile(r"exact[- ]match|abstention|pair[- ]consistency|\bscores\b", re.I)
EXEMPT_HEADING = re.compile(r"superseded", re.I)
EXEMPT_PARAGRAPH = re.compile(r"retract|withdrawn|superseded", re.I)
FIGURE = re.compile(r"(?<![\d.])0\.\d{3}(?!\d)")
PROSE_WINDOW = 40


def _walk(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("per_task", "pairs"):
                _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)
    elif isinstance(obj, float) and 0.0 <= obj <= 1.0:
        out.add(f"{obj:.3f}")


def _common_set_figures(runs):
    out = set()
    for a, b in itertools.combinations(runs, 2):
        ta = {t["task_hash"]: t for t in a["per_task"]}
        tb = {t["task_hash"]: t for t in b["per_task"]}
        common = ta.keys() & tb.keys()
        for side in (ta, tb):
            det = [side[h] for h in common if side[h]["determinability"] == "determinate"]
            t1b = [side[h] for h in common if side[h]["determinability"] != "determinate"]
            if det:
                out.add(f"{sum(t['exact_match'] for t in det) / len(det):.3f}")
            if t1b:
                out.add(f"{sum(t['abstention_correct'] for t in t1b) / len(t1b):.3f}")
    return out


def _current_task_hashes():
    hashes = set()
    for split in ("data/dev/t1.jsonl", "data/dev/t1_smoke.jsonl"):
        for line in (ROOT / split).read_text(encoding="utf-8").splitlines():
            if line.strip():
                hashes.add(json.loads(line)["task_hash"])
    return hashes


def current_results(results_dir=ROOT / "results"):
    current = _current_task_hashes()
    for f in sorted(results_dir.glob("**/*.public.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        scored = {t["task_hash"] for t in d.get("per_task", [])}
        if scored and scored <= current:
            yield f, d


def allowed_figures(results_dir=ROOT / "results"):
    allowed, runs = set(), []
    for f, d in current_results(results_dir):
        _walk(d, allowed)
        if f.parent == results_dir and ".live." in f.name and d.get("per_task"):
            runs.append(d)
    return allowed | _common_set_figures(runs)


def _outside_brackets(text):
    return re.sub(r"\[[^\]]*\]", " ", text)


def _headline_figures_in_table_row(cells, header):
    found = []
    row_label = cells[0] if cells else ""
    for i, cell in enumerate(cells[1:], start=1):
        col = header[i] if header and i < len(header) else ""
        if METRIC.search(row_label) or METRIC.search(col):
            m = FIGURE.search(_outside_brackets(cell))
            if m:
                found.append(m.group(0))
    return found


def _headline_figures_in_prose(line):
    found = []
    for trig in PROSE_TRIGGER.finditer(line):
        window = line[trig.end(): trig.end() + PROSE_WINDOW]
        for m in FIGURE.finditer(window):
            prefix = window[max(0, m.start() - 6): m.start()]
            if re.search(r"[\[+\-−]\s*$|p\s*=\s*$", prefix) or window[:m.start()].count("[") > window[:m.start()].count("]"):
                continue
            found.append(m.group(0))
            break
    return found


def unsupported_figures(text, allowed):
    """(line number, figure, line) for every headline figure absent from `allowed`."""
    problems, stack, paragraph, header = [], [], [], None

    def flush():
        nonlocal header
        block = " ".join(line for _, line in paragraph)
        exempt = any(EXEMPT_HEADING.search(h) for _, h in stack) or EXEMPT_PARAGRAPH.search(block)
        header = None
        for n, line in paragraph:
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if header is None:
                    header = cells
                    continue
                if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                    continue
                figs = _headline_figures_in_table_row(cells, header)
            else:
                header = None
                figs = _headline_figures_in_prose(line)
            if not exempt:
                problems.extend((n, f, line) for f in figs if f not in allowed)
        paragraph.clear()

    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"^\s*>\s?", "", raw).rstrip()
        h = re.match(r"^(#{1,6})\s+(.*)", line)
        if h:
            flush()
            level = len(h.group(1))
            stack = [s for s in stack if s[0] < level] + [(level, h.group(2))]
        elif not line.strip():
            flush()
        else:
            paragraph.append((n, line))
    flush()
    return problems


@pytest.fixture(scope="module")
def allowed():
    return allowed_figures()


@pytest.mark.parametrize("doc", DOCS)
def test_published_headline_figures_exist_in_results(doc, allowed):
    problems = unsupported_figures((ROOT / doc).read_text(encoding="utf-8"), allowed)
    assert not problems, (
        f"{doc} quotes headline figures that no committed results file contains "
        f"(LIMITS §47). Update the page from results/*.public.json, or move the figure "
        f"into a section marked superseded:\n"
        + "\n".join(f"  line {n}: {f}   {line[:100]}" for n, f, line in problems)
    )


# --- teeth: each of the three stale figures §47 records must turn this red --------------

HUB = (ROOT / "docs/HUB_README.md").read_text(encoding="utf-8")


def _flagged(text, allowed):
    return {f for _, f, _ in unsupported_figures(text, allowed)}


def test_teeth_stale_metrics_table_row(allowed):
    stale = HUB.replace("| **Claude Opus 5** | **0.621** | **0.624** | **0.636** (198 pairs) |",
                        "| **Claude Opus 5** | **0.514** | **0.438** | **0.570** |")
    assert stale != HUB, "fixture drifted: the Opus 5 metrics row is not where this test expects"
    flagged = _flagged(stale, allowed)
    assert {"0.514", "0.570"} <= flagged
    # 0.438 is NOT flagged, and that is a known limit, not a pass: it coincides with a genuine
    # current figure (Opus 5's abstention on the indeterminate class is 0.438). A check that
    # matches values cannot tell a stale number from an equal current one. Asserted so the
    # limit stays visible: if this ever flips, the allowed set changed and should be read.
    assert "0.438" in allowed and "0.438" not in flagged


def test_teeth_stale_prose_figure(allowed):
    stale = HUB.replace('winning — it scores 0.000.', 'winning — it scores 0.131.')
    assert stale != HUB, "fixture drifted: the always-abstain sentence is not where expected"
    assert "0.131" in _flagged(stale, allowed)


def test_teeth_retracted_result_without_its_superseded_label(allowed):
    stale = re.sub(r"## Superseded result \(v0\.1\.0 corpus\): retracted, do not cite\n\n"
                   r"\*\*Kept for the record only\..*?above\.\n", "## The result\n", HUB,
                   flags=re.S)
    assert stale != HUB, "fixture drifted: the superseded heading is not where expected"
    assert {"0.396", "0.050"} <= _flagged(stale, allowed)


def test_superseded_runs_are_not_evidence(allowed):
    """The v0.1.0 run that produced the stale figures must not be read as current."""
    names = {f.name for f, _ in current_results()}
    assert "t1.live.claude-opus-5.tool_less.public.json" not in names
    assert "t1.live.claude-opus-5-5-openrouter.tool_less.public.json" in names
    assert "0.514" not in allowed


def test_teeth_superseded_label_exempts(allowed):
    """The same stale table is accepted once it sits under a superseded heading."""
    text = "## Superseded result: do not cite\n\n| | abstention |\n|---|---:|\n| x | 0.514 |\n"
    assert _flagged(text, allowed) == set()
    assert _flagged(text.replace("Superseded result: do not cite", "The result"), allowed) == {"0.514"}
