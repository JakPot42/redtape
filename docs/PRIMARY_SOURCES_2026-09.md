# Primary sources read before the corpus fix (2026-09-19)

Read **before** any generator or oracle change, per the instruction that no rule is encoded
until someone has read it. Our measured citation error rate from memory is 30% (LIMITS §10).
Quotations are verbatim from the source text as fetched. The engine column is from reading
the installed `policyengine-us` 1.821.4 source, not its documentation.

**Headline: the engine matches the statute on every rule read. No upstream report follows.**
Every wrong answer key in LIMITS §36 traces to a default *our oracle* left unset
(`ssn_card_type`, `weekly_hours_worked_before_lsr`, `is_full_time_college_student`,
`has_tin`, the single-tax-unit spouse), not to an engine error.

---

## 1. EITC: IRC §32(c)(1)(E), §32(c)(3)(D), §32(m)

Source: Cornell LII, `uscode/text/26/32`. **Currency check:** the official US Code
(uscode.house.gov, prelim edition) shows 8 references to Pub. L. 119-21 in §24 and **none in
§32**, and Cornell's latest §32 amendment note is Pub. L. 117-2 (2021). So the 2025 act did
not amend §32 and this text is current for TY2025.

> **(c)(1)(E) Identification number requirement.** No credit shall be allowed under this
> section to an eligible individual who does not include on the return of tax for the taxable
> year— (i) such individual's taxpayer identification number, and (ii) if the individual is
> married, the taxpayer identification number of such individual's spouse.

> **(c)(3)(D)(i)** A qualifying child shall not be taken into account under subsection (b)
> unless the taxpayer includes the name, age, and TIN of the qualifying child on the return of
> tax for the taxable year.

> **(m)** Solely for purposes of subsections (c)(1)(E) and (c)(3)(D), a taxpayer
> identification number means a social security number issued to an individual by the Social
> Security Administration (other than a social security number issued pursuant to clause (II)
> (or that portion of clause (III) that relates to clause (II)) of section 205(c)(2)(B)(i) of
> the Social Security Act) on or before the due date for filing the return for the taxable year.

> Amendment note, 2021: Pub. L. 117-2, §9622(a), **struck out** subpar. (F), which read: "No
> credit shall be allowed under this section to any eligible individual who has one or more
> qualifying children if no qualifying child of such individual is taken into account under
> subsection (b) by reason of paragraph (3)(D)."

> **(d)(1)** In the case of an individual who is married, this section shall apply only if a
> joint return is filed for the taxable year.

**Reading.** An ITIN never qualifies, and neither does an SSN "not valid for employment"
(the SSA §205(c)(2)(B)(i)(II) kind). A married filer needs **both** spouses to hold work-valid
SSNs. A child without one is not counted, **but since 2021 the filer can still take the
childless credit** if otherwise eligible (age 25–64 etc.). A naive "child has no SSN → no
EITC" rule would be wrong.

**Engine.** `filer_meets_eitc_identification_requirements`: every head/spouse must satisfy
`meets_eitc_identification_requirements` (`ssn_card_type` ∈ {CITIZEN,
NON_CITIZEN_VALID_EAD}). `eitc_child_count` counts only children meeting the same test.
**Matches the statute, including the post-2021 childless fallback.** The defect is our
oracle's: `ssn_card_type` defaults to CITIZEN for everyone.

## 2. CTC: IRC §24(h)(7), as amended by Pub. L. 119-21 §70104

Source: uscode.house.gov, prelim edition. The amendment note says §70104's amendments "shall
apply to taxable years beginning after December 31, 2024", so they **apply to TY2025**.

> **(7) Social security number required. (A) In general.** No credit shall be allowed under
> this section to a taxpayer with respect to any qualifying child unless the taxpayer includes
> on the return of tax for the taxable year— (i) the taxpayer's social security number (or,
> in the case of a joint return, the social security number of at least 1 spouse), and (ii)
> the social security number of such qualifying child.
> **(B)** … "social security number" means a social security number issued to an individual
> by the Social Security Administration, but only if the social security number is issued—
> (i) to a citizen of the United States or pursuant to subclause (I) (or that portion of
> subclause (III) that relates to subclause (I)) of section 205(c)(2)(B)(i) of the Social
> Security Act, and (ii) before the due date for such return.

**Reading.** The CTC differs from the EITC in exactly one place that matters for married
couples: **one** spouse's work-valid SSN suffices, where the EITC needs both. The child must
have one too. A child without one does not generate CTC but may generate the $500 credit for
other dependents, which needs only a TIN (an ITIN will do).

**Engine.** `filer_meets_child_ctc_identification_requirements`: for 2025+
(`adult_ssn_requirement_applies` switches on 2025-01-01), every head/spouse must have *some*
TIN (`has_tin`), and **at least one** must have a valid SSN. Children: `ctc.eligible_ssn_card_type`
= {CITIZEN, NON_CITIZEN_VALID_EAD}. **Matches the statute.** The practical rule that every
filer needs some TIN to file at all comes from the Schedule 8812 instructions, which the engine
cites. **Unstated premise to close:** `has_tin` defaults to **True**, so the engine assumes an
ITIN for anyone without an SSN. The narrative must state it.

Still to verify by simulation during implementation: whether `ctc_value` includes the $500
other-dependent credit for a child without an SSN.

## 3. SNAP student rule: 7 CFR 273.5(a)–(b)

Source: Cornell LII, `cfr/text/7/273.5`.

> **(a)** An individual who is enrolled at least half-time in an institution of higher
> education shall be ineligible to participate in SNAP unless the individual qualifies for one
> of the exemptions contained in paragraph (b) of this section.

Exemptions that intersect the generator:

> **(b)(1)** Be age 17 or younger or age 50 or older;
> **(b)(2)** Be physically or mentally unfit;
> **(b)(5)** Be employed for a minimum of 20 hours per week and be paid for such employment …
> **(b)(6)** Be participating in a State or federally financed work study program …
> **(b)(8)** Be responsible for the care of a dependent household member under the age of 6;
> **(b)(9)** Be responsible for the care of a dependent household member who has reached the
> age of 6 but is under age 12 **when the State agency has determined that adequate child care
> is not available** …
> **(b)(10)** Be a single parent enrolled in an institution of higher education on a
> full-time basis … and be responsible for the care of a dependent child under age 12.
> (b)(3), (4), (7), (11): TANF, JOBS, on-the-job training, placement through an approved
> employment-and-training programme.

**Engine** (`is_snap_ineligible_student` and its inputs):

| exemption | engine | vs regulation |
|---|---|---|
| (b)(1) age | `age_threshold`: under 18 or 50+ | matches |
| (b)(2) unfit | `is_disabled` | matches (narratives state disability both ways) |
| (b)(5) 20 hrs paid | `weekly_hours_worked_before_lsr >= 20` | matches. **Our oracle never sets hours (default 0)** |
| (b)(6) work-study | `is_federal_work_study_participant` | matches. Narrative must state "not in work-study" |
| (b)(8) child under 6 | head/spouse with an SPM member under 6 | matches for v0's two shapes |
| (b)(9) child 6–11, no care | **"not modeled"** (the engine's own comment): treated as never met | a caseworker determination no narrative can supply, so it must be **stated** ("adequate child care is available") for the key to hold |
| (b)(10) single parent, full-time, child under 12 | reads `is_full_time_college_student` | matches. **Our oracle never sets the flag**, while narratives say "enrolled full-time", so the key denies an exemption the regulation grants |
| (b)(3)(4)(7)(11) programmes | `is_snap_employment_training_or_work_incentive_student`, TANF | narrative must state "not in any work-study, training or employment programme" |

California note, not yet read: CalFresh recognises additional state-approved programmes for
(b)(11) placement. Closing the narrative with "not in any … programme" makes the key
independent of that list.

## 4. Who receives which SSN: 20 CFR 422.104(a)

> We can assign you a social security number if … you are: (1) A United States citizen; or
> (2) An alien lawfully admitted to the United States for permanent residence or under other
> authority of law permitting you to work in the United States …; or (3) [a non-work SSN, only
> for a valid nonwork reason] … (b) … We will also mark your social security card with a
> legend such as "NOT VALID FOR EMPLOYMENT."

**Proposed mapping for the six generated statuses, stated in every narrative:**

| status | SSN stated in narrative | basis |
|---|---|---|
| CITIZEN | SSN (citizen) | 422.104(a)(1), read |
| LEGAL_PERMANENT_RESIDENT | SSN valid for work | 422.104(a)(2), read |
| REFUGEE, ASYLEE, CUBAN_HAITIAN_ENTRANT | SSN valid for work | 422.104(a)(2) *if* work-authorized. Work authorization incident to these statuses is 8 CFR 274a.12(a), **NOT read**. MEDIUM |
| UNDOCUMENTED | **no SSN; files with an ITIN** | no lawful work authority, so 422.104(a)(2) is unavailable |

Because the narrative **states** each person's SSN status, the answer key does not depend on
this mapping being legally exact. The mapping only governs how realistic the generated
combinations are. That is deliberate: it keeps an unread regulation out of the answer key.
