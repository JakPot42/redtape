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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Facts that may be withheld to create a T1b case. Names match the PolicyEngine
# input variable exactly, so provenance is unambiguous.
WITHHOLDABLE_FACTS = (
    "employment_income",
    "declared_benefits",
    "dependent_care_cost",
    "is_higher_ed_student",
    "immigration_status",
    "housing_cost",
    "age",
    "is_disabled",
)


# --------------------------------------------------------------------------------------
# Corpus scope. The immigration-status gate below is valid ONLY for this scope.
# --------------------------------------------------------------------------------------
# These are not decoration. `SAFE_IMMIGRATION_STATUSES` is safe *because* the corpus is
# California-only and 2025-only; the same statuses are NOT safe in another state or
# another year. `tests/test_immigration_scope.py` fails if either constant moves without
# the gate being re-derived. See docs/LIMITS.md 16 for why this guard exists.
CORPUS_STATE = "CA"
CORPUS_TAX_YEAR = 2025


# Statuses whose engine answer key is CORRECT for the corpus scope (CORPUS_STATE,
# CORPUS_TAX_YEAR).
#
# PL 119-21 section 10108 (enacted 2025-07-04) restricted SNAP to citizens, LPRs (after a
# five-year wait where applicable), Cuban/Haitian entrants, and COFA residents.
# policyengine-us 1.821.4 DOES implement this, in two layers:
#
#   gov/usda/snap/eligibility/eligible_immigration_statuses.yaml     2025-07-01 restricts
#   gov/states/ca/cdss/snap/eligibility/...statuses.yaml             2026-04-01 for CA
#
# California delays the restriction to 2026-04-01 per CDSS ACL 25-92, and
# `is_snap_immigration_status_eligible` returns `federal_eligible | ca_eligible`, where
# `ca_snap_immigration_status_eligible` is `defined_for = StateCode.CA`. So in California
# in 2025 every previously-eligible status is STILL eligible, and the engine is right to
# say so. Verified against the engine at all four boundary cells by
# `scripts/probe_immigration_state_scope.py`; pinned by tests.
#
# This is why the set is scope-conditional rather than absolute. In Texas the same five
# statuses go ineligible at 2025-07; in California they go ineligible at 2026-04. Either
# change to CORPUS_STATE or CORPUS_TAX_YEAR invalidates the reasoning here.
#
# COFA remains unrepresentable - the engine's enum has no such value, and the federal
# 2025-07-01 layer carries COFA only as a YAML comment - so that eligible category cannot
# be expressed as an input. Tracked upstream as PolicyEngine/policyengine-us#8296.
SAFE_IMMIGRATION_STATUSES = (
    "CITIZEN",                    # eligible; unaffected by HR 1
    "LEGAL_PERMANENT_RESIDENT",   # eligible; 5-year bar not applied to SNAP (see LIMITS 16)
    "CUBAN_HAITIAN_ENTRANT",      # eligible; retained by HR 1
    "REFUGEE",                    # eligible in CA until 2026-04-01 (ACL 25-92)
    "ASYLEE",                     # eligible in CA until 2026-04-01 (ACL 25-92)
    "DEPORTATION_WITHHELD",       # eligible in CA until 2026-04-01 (ACL 25-92)
    "CONDITIONAL_ENTRANT",        # eligible in CA until 2026-04-01 (ACL 25-92)
    "PAROLED_ONE_YEAR",           # eligible in CA until 2026-04-01 (ACL 25-92)
    "UNDOCUMENTED",               # ineligible
    "DACA",                       # ineligible
    "TPS",                        # ineligible
)

# Kept as a mechanism, deliberately empty. Nothing is excluded within the current corpus
# scope. Populate it - do not delete it - if a scope change makes a status wrong again;
# the disjoint/complete test is what forces a new engine status to be classified.
UNSAFE_IMMIGRATION_STATUSES: dict[str, str] = {}


class ImmigrationStatus(str, Enum):
    """Mirrors the engine enum. Only SAFE_IMMIGRATION_STATUSES may be generated."""

    CITIZEN = "CITIZEN"
    LEGAL_PERMANENT_RESIDENT = "LEGAL_PERMANENT_RESIDENT"
    CUBAN_HAITIAN_ENTRANT = "CUBAN_HAITIAN_ENTRANT"
    UNDOCUMENTED = "UNDOCUMENTED"
    DACA = "DACA"
    TPS = "TPS"
    REFUGEE = "REFUGEE"
    ASYLEE = "ASYLEE"
    DEPORTATION_WITHHELD = "DEPORTATION_WITHHELD"
    CONDITIONAL_ENTRANT = "CONDITIONAL_ENTRANT"
    PAROLED_ONE_YEAR = "PAROLED_ONE_YEAR"


# SSN held, by immigration status. docs/PRIMARY_SOURCES_2026-09.md §4. Values:
#   "citizen"  - an SSN issued to a citizen (20 CFR 422.104(a)(1))
#   "work"     - an SSN valid for work: LPRs and noncitizens with work authority
#                (422.104(a)(2)). Work authority incident to refugee/asylee/entrant/parolee
#                status is 8 CFR 274a.12(a), NOT READ - MEDIUM. Stated in every narrative,
#                so the answer key does not depend on this mapping being exact.
#   "itin"     - no SSN; files with an ITIN. Undocumented: no lawful work authority.
# Covers EVERY engine status, not only the generated ones, because the prober sweeps all
# of them; a missing key must crash, never default.
SSN_BY_STATUS = {
    "CITIZEN": "citizen",
    "LEGAL_PERMANENT_RESIDENT": "work",
    "CUBAN_HAITIAN_ENTRANT": "work",
    "REFUGEE": "work",
    "ASYLEE": "work",
    "DEPORTATION_WITHHELD": "work",
    "CONDITIONAL_ENTRANT": "work",
    "PAROLED_ONE_YEAR": "work",
    "DACA": "work",
    "TPS": "work",
    "UNDOCUMENTED": "itin",
}

# The two household shapes v0 generates (decision A, 2026-09-19). Relationships are
# EXPLICIT: the generator produces them, the oracle builds from them, the narrative states
# them, and neither the oracle nor the engine is allowed to infer one. Adult children,
# unmarried partners and roommates are v1: dependency tests and SNAP's
# purchase-and-prepare rule are their own project.
#   single_adult    - p1 is the only adult; every other member is p1's own child.
#   married_couple  - p1 and p2 are married and file jointly; every other member is their
#                     own child.
HOUSEHOLD_TYPES = ("single_adult", "married_couple")


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
    is_higher_ed_student: bool | None = Field(
        default=False,
        description="enrolled more than half-time in higher education (7 CFR 273.5). "
        "Produces an ELIGIBILITY flip for SNAP, unlike most facts. None means withheld.",
    )
    declared_benefits: tuple[DeclaredBenefit, ...] = Field(
        default=(),
        description="benefits the narrative states this person receives; empty means none stated",
    )

    declared_statuses: tuple[str, ...] = Field(
        default=(),
        description="boolean status facts the narrative states, e.g. is_permanently_disabled_veteran",
    )

    # Added 2026-09-19 (LIMITS §36). Each was an engine DEFAULT the answer key silently
    # rested on: 0 weekly hours for everyone (the SNAP student exemption, 7 CFR
    # 273.5(b)(5), reads it) and part-time enrolment for everyone (273.5(b)(10) reads it).
    weekly_hours: float | None = Field(
        default=0.0,
        description="average paid hours per week. Withheld TOGETHER with employment_income: "
        "stated hours would reveal withheld earnings. None means withheld.",
    )
    student_full_time: bool | None = Field(
        default=None,
        description="for a higher-education student, True = full-time, False = at least "
        "half-time but not full-time. None for non-students, and when student status is "
        "withheld.",
    )
    status_since: int | None = Field(
        default=None,
        description="calendar year the person's lawful immigration status began. None for "
        "citizens, for the undocumented, and when immigration_status is withheld (they are "
        "one fact). Generated at least FIVE_YEAR_BAR years before the tax year so the SNAP "
        "five-year bar (8 U.S.C. 1613) cannot bite - the engine does not model the bar, so "
        "an LPR whose status began recently would carry a legally wrong answer key "
        "(docs/LIMITS.md 39).",
    )

    @property
    def ssn_status(self) -> str | None:
        """Derived from immigration status, never generated independently, so withholding
        the status withholds this too. The mapping is docs/PRIMARY_SOURCES_2026-09.md §4
        (20 CFR 422.104). The narrative STATES the result, so the answer key never rests
        on the mapping being legally exact."""
        if self.immigration_status is None:
            return None
        return SSN_BY_STATUS[self.immigration_status.value]

    @property
    def declared_annual_total(self) -> float:
        return sum(b.annual_amount for b in self.declared_benefits)

    def declarations(self) -> dict[str, float]:
        return {b.program: b.annual_amount for b in self.declared_benefits}

    def withheld(self) -> list[str]:
        return [
            f
            for f in ("age", "employment_income", "immigration_status", "is_disabled",
                      "is_higher_ed_student")
            if getattr(self, f) is None
        ]


class Household(Strict):
    household_id: str
    seed: int
    index: int

    # Kept as literals so the type system pins them; CORPUS_STATE / CORPUS_TAX_YEAR are
    # the values the immigration gate is derived against, and a test ties the two together.
    state: Literal["CA"] = "CA"
    tax_year: int = CORPUS_TAX_YEAR
    month: str = Field(description='the SNAP month, "YYYY-MM"; SNAP is always scored monthly')

    people: tuple[Person, ...]
    household_type: Literal["single_adult", "married_couple"] = Field(
        description="see HOUSEHOLD_TYPES. p1 (and p2 if married) are the adults; everyone "
        "else is their own child and their dependent."
    )
    pays_heating_cooling: bool = Field(
        default=True,
        description="pays heating or cooling costs. Was set True by the oracle for every "
        "household without ever being stated (LIMITS §36); now a stated fact.",
    )
    housing_cost: float | None = Field(description="US dollars per YEAR, spm_unit; None means withheld")
    dependent_care_cost: float | None = Field(
        default=0.0,
        description="US dollars per YEAR, spm_unit; the SNAP dependent care deduction. "
        "None means withheld.",
    )

    @property
    def adults(self) -> tuple[Person, ...]:
        return self.people[: 2 if self.household_type == "married_couple" else 1]

    @property
    def children(self) -> tuple[Person, ...]:
        return self.people[len(self.adults):]

    @model_validator(mode="after")
    def _structure_is_explicit(self):
        """Structure is positional and checked, never inferred. Ages are NOT checked here:
        the prober restores swept ages, and a plausibility rule belongs to the generator
        (tests/test_household_structure.py), not to every intermediate object."""
        n_adults = 2 if self.household_type == "married_couple" else 1
        if len(self.people) < n_adults:
            raise ValueError(f"{self.household_type} needs {n_adults} adult(s)")
        ids = [p.person_id for p in self.people]
        if ids != [f"p{i + 1}" for i in range(len(ids))]:
            raise ValueError(f"person ids must be p1..pN in order, got {ids}")
        return self

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
    eligible: bool | None = Field(
        default=None,
        description="null ONLY when snap is listed in cannot_determine",
    )
    benefit: float | None = Field(
        default=None,
        description="US dollars for that MONTH, never annualized. null ONLY when snap "
                    "is listed in cannot_determine",
    )


class MedicaidAnswer(Strict):
    """UNSCORED in v0.

    Medicaid MAGI eligibility has NO external validation - we found no reachable
    published source against which to check it, and unlike SNAP/EITC/CTC the
    parameters are not published as a simple table. Rather than ship a scored
    benchmark cell backed only by the engine agreeing with itself, v0 computes and
    records Medicaid with full provenance but excludes it from scoring.

    This is a scope limit, not a defect. See docs/LIMITS.md 20.
    """

    period: Literal["year"] = "year"
    period_label: str
    person_eligible: dict[str, bool]
    scored: Literal[False] = Field(
        default=False,
        description="v0 does not score Medicaid; no external validation exists",
    )


class AnnualAmount(Strict):
    period: Literal["year"] = "year"
    period_label: str
    amount: float | None = Field(
        default=None,
        description="the amount the household RECEIVES, not a gross entitlement. null "
                    "ONLY when this program is listed in cannot_determine",
    )
    gross_entitlement: float | None = Field(
        default=None,
        description="pre-limitation credit where it differs from the amount received; "
        "CTC is $2,200/child gross but is limited by tax liability and the $1,700 "
        "refundable cap, so a zero-income family is entitled to $4,400 and receives $0",
    )


class Determinability(str, Enum):
    """Three-class T1b label. See CLAUDE.md."""

    DETERMINATE = "determinate"                     # complete facts; answer normally
    INDETERMINATE = "indeterminate"                 # a withheld fact flips the outcome; abstain
    INCOMPLETE_DETERMINATE = "incomplete_determinate"  # fact withheld but outcome unchanged; answer


# The closed vocabulary `missing_fact` must use (2026-09-19). Rendered into the prompt by
# redtape.envs.t1_eligibility.fact_vocabulary(), and matched EXACTLY by score_abstention.
#
# It lists EVERY fact field a case file states, not only the ones the generator can withhold:
# listing only the withholdable six would tell the model where to look. And the prompt's
# worked example no longer names a real fact. It used to be `p1.employment_income`, which
# was also the fact Opus 5 flagged most often (0.709), so "the prompt named it" was a
# confound on the per-fact table (README; LIMITS §35).
#
# Person facts are written `p<N>.<field>`; household facts by bare name. A model that
# believes something outside this list is missing writes `other: <description>`, which
# never matches a withheld fact and is counted separately (confabulated or unstated).
# tests/test_fact_vocabulary.py ties both tuples to the model fields.
PERSON_FACTS = (
    "age", "employment_income", "weekly_hours", "immigration_status", "ssn_status",
    "status_since", "is_disabled", "is_higher_ed_student", "student_full_time",
    "declared_benefits",
)

# The SNAP five-year bar for qualified non-citizens (8 U.S.C. 1613). The engine does not
# model it, so the corpus keeps every lawful status old enough that it cannot apply, and the
# narrative states the year. See docs/LIMITS.md 39 for why stating it beats the alternatives.
FIVE_YEAR_BAR = 5

# Statuses that have a lawful start year to state. Citizens have none; the undocumented have
# no lawful status at all.
STATUSES_WITH_START_YEAR = tuple(
    s for s in SSN_BY_STATUS if s not in ("CITIZEN", "UNDOCUMENTED")
)
HOUSEHOLD_FACTS = (
    "housing_cost", "dependent_care_cost", "pays_heating_cooling", "household_type",
)
OTHER_FACT_PREFIX = "other:"


class CannotDetermine(Strict):
    program: Literal["snap", "medicaid", "eitc", "ctc"]
    missing_fact: str = Field(
        description='exactly one identifier from the fact list, e.g. "p<N>.<fact>" or a '
        'household fact name; or "other: <description>"'
    )


# Programs whose values are SCORED in v0. Medicaid is deliberately absent.
SCORED_PROGRAMS = ("snap", "eitc", "ctc")


class T1Answer(Strict):
    """The scored answer.

    A scored program's value may be `null` **only** when that program appears in
    `cannot_determine`. That is not a loosening of the schema, it is the schema finally
    agreeing with the task: the prompt tells the model to abstain "instead of guessing", and
    the previous version then required a number anyway. A model that abstained correctly -
    naming the program and the missing fact - had to invent a figure it had just said it
    could not determine, or be rejected as malformed.

    It was rejected. On the first 1,200-task run every one of the 47 `schema_invalid`
    responses was rejected for exactly this, and 34 of them were abstentions that would
    otherwise have scored CORRECT. The benchmark was penalising the behaviour it exists to
    reward. See docs/LIMITS.md 27.

    A null WITHOUT a matching abstention is still rejected, so this cannot be used to skip
    an answer silently.
    """

    snap: SnapAnswer
    medicaid: MedicaidAnswer
    eitc: AnnualAmount
    ctc: AnnualAmount
    cannot_determine: tuple[CannotDetermine, ...] = ()

    @model_validator(mode="after")
    def _null_requires_abstention(self):
        """A missing value must be accompanied by an explicit abstention for that program."""
        abstained = {c.program for c in self.cannot_determine}
        missing = []
        if (self.snap.benefit is None or self.snap.eligible is None) \
                and "snap" not in abstained:
            missing.append("snap")
        if self.eitc.amount is None and "eitc" not in abstained:
            missing.append("eitc")
        if self.ctc.amount is None and "ctc" not in abstained:
            missing.append("ctc")
        if missing:
            raise ValueError(
                f"null value for {missing} without listing it in cannot_determine. A "
                f"program may be left null only when the answer explicitly abstains on it."
            )
        return self


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
