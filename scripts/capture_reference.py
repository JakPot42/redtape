"""Capture the determinism reference fixture. Run deliberately, never automatically.

    ./.venv/bin/python scripts/capture_reference.py

Writes `tests/data/determinism_reference.json`: a set of households serialised in full,
each with the exact oracle output they produced, and the engine and interpreter versions
that produced it.

**Regenerating this file is a decision, not a build step.** If the reference stops matching
after a dependency bump, that is the finding the fixture exists to surface - the engine's
answer to a fixed question changed. Re-capturing to make the test pass would delete exactly
the evidence we built it to collect. Re-capture only after a human has diffed the change,
understood it, and recorded it in CLAUDE.md and docs/LIMITS.md.
"""

from __future__ import annotations

import json
import platform
from importlib.metadata import version
from pathlib import Path

from redtape.config import DEV_SEED
from redtape.generator.households import generate
from redtape.oracle.policyengine_oracle import compute

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "determinism_reference.json"

# Indices into the PUBLIC dev seed. Nothing private goes near this fixture: it is committed
# and it is the one file in the repo whose entire purpose is to be compared against.
INDICES = (0, 1, 2, 3, 4)


def main() -> int:
    cases = []
    for index in INDICES:
        hh = generate(DEV_SEED, index)
        result = compute(hh)
        cases.append({
            "index": index,
            # The household is serialised IN FULL rather than referenced by (seed, index),
            # so the fixture does not depend on the generator staying still. A generator
            # change and an engine change are different findings and must not be able to
            # mask one another - test_determinism.py checks each separately.
            "household": json.loads(hh.model_dump_json()),
            "expected": json.loads(result.answer.model_dump_json()),
        })

    payload = {
        "note": (
            "Golden master for oracle drift. Expected values are ENGINE OUTPUT, not "
            "externally validated figures - this file detects change, it does not "
            "establish correctness. External validation lives in "
            "tests/test_external_validation.py and tests/test_parameter_drift.py, whose "
            "expected values must never come from the engine. Keep the two apart."
        ),
        "seed": DEV_SEED,
        "seed_is_public": True,
        "policyengine_us": version("policyengine-us"),
        "policyengine_core": version("policyengine-core"),
        "python": platform.python_version(),
        "cases": cases,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {len(cases)} cases -> {OUT}")
    print(f"  policyengine-us   {payload['policyengine_us']}")
    print(f"  policyengine-core {payload['policyengine_core']}")
    print(f"  python            {payload['python']}")
    for c in cases:
        s, e, ct = c["expected"]["snap"], c["expected"]["eitc"], c["expected"]["ctc"]
        print(f"  [{c['index']}] snap eligible={s['eligible']!s:<5} benefit={s['benefit']:>8.2f}  "
              f"eitc={e['amount']:>8.2f}  ctc={ct['amount']:>8.2f} "
              f"(gross {ct['gross_entitlement']:>8.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
