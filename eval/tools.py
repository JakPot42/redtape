"""The PolicyEngine calculator, exposed as a tool for the tool-equipped conditions.

SPEC.md 5 asks for a tool-equipped upper bound: the gap between tool-less and
tool-equipped runs separates *intake* errors (reading the case file wrong) from
*arithmetic* errors (computing the benefit wrong). For that gap to mean anything, the tool
has to take the household as a **structured record** and do the engine marshalling itself.
If the model had to build a PolicyEngine situation dict, the condition would measure
knowledge of PolicyEngine's entity model rather than the reasoning we care about.

Three conditions, and the third is the one that matters for the abstention claim:

1. `tool_less`               - no tool. The headline condition.
2. `tool_equipped`           - `calculate_benefits` takes a complete household record and
                               returns the scored answer. Upper bound on arithmetic.
3. `tool_equipped_unknowns`  - the same tool, except a fact may be passed as the string
                               `"unknown"`. Rather than defaulting it - which is what
                               PolicyEngine itself does, silently (LIMITS 3) - the tool
                               sweeps that fact and reports which programs its value
                               decides. This is the upper bound on *determinability*: it
                               hands the model exactly the check our labeller runs, so the
                               remaining error is the model's judgement about what to ask.

The tool never sees the answer key. It computes from what the model passes it, so a model
that mis-reads the narrative gets a confidently wrong number back - which is the intake
error we are trying to isolate, not a leak.
"""

from __future__ import annotations

import json

from redtape.oracle.determinability import SWEEPS, probe
from redtape.oracle.policyengine_oracle import compute
from redtape.schemas import Household, ImmigrationStatus, Person

UNKNOWN = "unknown"

TOOL_DESCRIPTION = (
    "Compute SNAP, EITC and CTC for a California household using the PolicyEngine rules "
    "engine. Pass the household exactly as the case file describes it. SNAP is returned "
    "for the stated month; EITC and CTC are annual for the tax year."
)

UNKNOWN_TOOL_DESCRIPTION = TOOL_DESCRIPTION + (
    " Any field whose value the case file does not state may be passed as the string "
    '"unknown". The tool will then report which programs that fact decides, instead of '
    "silently assuming a value for it."
)


def _person(raw: dict, i: int) -> tuple[Person, list[str]]:
    """Build a Person, returning the names of any fields marked unknown."""
    unknown = []
    fields: dict = {"person_id": raw.get("person_id") or f"p{i + 1}"}

    for name, caster in (
        ("age", int),
        ("employment_income", float),
        ("is_higher_ed_student", bool),
        ("is_disabled", bool),
    ):
        value = raw.get(name)
        if value == UNKNOWN:
            unknown.append(f"{fields['person_id']}.{name}")
            fields[name] = None
        elif value is not None:
            fields[name] = caster(value)

    status = raw.get("immigration_status")
    if status == UNKNOWN:
        unknown.append(f"{fields['person_id']}.immigration_status")
        fields["immigration_status"] = None
    elif status:
        fields["immigration_status"] = ImmigrationStatus(str(status).upper())
    else:
        fields["immigration_status"] = ImmigrationStatus.CITIZEN

    fields.setdefault("is_disabled", False)
    fields.setdefault("is_higher_ed_student", False)
    return Person(**fields), unknown


def build_household(payload: dict) -> tuple[Household, list[str]]:
    people, unknown = [], []
    for i, raw in enumerate(payload.get("people", [])):
        person, missing = _person(raw, i)
        people.append(person)
        unknown.extend(missing)

    fields: dict = {
        "household_id": payload.get("household_id", "tool-call"),
        "seed": 0,
        "index": 0,
        "month": payload["month"],
        "tax_year": int(payload.get("tax_year", 2025)),
        "people": tuple(people),
    }
    for name in ("housing_cost", "dependent_care_cost"):
        value = payload.get(name)
        if value == UNKNOWN:
            unknown.append(name)
            fields[name] = None
        else:
            fields[name] = float(value or 0.0)

    return Household(**fields), unknown


def calculate(payload: dict, *, allow_unknown: bool = False) -> dict:
    """Run the engine on a model-supplied household record.

    With `allow_unknown`, a fact marked unknown is swept rather than defaulted, and the
    tool answers the determinability question instead of inventing a number.
    """
    try:
        hh, unknown = build_household(payload)
    except Exception as exc:
        return {"error": f"could not read the household record: {type(exc).__name__}: {exc}"}

    if unknown and not allow_unknown:
        return {
            "error": (
                "every fact must be given a value in this condition; "
                f"unspecified: {unknown}"
            )
        }

    if unknown:
        if len(unknown) > 1:
            return {
                "error": (
                    "mark at most one fact unknown per call; this tool sweeps one fact at "
                    f"a time. Received: {unknown}"
                )
            }
        fact = unknown[0]
        key = fact.partition(".")[2] or fact
        if key not in SWEEPS:
            return {"error": f"no declared sweep range for {key!r}; cannot test it"}
        label = probe(hh, fact)
        return {
            "withheld_fact": fact,
            "values_tested": list(label.sweep_values),
            "decides_programs": list(label.deciding_programs),
            "determinable": not label.deciding_programs,
            "note": (
                "decides_programs lists the SCORED programs whose outcome changes across "
                "the tested range. If it is empty, the fact does not change any scored "
                "answer and you should answer normally; supply a value and call again."
            ),
        }

    result = compute(hh)
    answer = result.answer
    return {
        "snap": {"period": answer.snap.period_label, "eligible": answer.snap.eligible,
                 "monthly_benefit": answer.snap.benefit},
        "eitc": {"period": answer.eitc.period_label, "annual_amount": answer.eitc.amount},
        "ctc": {"period": answer.ctc.period_label, "annual_amount_received": answer.ctc.amount,
                "gross_entitlement": answer.ctc.gross_entitlement},
        "engine_version": result.engine_version,
    }


# The JSON schema handed to the model. Written out rather than generated from the pydantic
# model so the wording the model sees is a deliberate choice, not a serialisation artifact.
def tool_schema(*, allow_unknown: bool) -> dict:
    unknown_note = ' Pass the string "unknown" if the case file does not state it.'
    def described(base: str) -> str:
        return base + (unknown_note if allow_unknown else "")

    person = {
        "type": "object",
        "properties": {
            "person_id": {"type": "string", "description": 'e.g. "p1"'},
            "age": {"description": described("Age in years.")},
            "employment_income": {"description": described("Annual employment income, USD.")},
            "immigration_status": {
                "description": described(
                    "One of CITIZEN, LEGAL_PERMANENT_RESIDENT, CUBAN_HAITIAN_ENTRANT, "
                    "UNDOCUMENTED, DACA, TPS."
                )
            },
            "is_higher_ed_student": {
                "description": described(
                    "Enrolled more than half-time in higher education (7 CFR 273.5)."
                )
            },
            "is_disabled": {"description": described("Self-reported disability.")},
        },
        "required": ["person_id"],
    }
    return {
        "name": "calculate_benefits",
        "description": UNKNOWN_TOOL_DESCRIPTION if allow_unknown else TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": 'SNAP month, "YYYY-MM".'},
                "tax_year": {"type": "integer"},
                "people": {"type": "array", "items": person},
                "housing_cost": {"description": described("Annual shelter cost, USD.")},
                "dependent_care_cost": {"description": described("Annual dependent care cost, USD.")},
            },
            "required": ["month", "people"],
        },
    }


def run_tool(raw_input, *, allow_unknown: bool) -> str:
    payload = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
    return json.dumps(calculate(payload, allow_unknown=allow_unknown))
