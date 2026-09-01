"""The determinism check, as an actual test.

**Why this file exists.** CLAUDE.md said "re-run this check after any dependency bump; if
it ever diverges, stop and report before generating anything", and `docs/LIMITS.md` 5 said
the check was "verified, and re-checked in CI". Neither was true. The reference values
(`snap` 969.0, `eitc` 4328.0, `ctc` 2200.0, `household_net_income` 32658.21) existed only
as prose, the household that produced them was never recorded, and there was no CI. The
only determinism test in the suite, `test_oracle_is_deterministic`, asserts
`compute(hh) == compute(hh)` inside a single process, which cannot detect a version bump
changing an answer - it would return the new value twice and pass.

A documented control that does not exist is worse than no control, because work gets
approved on the strength of it. This is the mechanism.

**The old reference values are not reproducible and are not reproduced here.** The
household behind them was never written down, so there is no way to ask the engine the
same question again. They also included `household_net_income`, which `compute()` does not
return. This fixture therefore establishes a NEW reference over the oracle's actual output
surface, and CLAUDE.md records the change.

**What this test is not.** Expected values here are engine output, so the test proves the
engine still says what it said - not that what it says is right. External validation is a
separate track with a hard rule that its expected values never come from the engine
(`tests/test_parameter_drift.py`, `tests/test_external_validation.py`). Keeping the two
apart is the whole point; a golden master that drifted into being cited as validation
would be the circularity this project exists to avoid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redtape.config import DEV_SEED
from redtape.generator.households import generate
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import Household

FIXTURE = Path(__file__).resolve().parent / "data" / "determinism_reference.json"


@pytest.fixture(scope="module")
def reference() -> dict:
    if not FIXTURE.is_file():
        pytest.fail(
            f"{FIXTURE} is missing. Regenerate it deliberately with "
            f"`./.venv/bin/python scripts/capture_reference.py` and read that script's "
            f"docstring first - re-capturing to make a failing test pass destroys the "
            f"evidence the fixture exists to collect."
        )
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cases(reference):
    return [pytest.param(c, id=f"case{c['index']}") for c in reference["cases"]]


def test_the_fixture_covers_more_than_one_household(reference):
    """A single-household golden master is one lucky cancellation away from vacuous."""
    assert len(reference["cases"]) >= 3


def test_oracle_output_matches_the_committed_reference(reference):
    """The check CLAUDE.md has been asking for since the first commit.

    Exact equality, not a tolerance. The +/-1 SNAP tolerance is a statement about what a
    MODEL is allowed to get wrong; the engine reproducing its own arithmetic is not
    entitled to any slack at all. A cent of drift is a finding.
    """
    mismatches = []
    for case in reference["cases"]:
        hh = Household.model_validate(case["household"])
        got = json.loads(compute(hh).answer.model_dump_json())
        if got != case["expected"]:
            mismatches.append((case["index"], case["expected"], got))

    if mismatches:
        lines = [
            f"Oracle output changed against the committed reference "
            f"(recorded on policyengine-us {reference['policyengine_us']}, "
            f"policyengine-core {reference['policyengine_core']}, "
            f"python {reference['python']}).",
            "",
            "This is a FINDING, not a nuisance. Do not re-capture the fixture to make it "
            "pass. Diff the change, understand which rule moved, and record it in "
            "CLAUDE.md and docs/LIMITS.md before touching this file.",
            "",
        ]
        for index, expected, got in mismatches:
            lines.append(f"  case {index}:")
            for key in sorted(set(expected) | set(got)):
                if expected.get(key) != got.get(key):
                    lines.append(f"    {key}:")
                    lines.append(f"      expected {expected.get(key)}")
                    lines.append(f"      got      {got.get(key)}")
        pytest.fail("\n".join(lines))


def test_the_generator_still_produces_the_reference_households(reference):
    """Separate signal, deliberately.

    The fixture stores each household in full, so an engine change and a generator change
    cannot mask one another. If this fails but the test above passes, the generator moved
    and the oracle did not - a different finding with a different fix.
    """
    assert reference["seed"] == DEV_SEED
    for case in reference["cases"]:
        regenerated = json.loads(generate(DEV_SEED, case["index"]).model_dump_json())
        assert regenerated == case["household"], (
            f"generate(DEV_SEED, {case['index']}) no longer matches the committed "
            f"household. The generator changed; the oracle reference is unaffected but "
            f"(seed, index) reproducibility is not."
        )


def test_the_recorded_environment_matches_the_running_one(reference):
    """Warns when the reference was captured somewhere else.

    Not a hard failure on the engine version: the point of the fixture is to be run
    ACROSS versions, and failing here would just duplicate the real check above. The
    interpreter is different - CLAUDE.md treats the 3.13 pin as measurement apparatus
    rather than setup, because two runs agreeing is only evidence if the interpreter is
    the same.
    """
    import platform
    from importlib.metadata import version

    assert platform.python_version() == reference["python"], (
        "the reference was captured on a different interpreter, so agreement or "
        "disagreement with it says nothing about the engine"
    )

    running = version("policyengine-us")
    if running != reference["policyengine_us"]:
        pytest.skip(
            f"reference captured on policyengine-us {reference['policyengine_us']}, "
            f"running {running} - the comparison above is the real check"
        )
