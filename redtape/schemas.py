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
    "declared_benefits",
    "dependent_care_cost",
    "immigration_status",
    "housing_cost",
    "age",
    "is_disabled",
)


# Statuses where the engine and the published post-HR 1 rules AGREE.
#
# PL 119-21 (enacted 2025-07-04) restricted SNAP to citizens, LPRs (after a five-year
# wait where applicable), Cuban/Haitian entrants, and COFA residents. The engine does not
# implement this: REFUGEE, ASYLEE, DEPORTATION_WITHHELD, CONDITIONAL_ENTRANT and
# PAROLED_ONE_YEAR are still modelled as fully eligible in every month of 2025
# (docs/LIMITS.md 16). Generating households with those statuses would bake a known-wrong
# answer key into the corpus, so they are excluded until upstream implements the rule.
#
# COFA cannot be represented at all - the engine's enum has no such value - so that
# eligible category is simply unavailable.
SAFE_IMMIGRATION_STATUSES = (
    "CITIZEN",                    # eligible under both
    "LEGAL_PERMANENT_RESIDENT",   # eligible under both (5-year bar NOT modelled; see LIMITS 16)
    "CUBAN_HAITIAN_ENTRANT",      # eligible under both
    "UNDOCUMENTED",               # ineligible under both
    "DACA",                       # ineligible under both
    "TPS",                        # ineligible under both
)

# Excluded, with the reason, so the exclusion is auditable rather than implicit.
UNSAFE_IMMIGRATION_STATUSES = {
    "REFUGEE": "HR 1 removed eligibility; engine still grants it",
    "ASYLEE": "HR 1 removed eligibility; engine still grants it",
    "DEPORTATION_WITHHELD": "HR 1 removed eligibility; engine still grants it",
    "CONDITIONAL_ENTRANT": "HR 1 removed eligibility; engine still grants it",
    "PAROLED_ONE_YEAR": "HR 1 removed eligibility; engine still grants it",
}


class ImmigrationStatus(str, Enum):
    """Mirrors the engine enum. Only SAFE_IMMIGRATION_STATUSES may be generated."""

    CITIZEN = "CITIZEN"
    LEGAL_PERMANENT_RESIDENT = "LEGAL_PERMANENT_RESIDENT"
    CUBAN_HAITIAN_ENTRANT = "CUBAN_HAITIAN_ENTRANT"
    UNDOCUMENTED = "UNDOCUMENTED"
    DACA = "DACA"
    TPS = "TPS"
    # Present so they can be named and excluded; never generated.
    REFUGEE = "REFUGEE"
    ASYLEE = "ASYLEE"
    DEPORTATION_WITHHELD = "DEPORTATION_WITHHELD"
    CONDITIONAL_ENTRANT = "CONDITIONAL_ENTRANT"
    PAROLED_ONE_YEAR = "PAROLED_ONE_YEAR"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------
# Household
# --------------------------------------------------------------------------------------


class DeclaredBenefit(Strict):
    """A benefit the narrative STATES the household receives.

    Distinct from a benefit the engine would impute. Declared receipt is visible to the
    agent and therefore fair game for the answer key; imputed receipt is not.
    Establishing receipt of SSI, SSDI or VA disability is what makes a household
    "disabled" for SNAP - a self-reported `is_disabled` flag does not.
    """

    program: str = Field(description="PolicyEngine variable name; see DECLARABLE_PROGRAMS")
    annual_amount: float = Field(description="US dollars per YEAR")


class Person(Strict):
    person_id: str
    age: int | None = Field(description="years; None means withheld, not zero")
    employment_income: float | None = Field(description="US dollars per YEAR; None means withheld")
    immigration_status: ImmigrationStatus | None = None
    is_disabled: bool | None = None
    declared_benefits: tuple[DeclaredBenefit, ...] = Field(
        default=(),
        description="benefits the narrative states this person receives; empty means none stated",
    )

    declared_statuses: tuple[str, ...] = Field(
        default=(),
        description="boolean status facts the narrative states, e.g. is_permanently_disabled_veteran",
    )

    @property
    def declared_annual_total(self) -> float:
        return sum(b.annual_amount for b in self.declared_benefits)

    def declarations(self) -> dict[str, float]:
        return {b.program: b.annual_amount for b in self.declared_benefits}

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
    dependent_care_cost: float | None = Field(
        default=0.0,
        description="US dollars per YEAR, spm_unit; the SNAP dependent care deduction. "
        "None means withheld.",
    )

    def withheld(self) -> list[str]:
        """Fact names withheld anywhere in this household, person-qualified."""
        out = ["housing_cost"] if self.housing_cost is None else []
        if self.dependent_care_cost is None:
            out.append("dependent_care_cost")
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
