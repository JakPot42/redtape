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
from redtape.schemas import (
    SAFE_IMMIGRATION_STATUSES,
    SCORED_PROGRAMS,
    Determinability,
    Household,
    ImmigrationStatus,
    T1Answer,
)

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
def _paid(annual: float) -> tuple[float, float]:
    """(annual income, weekly hours) at the California 2025 minimum wage, capped at 40 hrs.
    Earnings and hours are ONE withheld fact (generator.withhold), so they are swept as a
    pair; holding stated hours fixed while income ran to $0 would sweep impossible
    households."""
    return (annual, float(min(40, int(annual // (52 * 16.50)))))


SWEEPS: dict[str, tuple[Any, ...]] = {
    # (income, hours) pairs. 20,000 is ~23 hrs at the minimum wage: straddles the 20-hour
    # SNAP student exemption (7 CFR 273.5(b)(5)) on the low side via 12,000 (~13 hrs).
    "employment_income": tuple(_paid(a) for a in
                               (0.0, 5_000.0, 12_000.0, 20_000.0, 30_000.0, 45_000.0,
                                80_000.0)),
    "housing_cost": (0.0, 3_600.0, 9_000.0, 18_000.0, 30_000.0, 48_000.0),
    # Restricted to SAFE_IMMIGRATION_STATUSES, which for the CA/2025 corpus scope is
    # every engine status: California delays the HR 1 restriction to 2026-04-01 (CDSS
    # ACL 25-92) and the engine models that correctly, so REFUGEE/ASYLEE and the other
    # previously-excluded statuses carry a correct answer key here and are swept again
    # (docs/LIMITS.md 16).
    # (status, status_since). The start year travels with the status: it is stated only
    # when the status is, and a swept status needs a start year the oracle can set. 2012 is
    # far enough back that the five-year bar cannot apply for any swept value.
    "immigration_status": tuple(
        (s, None if s.value in ("CITIZEN", "UNDOCUMENTED") else 2012)
        for s in ImmigrationStatus if s.value in SAFE_IMMIGRATION_STATUSES
    ),
    "dependent_care_cost": (0.0, 600.0, 2_400.0, 6_000.0, 12_000.0),
    # Only p1.* facts are withheld and p1 is always an adult parent now, so child ages are
    # not plausible values (the old sweep included 2, 10 and 17 for the head of household).
    # Adult values at the legal thresholds: 25/65 childless EITC, 50 SNAP student
    # exemption, 60 SNAP elderly.
    "age": (19, 24, 25, 35, 49, 50, 59, 60, 64, 65, 75),
    "is_disabled": (False, True),
    # (is_higher_ed_student, student_full_time). Swept with intensity because 7 CFR
    # 273.5(b)(10) reads full-time status. Student status was the fact the flip class was
    # built on; with hours now stated, a student working 20+ hours is exempt and does not
    # flip - the class may shrink, and that is the correct result.
    "is_higher_ed_student": ((False, None), (True, False), (True, True)),
}

# Facts whose sweep value is a tuple spanning several fields (see generator.withhold).
_COUPLED = {
    "employment_income": ("employment_income", "weekly_hours"),
    "immigration_status": ("immigration_status", "status_since"),
    "is_higher_ed_student": ("is_higher_ed_student", "student_full_time"),
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
    """SCORED programs the fact decides. The label is derived from this, never from the
    full set - see `unscored_deciding_programs`."""
    unscored_deciding_programs: tuple[str, ...] = ()
    """Programs the fact decides that v0 does not score (Medicaid). Recorded for audit
    and never used to label a task.

    Keeping this visible rather than discarding it is the point: a fact that decides
    Medicaid alone is genuinely interesting, and if Medicaid ever gains external
    validation and enters SCORED_PROGRAMS, these are the tasks whose labels change."""


def _restore(hh: Household, fact: str, value: Any) -> Household:
    """Put `value` back into the withheld slot."""
    if fact in ("housing_cost", "dependent_care_cost"):
        return hh.model_copy(update={fact: float(value)})

    pid, _, field = fact.partition(".")
    update = dict(zip(_COUPLED[field], value)) if field in _COUPLED else {field: value}
    people = [
        p.model_copy(update=update) if p.person_id == pid else p for p in hh.people
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

    # The label is decided by SCORED programs only. A fact that moves Medicaid alone
    # leaves the task determinate *as far as this benchmark can score it*, and labelling
    # it INDETERMINATE would demand an abstention the scorer cannot credit - the
    # Milestone 1 defect. See redtape/scoring/invariants.py for the standing rule.
    all_deciding = tuple(v.program for v in verdicts if v.deciding)
    deciding = tuple(p for p in all_deciding if p in SCORED_PROGRAMS)
    unscored = tuple(p for p in all_deciding if p not in SCORED_PROGRAMS)
    label = Determinability.INDETERMINATE if deciding else Determinability.INCOMPLETE_DETERMINATE

    return DeterminabilityLabel(
        household_id=hh.household_id,
        withheld_fact=fact,
        sweep_values=tuple(str(getattr(v, "value", v)) for v in values),
        label=label,
        per_program=tuple(verdicts),
        deciding_programs=deciding,
        unscored_deciding_programs=unscored,
    )
