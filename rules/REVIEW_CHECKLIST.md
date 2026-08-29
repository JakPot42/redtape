# Rules table review checklist

One line per rule. **Only the human reviewer promotes a rule's confidence.** Claude
drafts at `medium` (or `low` where the legal meaning is uncertain) and never promotes on
its own — the linter fails the build if a rule sits at `high` without a sign-off
recorded here.

To sign off a rule: read the cited section, confirm the rule states what the citation
says, then tick the box and add the id to `approved_high` where the linter is invoked.

**Phase 1 status: this is a ten-rule seed set whose purpose is to prove the format.**
It is not the finished table and should not be reviewed as if it were. The full table is
its own phase with its own checkpoint.

## Citation corrections applied 2026-08-29

Three citations were wrong and have been corrected against the regulation text
(Cornell LII, `law.cornell.edu/cfr/text/7/273.2` and `/273.6`). Details in
`docs/LIMITS.md` §10. **No confidence level was changed** — these are corrections, not
promotions.

| rule | was | now |
|---|---|---|
| SNAP-UTIL-01 | 273.2(f)(1)(iv) *(medical expenses)* | **273.2(f)(1)(iii)** *(utility expenses)* |
| SNAP-DIS-01 | 273.2(f)(1)(v) *(SSN verification)* | **273.2(f)(1)(viii)** *(disability)* |
| SNAP-SSN-01 | 273.2(f)(1)(v) | **273.6** *(the substantive SSN requirement)* |

Only SNAP-SSN-01 had been flagged as doubtful. The other two were believed correct and
were not — which is the case for never accepting an unread citation.

## How to read the confidence column

- `high` — reviewer has read the citation and confirms the rule. **Scored.**
- `medium` — drafted from the primary source, not yet reviewed. **Scored.**
- `low` — the legal meaning is genuinely uncertain, or the federal rule may be modified
  by the California manual in ways not yet checked. **Excluded from scoring until
  promoted.** May still appear in narratives.

## Checklist

| ☐ | id | program | confidence | citation | what to check |
|---|----|---------|-----------|----------|----------------|
| ☐ | SNAP-INC-01 | snap | medium | 7 CFR 273.2(f)(1)(i) | Citation verified as "gross nonexempt income". Confirm the 30-day pay-stub window is what CA actually applies. |
| ☐ | SNAP-ALIEN-01 | snap | medium | 7 CFR 273.2(f)(1)(ii) | Citation verified as "alien eligibility". Confirm which statuses need documentary proof, and how ineligible non-citizens affect the benefit rather than eligibility. |
| ☐ | SNAP-RES-01 | snap | medium | 7 CFR 273.2(f)(1)(vi) | Citation verified as "residency". Confirm no specific document is federally required, and check whether CA narrows this. |
| ☐ | SNAP-ID-01 | snap | medium | 7 CFR 273.2(f)(1)(vii) | Citation verified as "identity". Confirm it applies to the applicant or authorized representative, not every member. |
| ☐ | SNAP-SHELTER-01 | snap | **low** | 7 CFR 273.2(f)(3) | **Uncertain, unchanged.** Drafted as always-required when a shelter cost is claimed, but the regulation frames it as verify-when-questionable. Decide which the benchmark should encode. |
| ☐ | SNAP-UTIL-01 | snap | medium | 7 CFR 273.2(f)(1)(iii) | **Citation corrected.** Now points at utility expenses. Confirm the trigger is claiming an allowance above the state standard, and check CA's SUA/LUA rules ($645 / $166 for FFY2025). |
| ☐ | SNAP-DIS-01 | snap | medium | 7 CFR 273.2(f)(1)(viii) | **Citation corrected.** Now points at disability. Confirm which determinations need verification and which are established by receipt of another benefit. |
| ☐ | SNAP-SSN-01 | snap | **low** | 7 CFR 273.6 | **Citation corrected and now verified.** §273.6 is headed "Social security numbers" and requires each member to provide or apply for an SSN before certification — which is what the rule says. **Promotion candidate**, but only you can promote it. |
| ☐ | MEDI-INC-01 | medicaid | **low** | 42 CFR 435.945; CA MPP 50167 | **Uncertain, unchanged.** California verifies income substantially through electronic data sources; a paper-document requirement may misstate practice. Note also that no Medicaid output has any external validation (`docs/LIMITS.md` §7). |
| ☐ | MEDI-AGE-01 | medicaid | **low** | 42 CFR 435.407 | **Uncertain, unchanged.** The cited section concerns citizenship/identity documentation; whether it is the right authority for age is unconfirmed. |

## Composition at time of writing

- 10 rules total
- high: 0 · medium: 6 · low: 4
- scored (excludes `low`): 6

The README must state this composition at release time (SPEC.md §6).
