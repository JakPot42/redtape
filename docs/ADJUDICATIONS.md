# Adjudications

Each entry records a case where a model's answer and the answer key disagreed, and how the
disagreement was settled.

**Rule: no entry is committed while its "Source opened" line is blank or only a candidate.**
A source is opened when the document itself was read and the sentence that settles the
question is quoted, with where it was found. A citation remembered, suggested by someone
else, or copied from another of our documents is a candidate, not a source (LIMITS §48).

Status values:

- **draft**: source opened and quoted, resolution written, awaiting review.
- **open**: the disagreement is recorded, but the source is not yet opened or the resolution
  is undecided.
- **blank**: placeholder only.

---

## A1. The schema rejected correct abstentions

- **Status:** draft
- **Disagreement:** on the first 1,200-task run, all 47 `schema_invalid` responses were models
  that listed a program in `cannot_determine`, named the missing fact, and set that program's
  value to `null`. Pydantic rejected each whole reply and the task scored as an abstention
  failure (LIMITS §27).
- **Competing answers:**
  - Schema: every program's amount and eligibility is a required value, so a `null` is a
    malformed reply.
  - Models: the prompt says not to guess when a required fact is missing, so there is no
    value to give.
- **Source opened:** the task's own instructions and schema, as they stood before the fix
  (`git show 571d308^`, the parent of the fix commit).
  - The prompt, `redtape/envs/t1_eligibility.py` line 89:
    > "If a fact required to determine a program's outcome is missing from the case file,
    > list that program in `cannot_determine` with the missing fact, instead of guessing."
  - The schema, `redtape/schemas.py` lines 178-179 and 206:
    > `eligible: bool`
    > `benefit: float = Field(description="US dollars for that MONTH, never annualized")`
    > `amount: float = Field(description="the amount the household RECEIVES, not a gross entitlement")`

    All three are required, with no `None` allowed.
- **Resolution:** the models were right; the schema contradicted the instructions it was
  meant to enforce. Fixed in 571d308 (2026-09-03): a scored program's value may be `null`
  only when that program is in `cannot_determine`, enforced by a model validator on
  `T1Answer`. A `null` without a matching abstention is still rejected. Published figures
  were corrected (abstention 0.357 to 0.438, `schema_invalid` 47 to 0).
- **Regression test:**
  `tests/test_parse_real_replies.py::test_real_abstentions_with_null_amounts_parse` and
  `tests/test_parse_real_replies.py::test_null_without_abstention_is_still_rejected`

## A2. Answer keys rested on engine defaults no case file states

- **Status:** draft
- **Disagreement:** LIMITS §35-36. The answer keys used three engine defaults that no case
  file stated: a second adult as the tax-unit spouse, every person working zero hours, and
  every person holding a citizen's Social Security card, including undocumented people.
  Models asked for the missing relationship, hours or SSN status and were scored as
  abstaining needlessly.
- **Competing answers:**
  - Key: the engine defaults (spouse, 0 hours, citizen SSN).
  - Models: the case file does not say, and the answer turns on it.
- **Source opened** (read 2026-09-25):
  - Filing status, 26 U.S.C. 6013(a), uscode.house.gov preliminary edition:
    > "A husband and wife may make a single return jointly of income taxes under subtitle A,
    > even though one of the spouses has neither gross income nor deductions"

    and for the EITC, 26 U.S.C. 32(d)(1):
    > "In the case of an individual who is married, this section shall apply only if a joint
    > return is filed for the taxable year under section 6013."

    Only a married couple can file jointly, so keying two unrelated adults (including a
    45-year-old and a 20-year-old) as joint filers was not supported by any stated fact.
  - Student work hours, 7 CFR 273.5, eCFR as of 2026-09-22:
    > "(a) Applicability. An individual who is enrolled at least half-time in an institution
    > of higher education shall be ineligible to participate in SNAP unless the individual
    > qualifies for one of the exemptions contained in paragraph (b) of this section."

    > "(5) Be employed for a minimum of 20 hours per week and be paid for such employment"

    So a student's weekly hours decide SNAP eligibility; the zero-hours default made a
    student earning $18,000 ineligible where 25 hours a week makes them eligible (LIMITS §36).
  - Social Security numbers, 7 CFR 273.6(a), eCFR as of 2026-09-22:
    > "The State agency shall require that a household participating or applying for
    > participation in SNAP provide the State agency with the social security number (SSN) of
    > each household member or apply for one before certification."

    and for the EITC, 26 U.S.C. 32(m):
    > "a taxpayer identification number means a social security number issued to an
    > individual by the Social Security Administration (other than a social security number
    > issued pursuant to clause (II) ... of section 205(c)(2)(B)(i) of the Social Security Act)"

    So SSN status is load-bearing for both programs, and a citizen's card could not be
    assumed for an undocumented filer.
- **Resolution:** the models were right that the case files were incomplete, and in the
  three cases above the keys were wrong in law, not merely unstated. Every model result was
  withdrawn from the headline on 2026-09-19. The corrected corpus states relationships and
  filing structure, weekly hours for every earner, and SSN status derived from immigration
  status, and closes the rest with a closure sentence.
- **Regression test:**
  `tests/test_unstated_premises.py::test_no_scored_answer_rests_on_an_unstated_premise`,
  which traces every input the engine reads and requires each to be stated, closed, or shown
  irrelevant by perturbation. It passes on the corrected corpus; it was xfail-strict until then.

## A3. Coupled facts: naming the other half of a withheld fact

- **Status:** draft
- **Disagreement:** with `p1.immigration_status` withheld, a model abstained naming
  `p1.ssn_status`. The scorer credited only the canonical identifier.
- **Competing answers:**
  - Scorer: a wrong reason, 0.5.
  - Model: the SSN fact is missing too, so naming it identifies the same hole.
- **Source opened:** the generator, which is the authority for what a case file contains.
  `redtape/generator/narratives.py`, in the person renderer:
  > "# Derived from status, so silent exactly when the status is withheld."

  LIMITS §37, table of named facts on the first corrected-corpus run:
  > "`p1.ssn_status` where `p1.immigration_status` was withheld | 10 | yes - coupled (§36)"

  and `redtape/scoring/core.py`, above `_COUPLED_FACTS`:
  > "That is a correct observation about a genuinely missing fact, not a wrong reason, and
  > scoring it 0.5 would have measured which half of a pair we happen to call canonical."
- **Resolution:** the model was right. Coupled facts are withheld together, so either half is
  credited. The coupling is kept narrow: another person's SSN, or an unrelated fact, is still
  not the withheld one. Groups: employment income with weekly hours, immigration status with
  SSN status, student status with full-time status.
- **Regression test:** `tests/test_env.py::test_either_half_of_a_coupled_fact_counts`

## A4. The anti-hack gate read the answer key

- **Status:** draft
- **Disagreement:** a sincere, well-formed answer (SNAP ineligible, every amount zero, no
  abstention) failed the gate, and the gate also zeroed its abstention score.
- **Competing answers:**
  - Gate: an all-zero answer is degenerate, not an attempt.
  - Model: the answer was wrong but sincere, and not abstaining was correct.
- **Source opened:** LIMITS §42:
  > "`score_antihack`'s `all_amounts_zero` flag fires when every answered amount is zero
  > **and the truth is not all zero**."

  > "The gate reads the answer key. It is described as a structural check on the response,
  > but it consults ground truth, so it fires only on *wrong* all-zero answers."

  and the audit table in the same section:
  > "`abstained_on_everything` | **yes** - consulted `deciding_programs`, which is
  > answer-key information | **removed**"
- **Resolution:** a gate decides whether a response is a scoreable attempt, and must answer
  that from the response alone. Both key-reading checks were removed; `negative_amount` was
  kept. `score_antihack(given)` now takes only the response. Decided on 2026-09-21, before
  the Opus 5 run, so its results could not influence the choice.
- **Regression test:** `tests/test_env.py::test_the_gate_cannot_read_the_answer_key`

## A5. The SNAP five-year bar

- **Status:** draft
- **Disagreement:** 13 of GPT-5.6 Sol's 16 `other:` escapes asked how long a lawful permanent
  resident had held that status (LIMITS §39). The case files never state it. The engine
  never reads it: `years_since_us_entry` defaults to 5 and SNAP's status test does not
  consult it.
- **Competing answers:**
  - Key: computed as though the bar is satisfied (implicitly, and undocumented).
  - Models: the answer cannot be determined without the duration of LPR status.
- **Source opened** (read 2026-09-25):
  - 8 U.S.C. 1612(a)(2)(L), uscode.house.gov, preliminary edition, the SNAP-specific rule:
    > "With respect to eligibility for benefits for the specified Federal program described in
    > paragraph (3)(B), paragraph (1) shall not apply to any qualified alien who has resided in
    > the United States with a status within the meaning of the term "qualified alien" for a
    > period of 5 years or more beginning on the date of the alien's entry into the United
    > States."
  - 8 U.S.C. 1613(a), same source, the general bar:
    > "an alien who is a qualified alien (as defined in section 1641 of this title) and who
    > enters the United States on or after August 22, 1996, is not eligible for any Federal
    > means-tested public benefit for a period of 5 years beginning on the date of the alien's
    > entry into the United States with a status within the meaning of the term "qualified
    > alien"."
  - 7 CFR 273.4(a)(6)(iii), eCFR as of 2026-09-22:
    > "(iii) The following qualified aliens, as defined in paragraph (a)(6)(i) of this
    > section, must be in a qualified status for 5 years before being eligible to receive
    > SNAP benefits."
    > "(A) An alien age 18 or older lawfully admitted for permanent residence under the INA."
  - 7 CFR 273.4(a)(6)(ii)(A), same source, an exception the models did not raise:
    > "An alien age 18 or older lawfully admitted for permanent residence under the INA who has
    > 40 qualifying quarters as determined under Title II of the SSA"
- **Resolution:** the models were right that the case file is incomplete for an LPR adult.
  One refinement: duration of status is not the only fact that settles it. An LPR adult with
  40 qualifying quarters of work is exempt from the 5-year requirement whatever the duration,
  so the case file is missing "duration of status, or 40 qualifying quarters", and asking for
  either is a correct request. The key is not wrong in its number (engine default 5, bar
  treated as met); it rests on a premise no case file states. The earlier citation of
  8 U.S.C. 1613 alone was incomplete: the SNAP-specific rule is 1612(a)(2)(L), implemented at
  7 CFR 273.4(a)(6)(iii). Corrected everywhere it appeared (LIMITS §49). Fix: branch `lpr-five-year-bar` (925e195) states a status start
  year at least five years before the tax year, so the bar cannot apply. Not merged. It is
  held for the single corpus revision (PROJECT_RECORD §6).
- **Not checked:** how P.L. 119-21 §10108, which narrows which non-citizens are eligible for
  SNAP, interacts with these provisions. The uscode.house.gov text read here carries no
  P.L. 119 amendment note for 1612 or 1613.
- **Regression test:** on branch `lpr-five-year-bar` only:
  `tests/test_unstated_premises.py::test_no_scored_answer_rests_on_an_unstated_premise`, which
  fails when an LPR adult lacks a stated start year or has one under five years before the
  tax year. Nothing on `main` guards this yet.

## A6. "Reports a disability"

- **Status:** draft
- **Disagreement:** on 22 determinate tasks all three models (Opus 5, Opus 5.5, GPT-5.6 Sol)
  agree with each other against the SNAP key. 17 involve a person who "reports a disability".
  The models treat that person as a disabled household member, which removes the
  excess-shelter cap and the gross income test, so they answer higher (LIMITS §46).
- **Competing answers:**
  - Key: not a disabled member for SNAP, because no qualifying benefit is stated.
  - Models: a disabled member, from the self-report.
- **Source opened:**
  - The case file, task hh-20260828-00007 in `data/dev/t1.jsonl`, exact wording:
    > "Person p2 is 23, earns $4,407 per month from employment, working 53 hours a week, a
    > citizen, holds a Social Security number, reports a disability, is not a student."

    and the closure sentence, several lines later:
    > "Other than employment income and any benefits stated above, nobody in the household has
    > any income, and nobody has savings or other assets."
  - 7 CFR 271.2, definition of "Elderly or disabled member", eCFR as of 2026-09-22:
    > "Elderly or disabled member means a member of a household who: (1) Is 60 years of age or
    > older; (2) Receives supplemental security income benefits under title XVI of the Social
    > Security Act or disability or blindness payments under titles I, II, X, XIV, or XVI of
    > the Social Security Act;"
- **SSI amounts:** none stated. Of 1,200 dev tasks, 237 contain "reports a disability"; none
  of those mention SSI, SSDI or any disability benefit, and no task in the split mentions SSI
  at all. The comparison against paragraph (2) therefore does not arise: no case file gives
  a benefit that would make the person a disabled member.
- **Resolution:** the key is right in law. Under 7 CFR 271.2 SNAP disability turns on
  receiving a qualifying benefit, and the closure sentence rules out any income not stated.
  The case file is still ambiguous, because "reports a disability" reads naturally as the
  fact that matters and sits far from the closure sentence. This is a wording defect in the
  corpus, not an arithmetic one. Fix (LIMITS §46.1): for every person who reports a
  disability, state that they receive no disability benefit (no SSI determination, no SSDI,
  no veteran disability status). Held for the same single corpus revision as A5.
- **Related:** PolicyEngine issue #8431 (open, 2026-05-24), "Audit broad is_disabled usage
  across program-specific disability rules". It records that SNAP's work-requirement and
  student exemptions read the broad `is_disabled`, while SNAP's elderly/disabled rules read
  `is_usda_disabled`. The flag this renderer sets could therefore still move a key through
  the exemptions even though 271.2 governs the shelter cap and gross test. Not measured.
- **Regression test:** none yet. Named for the fix:
  `tests/test_unstated_premises.py::test_reported_disability_states_no_qualifying_benefit`,
  asserting the no-benefit sentence whenever "reports a disability" is rendered.
