"""Pydantic models for households, answers, and provenance.

Two rules this module enforces structurally:

1. Every answer field carries its period explicitly. A number that leaves the reader
   to infer whether it is monthly or annual is a bug (CLAUDE.md).
2. A household fact is either present or explicitly marked unknown. There is no
   "absent" third state, because PolicyEngine would silently substitute a default
   for it and we would never notice.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Facts that may be withheld to create a T1b case. Names match the PolicyEngine
# input variable exactly, so provenance is unambiguous.
WITHHOLDABLE_FACTS = (
    "employment_income",
    "immigration_status",
    "housing_cost",
    "age",
    "is_disabled",
)


class ImmigrationStatus(str, Enum):
    CITIZEN = "CITIZEN"
    LEGAL_PERMANENT_RESIDENT = "LEGAL_PERMANENT_RESIDENT"
    REFUGEE = "REFUGEE"
    ASYLEE = "ASYLEE"
    UNDOCUMENTED = "UNDOCUMENTED"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------
# Household
# --------------------------------------------------------------------------------------


class Person(Strict):
    person_id: str
    age: int | None = Field(description="years; None means withheld, not zero")
    employment_income: float | None = Field(description="US dollars per YEAR; None means withheld")
    immigration_status: ImmigrationStatus | None = None
    is_disabled: bool | None = None

    def withheld(self) -> list[str]:
        return [f for f in ("age", "employment_income", "immigration_status", "is_disabled")
                if getattr(self, f) is None]


class Household(Strict):
    household_id: str
    seed: int
    index: int

    state: Literal["CA"] = "CA"
    tax_year: int = 2025
    month: str = Field(description='the SNAP month, "YYYY-MM"; SNAP is always scored monthly')

    people: tuple[Person, ...]
    housing_cost: float | None = Field(description="US dollars per YEAR, spm_unit; None means withheld")

    def withheld(self) -> list[str]:
        """Fact names withheld anywhere in this household, person-qualified."""
        out = ["housing_cost"] if self.housing_cost is None else []
        for p in self.people:
            out.extend(f"{p.person_id}.{f}" for f in p.withheld())
        return out

    @property
    def is_complete(self) -> bool:
        return not self.withheld()


# --------------------------------------------------------------------------------------
# Answers - every field states its period
# --------------------------------------------------------------------------------------


class SnapAnswer(Strict):
    period: Literal["month"] = "month"
    period_label: str = Field(description='the month scored, "YYYY-MM"')
    eligible: bool
    benefit: float = Field(description="US dollars for that MONTH, never annualized")


class MedicaidAnswer(Strict):
    period: Literal["year"] = "year"
    period_label: str
    person_eligible: dict[str, bool]


class AnnualAmount(Strict):
    period: Literal["year"] = "year"
    period_label: str
    amount: float


class Determinability(str, Enum):
    """Three-class T1b label. See CLAUDE.md."""

    DETERMINATE = "determinate"                     # complete facts; answer normally
    INDETERMINATE = "indeterminate"                 # a withheld fact flips the outcome; abstain
    INCOMPLETE_DETERMINATE = "incomplete_determinate"  # fact withheld but outcome unchanged; answer


class CannotDetermine(Strict):
    program: Literal["snap", "medicaid", "eitc", "ctc"]
    missing_fact: str = Field(description='e.g. "p1.employment_income"')


class T1Answer(Strict):
    snap: SnapAnswer
    medicaid: MedicaidAnswer
    eitc: AnnualAmount
    ctc: AnnualAmount
    cannot_determine: tuple[CannotDetermine, ...] = ()


class Provenance(Strict):
    """Where a single answer value came from. No value ships without one."""

    field: str
    variable: str = Field(description="PolicyEngine variable name")
    entity: str
    period_queried: str
    quantity_type: str


class OracleResult(Strict):
    answer: T1Answer
    provenance: tuple[Provenance, ...]
    engine_version: str
    python_version: str
