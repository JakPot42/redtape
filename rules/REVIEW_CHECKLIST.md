# Rules table review checklist

One line per rule. **Only the human reviewer promotes a rule's confidence.** Claude
drafts at `medium` (or `low` where the legal meaning is uncertain) and never promotes
on its own — the linter fails the build if a rule sits at `high` without a sign-off
recorded here.

To sign off a rule: read the cited section, confirm the rule states what the citation
says, then tick the box and add the id to `approved_high` where the linter is invoked.

**Phase 1 status: this is a ten-rule seed set whose purpose is to prove the format.**
It is not the finished table and should not be reviewed as if it were. The full table
is its own phase with its own checkpoint.

## How to read the confidence column

- `high` — reviewer has read the citation and confirms the rule. **Scored.**
- `medium` — drafted from the primary source, not yet reviewed. **Scored.**
- `low` — the legal meaning is genuinely uncertain, or the federal rule may be
  modified by the California manual in ways not yet checked. **Excluded from scoring
  until promoted.** May still appear in narratives.

## Checklist

| ☐ | id | program | confidence | citation | what to check |
|---|----|---------|-----------|----------|----------------|
| ☐ | SNAP-INC-01 | snap | medium | 7 CFR 273.2(f)(1)(i) | Confirm gross nonexempt income is mandatory-verify at certification, and that a 30-day pay-stub window is what CA actually applies. |
| ☐ | SNAP-ALIEN-01 | snap | medium | 7 CFR 273.2(f)(1)(ii) | Confirm which statuses require documentary proof, and how ineligible non-citizens affect the benefit rather than eligibility. |
| ☐ | SNAP-RES-01 | snap | medium | 7 CFR 273.2(f)(1)(vi) | Confirm residency is mandatory-verify and that no specific document is federally required. Check whether CA narrows this. |
| ☐ | SNAP-ID-01 | snap | medium | 7 CFR 273.2(f)(1)(vii) | Confirm identity applies to applicant or authorized representative, not every member. |
| ☐ | SNAP-SHELTER-01 | snap | **low** | 7 CFR 273.2(f)(3) | **Uncertain.** Drafted as always-required when a shelter cost is claimed, but the regulation frames it as verify-when-questionable. Decide which the benchmark should encode. |
| ☐ | SNAP-UTIL-01 | snap | medium | 7 CFR 273.2(f)(1)(iv) | Confirm the trigger is claiming a utility allowance, and check CA's standard utility allowance rules. |
| ☐ | SNAP-DIS-01 | snap | medium | 7 CFR 273.2(f)(1)(v) | Confirm which disability determinations require verification and which are established by receipt of another benefit. |
| ☐ | SNAP-SSN-01 | snap | **low** | 7 CFR 273.2(f)(1)(v) | **Uncertain.** Citation may be wrong — SSN requirements may sit at 273.6 rather than 273.2(f). Verify the section before promoting. |
| ☐ | MEDI-INC-01 | medicaid | **low** | 42 CFR 435.945; CA MPP 50167 | **Uncertain.** California verifies income substantially through electronic data sources; a paper-document requirement may misstate practice. |
| ☐ | MEDI-AGE-01 | medicaid | **low** | 42 CFR 435.407 | **Uncertain.** Cited section concerns citizenship/identity documentation; whether it is the right authority for age is unconfirmed. |

## Composition at time of writing

- 10 rules total
- high: 0 · medium: 6 · low: 4
- scored (excludes `low`): 6

The README must state this composition at release time (SPEC.md §6).
