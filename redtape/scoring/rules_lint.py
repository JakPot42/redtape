"""Linter for rules/verification_requirements.yaml.

The rules table is the highest-risk artifact in the project (SPEC.md 6). The linter is
what keeps its failure modes mechanical rather than silent. It enforces:

* every rule carries a citation and a summary;
* every rule declares a confidence, from a closed set;
* NOTHING is at `high` unless the human reviewer signed it off in REVIEW_CHECKLIST.md -
  Claude never promotes a rule's confidence on its own;
* `low` rules are flagged as excluded from scoring;
* ids are unique and well-formed;
* `applies_when` parses under the small condition language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[A-Z]+(-[A-Z]+)*-\d{2}$")
PROGRAMS = {"snap", "medicaid", "eitc", "ctc"}
CONFIDENCES = {"low", "medium", "high"}
SUBJECTS = {
    "household",
    "applicant",
    "all_members",
    "member_with_earned_income",
    "member_non_citizen",
    "member_disabled",
}
OPERATORS = {"gt", "lt", "gte", "lte", "eq", "not_eq"}
SCOPES = {"always", "household", "any_member", "all_members"}


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_rules: int = 0
    by_confidence: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def scored_rules(self) -> int:
        """Rules eligible for scoring: everything not at `low`."""
        return self.n_rules - self.by_confidence.get("low", 0)

    def render(self) -> str:
        lines = [
            f"rules: {self.n_rules}",
            "confidence: "
            + ", ".join(f"{k}={self.by_confidence.get(k, 0)}" for k in ("high", "medium", "low")),
            f"scored (excludes low): {self.scored_rules}",
        ]
        for e in self.errors:
            lines.append(f"  ERROR   {e}")
        for w in self.warnings:
            lines.append(f"  WARN    {w}")
        lines.append("PASS" if self.ok else "FAIL")
        return "\n".join(lines)


def _check_condition(rid: str, cond: dict, rep: LintReport) -> None:
    if not isinstance(cond, dict) or not cond:
        rep.errors.append(f"{rid}: applies_when must be a non-empty mapping")
        return
    for scope, body in cond.items():
        if scope not in SCOPES:
            rep.errors.append(f"{rid}: unknown applies_when scope {scope!r}")
            continue
        if scope == "always":
            if body is not True:
                rep.errors.append(f"{rid}: applies_when.always must be literally true")
            continue
        if not isinstance(body, dict) or not body:
            rep.errors.append(f"{rid}: applies_when.{scope} must be a non-empty mapping")
            continue
        for fname, test in body.items():
            if not isinstance(test, dict) or len(test) != 1:
                rep.errors.append(f"{rid}: {scope}.{fname} must be a single-operator mapping")
                continue
            (op,) = test
            if op not in OPERATORS:
                rep.errors.append(f"{rid}: {scope}.{fname} unknown operator {op!r}")


def lint(path: Path, approved_high: set[str] | None = None) -> LintReport:
    """Lint the table. `approved_high` is the set of ids the reviewer signed off."""
    rep = LintReport()
    approved_high = approved_high or set()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "rules" not in data:
        rep.errors.append("file must be a mapping with a top-level `rules` key")
        return rep

    rules = data["rules"] or []
    rep.n_rules = len(rules)
    seen: set[str] = set()

    for i, r in enumerate(rules):
        rid = r.get("id", f"<rule #{i}>")

        if not ID_RE.match(str(rid)):
            rep.errors.append(f"{rid}: id must look like SNAP-INC-01")
        if rid in seen:
            rep.errors.append(f"{rid}: duplicate id")
        seen.add(rid)

        if r.get("program") not in PROGRAMS:
            rep.errors.append(f"{rid}: program must be one of {sorted(PROGRAMS)}")
        if r.get("subject") not in SUBJECTS:
            rep.errors.append(f"{rid}: subject {r.get('subject')!r} not in {sorted(SUBJECTS)}")

        if not str(r.get("citation", "")).strip():
            rep.errors.append(f"{rid}: missing citation")
        if not str(r.get("summary", "")).strip():
            rep.errors.append(f"{rid}: missing summary")

        conf = r.get("confidence")
        if conf not in CONFIDENCES:
            rep.errors.append(f"{rid}: confidence must be one of {sorted(CONFIDENCES)}")
        else:
            rep.by_confidence[conf] = rep.by_confidence.get(conf, 0) + 1
            if conf == "high" and rid not in approved_high:
                rep.errors.append(
                    f"{rid}: confidence `high` without reviewer sign-off in REVIEW_CHECKLIST.md "
                    "- only the human reviewer promotes a rule"
                )
            if conf == "low":
                rep.warnings.append(f"{rid}: `low` confidence, excluded from scoring until promoted")

        docs = r.get("acceptable_documents")
        if not isinstance(docs, list) or not docs:
            rep.errors.append(f"{rid}: acceptable_documents must be a non-empty list")
        else:
            for d in docs:
                for key in ("type", "issuer", "count"):
                    if key not in d:
                        rep.errors.append(f"{rid}: document entry missing {key!r}")

        _check_condition(str(rid), r.get("applies_when", {}), rep)

    return rep


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0]) if argv else Path("rules/verification_requirements.yaml")
    rep = lint(path)
    print(rep.render())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
