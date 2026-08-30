"""Answer parsing, with malformed JSON kept strictly separate from a wrong answer.

SPEC.md 4: malformed JSON scores zero and is logged separately. The distinction matters
because the two failures mean different things - a model that cannot emit JSON has not
been measured on benefits reasoning at all, and averaging the two together would hide
that.

This module imports nothing from `verifiers`. It takes a string and returns a result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from redtape.schemas import T1Answer

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ParseFailure(str, Enum):
    NONE = "none"
    NO_JSON_FOUND = "no_json_found"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_INVALID = "schema_invalid"


@dataclass(frozen=True)
class ParseResult:
    answer: T1Answer | None
    failure: ParseFailure
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.answer is not None


def _candidates(text: str):
    """JSON candidates, most likely first: fenced blocks, then the last balanced object.

    Models routinely wrap JSON in prose or a code fence. Accepting those is not
    leniency about the answer - the schema is still strict - it is refusing to score
    formatting as if it were reasoning.
    """
    for m in _FENCE.finditer(text):
        yield m.group(1).strip()

    depth, start = 0, None
    spans = []
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append(text[start : i + 1])
    yield from reversed(spans)


def parse_answer(text: str) -> ParseResult:
    """Extract a T1Answer, or say precisely how the output failed."""
    if not text or not text.strip():
        return ParseResult(None, ParseFailure.NO_JSON_FOUND, "empty output")

    saw_json = False
    last_detail = ""
    for candidate in _candidates(text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            continue
        saw_json = True
        if not isinstance(payload, dict):
            last_detail = f"top-level JSON is {type(payload).__name__}, not an object"
            continue
        try:
            return ParseResult(T1Answer.model_validate(payload), ParseFailure.NONE)
        except ValidationError as exc:
            errs = exc.errors()[:3]
            last_detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs
            )
            continue

    if saw_json:
        return ParseResult(None, ParseFailure.SCHEMA_INVALID, last_detail)
    return ParseResult(
        None,
        ParseFailure.MALFORMED_JSON if last_detail else ParseFailure.NO_JSON_FOUND,
        last_detail or "no JSON object found in output",
    )
