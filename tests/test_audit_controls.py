"""The controls CLAUDE.md asserts, with tests that fail when they are absent (LIMITS §33).

Three claims that nothing verified until now:

1. "Pin every dependency to an exact version (`==`, never `>=`)" - `pyproject.toml` used `>=`
   for the runtime dependencies, so a Hub install resolved whatever `verifiers` was current.
2. "Evaluation is engine-free; the oracle is never called during a rollout" - no test, and CI
   installs the engine, so an accidental `policyengine_us` import on the scoring path would
   pass here and fail for a stranger who installed the wheel.
3. "All `verifiers` contact stays inside `redtape/envs/`" - true by inspection, unverified.

Each test below is written so that it CANNOT pass by accident in this environment: the
engine-free test blocks the import rather than relying on the engine being absent (it is
installed here), and the isolation test is checked against a planted violation.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

# ---------------------------------------------------------------- 1. exact pins
_SPEC = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(.*)$")


def _declared_dependencies() -> list[tuple[str, str]]:
    """(name, specifier) for every dependency in EVERY table of pyproject.toml.

    Parsed with tomllib rather than by regex over lines: the first version of this read only
    the main `dependencies` table and silently checked 2 of 9 specifiers, which its own
    "has the parser lost sight of the file" assertion caught.
    """
    import tomllib

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    raw = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        raw.extend(extra)
    out = []
    for item in raw:
        m = _SPEC.match(item.split(";")[0].strip())
        assert m, item
        out.append((m.group(1), m.group(2).strip()))
    return out


def test_every_dependency_is_pinned_exactly():
    deps = _declared_dependencies()
    assert len(deps) >= 7, f"parsed only {deps}; the parser has lost sight of the file"
    bad = [f"{n}{s or ' (no specifier)'}" for n, s in deps if not s.startswith("==")]
    assert bad == [], (
        f"CLAUDE.md requires exact pins; these are not: {bad}. A Hub install does not use "
        f"uv.lock, so a floating runtime dependency ships to strangers."
    )


def test_the_pin_parser_would_notice_a_loose_specifier():
    """Positive control: the parser must actually see a `>=` when one is present."""
    assert [n for n, s in _declared_dependencies() if s.startswith(">=")] == []
    for sample, spec in (("verifiers>=0.3.1", ">=0.3.1"), ("pyyaml~=6.0", "~=6.0"),
                         ("pydantic", "")):
        m = _SPEC.match(sample)
        assert m and m.group(2) == spec, sample


# ------------------------------------------------- 2. evaluation is engine-free
ENGINE_FREE_PROGRAM = """
import sys

class Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("policyengine_us", "policyengine_core", "policyengine"):
            raise AssertionError("the evaluation path imported " + name)
        return None

sys.meta_path.insert(0, Blocker())

# The full path a scored rollout takes: load the split, read the prompt, parse a reply,
# score it. Nothing here may reach the engine.
from eval.run_eval import load_tasks, score_one, perfect_agent

tasks = load_tasks("data/dev/t1.jsonl", limit=25)
records = [score_one(t, perfect_agent(t)) for t in tasks]
assert len(records) == 25
assert all(r.gate_passed for r in records)
assert all(not r.scorer_error for r in records)
print("ENGINE_FREE_OK")
"""

BLOCKER_CONTROL = ENGINE_FREE_PROGRAM.replace(
    'print("ENGINE_FREE_OK")',
    "import policyengine_us  # noqa: F401\nprint('SHOULD NOT REACH HERE')",
)


def _run(program: str):
    return subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                          capture_output=True, text=True, timeout=900)


def test_scoring_path_never_imports_the_engine():
    """The engine IS installed here, so absence proves nothing: the import is BLOCKED and
    the blocker raises. If the scoring path ever imports policyengine, this fails."""
    r = _run(ENGINE_FREE_PROGRAM)
    assert "ENGINE_FREE_OK" in r.stdout, r.stdout + r.stderr


def test_the_engine_blocker_actually_blocks():
    """Positive control for the test above. Without this, a blocker that silently does
    nothing would make the engine-free claim pass for the wrong reason - and that claim is
    about what a stranger's install does, which this machine cannot otherwise observe."""
    r = _run(BLOCKER_CONTROL)
    assert r.returncode != 0
    assert "the evaluation path imported policyengine_us" in (r.stdout + r.stderr)


# ------------------------------------------------- 3. verifiers isolation
ALLOWED_VERIFIERS_DIR = ROOT / "redtape" / "envs"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _violations() -> list[str]:
    return [str(p.relative_to(ROOT)) for p in (ROOT / "redtape").rglob("*.py")
            if ALLOWED_VERIFIERS_DIR not in p.parents and "verifiers" in _imports(p)]


def test_verifiers_is_imported_only_inside_redtape_envs():
    assert _violations() == [], (
        "CLAUDE.md, 'Isolation rule': the generator, the oracles and the scorers take plain "
        "data and return plain values, so v1 API churn touches one directory."
    )


def test_the_isolation_scan_detects_a_planted_import(tmp_path):
    """Positive control: an AST scan that never matches anything would pass forever."""
    planted = tmp_path / "planted.py"
    planted.write_text("from verifiers.v1.task import Task\n", encoding="utf-8")
    assert "verifiers" in _imports(planted)
    planted.write_text("import verifiers\n", encoding="utf-8")
    assert "verifiers" in _imports(planted)


@pytest.mark.parametrize("module", ["redtape/schemas.py", "redtape/oracle/policyengine_oracle.py",
                                    "redtape/scoring/core.py", "redtape/generator/households.py"])
def test_core_modules_do_not_import_verifiers(module):
    assert "verifiers" not in _imports(ROOT / module)
