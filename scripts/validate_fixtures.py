"""Validation track (a): run the engine's own shipped YAML fixtures on our pinned build.

This proves wiring correctness and catches version drift. It is CIRCULAR as evidence of
external validity - these are the library's own tests - and docs/LIMITS.md says so.
Track (b), independently-sourced worked examples, is a separate exercise.
"""

import inspect
import os

import policyengine_us
from policyengine_core.tools.test_runner import run_tests

from policyengine_us import CountryTaxBenefitSystem

print("run_tests signature:", inspect.signature(run_tests))
print()

PE = os.path.dirname(policyengine_us.__file__)
TARGETS = {
    "SNAP (usda/snap)": os.path.join(PE, "tests", "policy", "baseline", "gov", "usda", "snap"),
    "California (states/ca)": os.path.join(PE, "tests", "policy", "baseline", "gov", "states", "ca"),
}

system = CountryTaxBenefitSystem()

for label, path in TARGETS.items():
    n = sum(len([f for f in fs if f.endswith((".yaml", ".yml"))]) for _, _, fs in os.walk(path))
    print("=" * 70)
    print(f"{label}: {n} yaml files")
    print("=" * 70)
    try:
        rc = run_tests(system, path, options={})
        print(f"  run_tests returned: {rc}")
    except SystemExit as e:
        print(f"  SystemExit: {e.code}")
    except Exception as e:
        print(f"  {type(e).__name__}: {str(e)[:400]}")
    print()
