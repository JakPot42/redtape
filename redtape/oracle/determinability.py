"""Perturbation-based determinability prober - the interim T1b labeller.

PolicyEngine has no representation of "unknown" (docs/LIMITS.md 3), so it cannot tell
us whether a withheld fact was load-bearing. We establish that ourselves: sweep the
withheld fact across a declared plausible range, recompute, and see whether the outcome
moves.

This is a finite-sample UNDER-APPROXIMATION. It can prove a fact is deciding (a flip was
observed) but cannot prove one is not - it only sampled. The declared range is recorded
with every label so the claim is auditable, and the SMT replacement (CLAUDE.md, deferred
design decisions) answers the same question exactly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import Determinability, Household, ImmigrationStatus, T1Answer

PROGRAMS = ("snap", "medicaid", "eitc", "ctc")

# Default amount tolerance, matching the scoring tolerance. A program counts as
# indeterminate if the sweep moves its amount by more than this.
DEFAULT_TOLERANCE = 1.0

# Declared sweep ranges. These are the justification for every label the prober emits,
# so they are data, not magic numbers buried in a loop.
#
# employment_income  - spans zero to comfortably over the SNAP gross income limit.
# housing_cost       - spans zero past the excess shelter deduction cap; the cap creates
#                      a plateau (docs/LIMITS.md 4), so low values must be sampled.
# immigration_status - every status the generator can produce; direction is never
#                      assumed, since UNDOCUMENTED can raise the benefit.
# age                - child/adult/senior boundaries that gate several programs.
SWEEPS: dict[str, tuple[Any, ...]] = {
    "employment_income": (0.0, 5_000.0, 12_000.0, 20_000.0, 30_000.0, 45_000.0, 80_000.0),
    "housing_cost": (0.0, 3_600.0, 9_000.0, 18_000.0, 30_000.0, 48_000.0),
    "immigration_status": tuple(ImmigrationStatus),
    "age": (2, 10, 17, 19, 35, 59, 66, 75),
    "is_disabled": (False, True),
}


class ProgramVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    program: str
    deciding: bool
    observed: tuple[str, ...]


class DeterminabilityLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    household_id: str
    withheld_fact: str
    sweep_values: tuple[str, ...]
    label: Determinability
    per_program: tuple[ProgramVerdict, ...]
    deciding_programs: tuple[str, ...]


def _restore(hh: Household, fact: str, value: Any) -> Household:
    """Put `value` back into the withheld slot."""
    if fact == "housing_cost":
        return hh.model_copy(update={"housing_cost": float(value)})

    pid, _, field = fact.partition(".")
    people = [
        p.model_copy(update={field: value}) if p.person_id == pid else p for p in hh.people
    ]
    return hh.model_copy(update={"people": tuple(people)})


def _observation(answer: T1Answer, program: str) -> str:
    """A comparable string per program. Amounts are rounded to the tolerance grid."""
    if program == "snap":
        return f"eligible={answer.snap.eligible} benefit={answer.snap.benefit:.2f}"
    if program == "medicaid":
        return " ".join(f"{k}={v}" for k, v in sorted(answer.medicaid.person_eligible.items()))
    if program == "eitc":
        return f"{answer.eitc.amount:.2f}"
    return f"{answer.ctc.amount:.2f}"


def _differs(a: str, b: str, program: str, tolerance: float) -> bool:
    """Booleans differ exactly; amounts differ only beyond the tolerance."""
    if a == b:
        return False
    if program in ("eitc", "ctc"):
        return abs(float(a) - float(b)) > tolerance
    if program == "snap":
        ea, ba = a.split(" benefit=")
        eb, bb = b.split(" benefit=")
        return ea != eb or abs(float(ba) - float(bb)) > tolerance
    return True  # medicaid: per-person booleans


def probe(hh: Household, fact: str, tolerance: float = DEFAULT_TOLERANCE) -> DeterminabilityLabel:
    """Classify one (household, withheld fact) pair.

    `hh` must already have `fact` withheld. Returns the three-class label plus which
    programs the fact actually decides.
    """
    if fact not in hh.withheld():
        raise ValueError(f"{fact!r} is not withheld in {hh.household_id}; nothing to probe")

    key = fact.partition(".")[2] or fact
    if key not in SWEEPS:
        raise ValueError(f"no declared sweep range for {key!r}")
    values = SWEEPS[key]

    observations: dict[str, list[str]] = {p: [] for p in PROGRAMS}
    for value in values:
        answer = compute(_restore(hh, fact, value)).answer
        for p in PROGRAMS:
            observations[p].append(_observation(answer, p))

    verdicts = []
    for p in PROGRAMS:
        obs = observations[p]
        deciding = any(_differs(obs[0], o, p, tolerance) for o in obs[1:])
        verdicts.append(ProgramVerdict(program=p, deciding=deciding, observed=tuple(obs)))

    deciding = tuple(v.program for v in verdicts if v.deciding)
    label = Determinability.INDETERMINATE if deciding else Determinability.INCOMPLETE_DETERMINATE

    return DeterminabilityLabel(
        household_id=hh.household_id,
        withheld_fact=fact,
        sweep_values=tuple(str(getattr(v, "value", v)) for v in values),
        label=label,
        per_program=tuple(verdicts),
        deciding_programs=deciding,
    )
