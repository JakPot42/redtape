"""Seed discipline: the held-out path fails closed, and results can be published safely.

These tests exist because of a real defect, not a hypothetical one. `build_split.py`
resolved its seed as `int(env) if env else 20260828`, so a held-out build with no `.env`
did not fail - it produced a split with *public* provenance and labelled it held-out. The
split would have looked completely normal while being worthless, which is the same shape
as the two scope bugs already in `docs/LIMITS.md`: `medicaid` (a dollar amount where a
boolean was meant) and `ctc` (gross entitlement where received value was meant). A
plausible value standing exactly where the real one belongs, and nothing raising.

So the tests below are about a *class* of failure. They check that the wrong thing stops
the build rather than that the right thing works.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.metrics import (
    SEED_DERIVED_TASK_FIELDS,
    SeedLeak,
    assert_publishable,
    redact,
)
from redtape.config import (
    DEV,
    DEV_SEED,
    HELDOUT,
    HELDOUT_SEED_VAR,
    MissingHeldoutSeed,
    SeedMisuse,
    load_dotenv,
    resolve_seed,
    seed_fingerprint,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_env_file(tmp_path, monkeypatch):
    """Point the loader at a directory with no `.env`, and clear the variable.

    Without this a test would silently read the developer's real `.env` and pass for the
    wrong reason - which is the same class of mistake the module under test exists to
    prevent, so it would be a poor place to make it.
    """
    monkeypatch.setattr("redtape.config.repo_root", lambda: tmp_path)
    monkeypatch.delenv(HELDOUT_SEED_VAR, raising=False)
    return tmp_path


# --------------------------------------------------------------- the held-out path stops
def test_heldout_with_no_seed_raises(no_env_file):
    """The defect, reproduced. It must now stop the build."""
    with pytest.raises(MissingHeldoutSeed) as exc:
        resolve_seed(HELDOUT)
    # The message has to say what went wrong and what to do, because whoever hits this is
    # about to spend hours on a generation run.
    assert HELDOUT_SEED_VAR in str(exc.value)
    assert str(DEV_SEED) in str(exc.value)


def test_heldout_never_falls_back_to_the_public_seed(no_env_file):
    """The specific thing that must not happen, asserted directly."""
    with pytest.raises(MissingHeldoutSeed):
        got = resolve_seed(HELDOUT)
        assert got != DEV_SEED, "held-out build silently used the PUBLIC dev seed"


def test_heldout_refuses_an_explicit_seed(no_env_file):
    """One source of truth. A seed on a command line can be wrong, and can be logged."""
    with pytest.raises(SeedMisuse):
        resolve_seed(HELDOUT, override=12345)


def test_heldout_refuses_a_non_integer_seed(no_env_file, monkeypatch):
    monkeypatch.setenv(HELDOUT_SEED_VAR, "not-a-number")
    with pytest.raises(MissingHeldoutSeed):
        resolve_seed(HELDOUT)


def test_the_cli_exits_non_zero_rather_than_building(monkeypatch):
    """End to end through the actual script, since that is what a person runs.

    The variable is set empty rather than deleted: `load_dotenv` does not override what is
    already in the environment, so an empty value is what a real half-configured shell
    looks like, and it must fail exactly as an absent one does.
    """
    proc = subprocess.run(
        [sys.executable, "scripts/build_split.py", "--split", "heldout", "--n", "4"],
        cwd=REPO, capture_output=True, text=True,
        env={**{k: v for k, v in __import__("os").environ.items()}, HELDOUT_SEED_VAR: ""},
    )
    assert proc.returncode != 0, "a held-out build with no seed SUCCEEDED"
    assert "MissingHeldoutSeed" in proc.stderr
    # It must stop before doing any work. The engine warm-up is the first expensive step.
    assert "engine warm" not in proc.stdout


def test_build_split_no_longer_contains_a_default_seed():
    """Teeth. The fix is the *absence* of a literal, which a normal test cannot observe.

    Modelled on `test_invariant_has_teeth`: if someone reintroduces a fallback constant,
    every other test here still passes, because they all exercise `resolve_seed`.
    """
    source = (REPO / "scripts" / "build_split.py").read_text(encoding="utf-8")
    assert str(DEV_SEED) not in source, (
        f"the public seed {DEV_SEED} is hardcoded in build_split.py again. It belongs in "
        f"redtape/config.py as DEV_SEED, reachable only through resolve_seed()."
    )


# --------------------------------------------------------------------- the dev path works
def test_dev_falls_back_to_the_public_seed(no_env_file):
    """Deliberate asymmetry: the dev seed is public, so a default costs nothing."""
    assert resolve_seed(DEV) == DEV_SEED


def test_dev_accepts_an_explicit_seed(no_env_file):
    assert resolve_seed(DEV, override=42) == 42


# ------------------------------------------------------------------------ .env auto-loads
def test_dotenv_loads_without_a_shell_export(no_env_file, monkeypatch):
    """A file that must be exported by hand is a file somebody forgets to export."""
    (no_env_file / ".env").write_text(f"{HELDOUT_SEED_VAR}=112233445566778899\n")
    assert resolve_seed(HELDOUT) == 112233445566778899


def test_dotenv_ignores_comments_and_blank_lines(no_env_file):
    (no_env_file / ".env").write_text(
        f"# a comment\n\n  \n{HELDOUT_SEED_VAR}=778899\n"
    )
    assert resolve_seed(HELDOUT) == 778899


def test_an_explicit_export_outranks_the_file(no_env_file, monkeypatch):
    """The auto-load stops a FORGOTTEN export falling back. It must not overrule a
    deliberate one."""
    (no_env_file / ".env").write_text(f"{HELDOUT_SEED_VAR}=111\n")
    monkeypatch.setenv(HELDOUT_SEED_VAR, "222")
    assert resolve_seed(HELDOUT) == 222


def test_load_dotenv_on_a_missing_file_is_not_an_error(no_env_file):
    """The consequence lands at resolve_seed, which is where it can be explained."""
    assert load_dotenv(no_env_file / ".env") == []


# ----------------------------------------------------------------------------- redaction
def _results(seed: int = 998877665544332211) -> dict:
    """A results file shaped like the real one, carrying the seed in all three places."""
    return {
        "schema_version": "2",
        "t1_exact_match_determinate": {"value": 0.5, "n": 2},
        "t1b_abstention_accuracy": {"value": 0.5, "n": 2},
        "pair_consistency": {"value": 1.0, "n_pairs": 1},
        "composite": {"value": 0.4, "n": 2},
        "diagnostics": {"publishable": True, "scorer_error_count": 0},
        "run": {"model": "m", "split": "t1", "condition": "tool_less", "seed": seed},
        "per_task": [
            {"task_hash": "aaaa", "household_id": f"hh-{seed}-00001",
             "pair_id": f"pair-{seed}-00001", "exact_match": True, "composite": 0.9},
            {"task_hash": "bbbb", "household_id": f"hh-{seed}-00002",
             "pair_id": f"pair-{seed}-00001", "exact_match": False, "composite": 0.1},
            {"task_hash": "cccc", "household_id": f"hh-{seed}-00003",
             "pair_id": "", "exact_match": True, "composite": 0.7},
        ],
    }


def test_redacted_results_contain_no_seed_derived_field():
    """The headline requirement: nothing publishable may carry the seed."""
    seed = 998877665544332211
    public = redact(_results(seed))

    blob = json.dumps(public)
    assert str(seed) not in blob, "the seed survived redaction"
    assert "seed" not in public["run"]
    for row in public["per_task"]:
        for name in SEED_DERIVED_TASK_FIELDS:
            assert name not in row, f"{name} survived redaction"


def test_redaction_keeps_every_score_and_the_task_identity():
    """Redaction must cost provenance, not evidence."""
    original = _results()
    public = redact(original)

    assert public["t1_exact_match_determinate"] == original["t1_exact_match_determinate"]
    assert public["t1b_abstention_accuracy"] == original["t1b_abstention_accuracy"]
    assert public["diagnostics"] == original["diagnostics"]
    assert [r["task_hash"] for r in public["per_task"]] == ["aaaa", "bbbb", "cccc"]
    assert [r["composite"] for r in public["per_task"]] == [0.9, 0.1, 0.7]
    assert public["run"]["redacted"] is True


def test_redaction_preserves_pair_grouping_without_the_seed():
    """Pairs are renumbered, not hashed: a hash of `pair-{seed}-{index}` is invertible by
    anyone willing to enumerate seeds, so it would look like protection and offer none."""
    public = redact(_results())
    rows = public["per_task"]
    assert rows[0]["pair"] == rows[1]["pair"], "paired tasks lost their grouping"
    assert "pair" not in rows[2], "an unpaired task was given a pair label"
    assert "-" in rows[0]["pair"] and rows[0]["pair"].startswith("pair-")


def test_redaction_does_not_mutate_the_original():
    original = _results()
    redact(original)
    assert original["run"]["seed"] == 998877665544332211
    assert "household_id" in original["per_task"][0]


# ------------------------------------------------------------------- the guard has teeth
def test_assert_publishable_rejects_an_unredacted_file():
    with pytest.raises(SeedLeak):
        assert_publishable(_results())


def test_assert_publishable_rejects_the_seed_hiding_in_an_unexpected_field():
    """The reason this scans the serialised text instead of checking a field list: the
    failure being guarded against is a field nobody thought to put on the list."""
    seed = 998877665544332211
    r = redact(_results(seed))
    r["run"]["notes"] = f"regenerate with seed {seed}"
    with pytest.raises(SeedLeak):
        assert_publishable(r, seed)


def test_assert_publishable_accepts_a_redacted_file():
    seed = 998877665544332211
    assert_publishable(redact(_results(seed)), seed) is None


# --------------------------------------------------------------------------- fingerprint
def test_fingerprint_is_stable_and_does_not_disclose_the_seed():
    assert seed_fingerprint(20260828) == seed_fingerprint(20260828)
    assert seed_fingerprint(20260828) != seed_fingerprint(20260829)
    assert str(20260828) not in seed_fingerprint(20260828)
    assert len(seed_fingerprint(20260828)) == 16
