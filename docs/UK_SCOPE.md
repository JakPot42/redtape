# PolicyEngine UK: scoping (not built)

**Status: scoping only, 2026-09-18. Nothing here is built. Nothing is committed to scoring.**

The thesis under test: *Redtape's machinery ports to the UK with an oracle swap rather than a
rewrite.* Short answer, from measurement rather than reading: **true for the evaluation half,
false for the ground-truth half.** Everything under `eval/` (cache, budget, providers,
parse → schema → score path, abstention and pair metrics, redaction, the `verifiers.v1` env
shape) is jurisdiction-free and carries over. The oracle wrapper, answer schema, generator,
narratives, the `rules_only` baseline, and all external validation are California-specific
and would be rewritten, not swapped. Roughly half the codebase by module count.

Every engine observation below was measured on **`policyengine-uk` 2.99.1** (with
`policyengine-core` 3.32.6 as resolved) in a scratch venv at `/tmp/peuk`, outside this repo's
lockfile. Probe scripts are not committed. **Engine output is an observation about the engine,
never ground truth.**

---

## 1. Period semantics: the US monthly/annual split does NOT carry over

This is the trap the US build lost a day to, so it was measured first.

| | US (`policyengine-us`) | UK (`policyengine-uk` 2.99.1) |
|---|---|---|
| variables by period | mixed; SNAP family is `month` | **883 of 891 are `year`**; 8 are `month` (NI class 1 and energy/fuel only) |
| what a year period means | calendar year | **benefit/tax year starting in April** |
| Universal Credit | n/a | `year` variable, although DWP assesses UC in **monthly assessment periods** |

**How "year" is defined.** The YAML stores legal effective dates (UC standard allowance
`2025-04-01: 400.14`). At load, `utils/parameters.py::convert_to_fiscal_year_parameters`
rewrites every parameter: *"samples each parameter at April 30 of each year and sets that as
the value for the entire year period"*. Only parameters flagged `fiscal_year_blend: true`
are day-weighted instead, and there are three of them, all capital gains tax.

Measured consequences:

- `universal_credit` for period `"2025"` = **4,801.68 = 12 × 400.14**, the 2025-26 rate.
  DWP's published rates confirm 393.45 (2024-25) → 400.14 (2025-26).
- A **monthly query of a year variable** (`"2025-01"`) returns 400.14. In reality January
  2025 paid the 2024-25 rate, 393.45. **Every Jan–Mar month is answered at the wrong year's
  rate.** Querying the engine by calendar month is unsafe, full stop.
- Any change effective after 30 April is invisible until the next year. The source shows
  such changes existed: UC `2021-10-06` (removal of the £20 uplift) is sampled away for 2021.
- Weekly benefits annualise at exactly ×52 (Pension Credit 11,536.20 / 221.85 = 52.000), so
  a weekly figure is recoverable exactly. A real payment year can contain 53 paydays; asking
  for the annual figure would bake in PolicyEngine's 52.

**Proposed canonical periods** (decision needed, see §8):

| field | period asked | engine query |
|---|---|---|
| Universal Credit | **monthly** amount for a stated month | fiscal-year `"Y"` ÷ 12, valid only under "circumstances unchanged all year" |
| Pension Credit, Child Benefit, Scottish Child Payment | **weekly** | fiscal-year ÷ 52 |
| Income Tax, National Insurance | **tax year** (6 Apr – 5 Apr), labelled `"2025-26"` | fiscal-year `"Y"` |

Rules that follow: map any stated month to the fiscal year *containing* it before querying;
**never generate April** (UC uprates from the first assessment period on or after the
uprating date while the tax year starts 6 April, so April straddles both); every narrative
states "circumstances unchanged throughout the year"; every period label is `YYYY-YY`, never
a bare year, because a bare `2025` means different things to the engine and to a reader.

---

## 2. Oracle wrapper: what changes

| | US | UK |
|---|---|---|
| entities | person / tax_unit / spm_unit / family / household | **person / benunit / household**. No tax unit: UK income tax is individual |
| scored programmes | SNAP, EITC, CTC | candidates: UC, Pension Credit, Child Benefit, income tax, NI, Scottish Child Payment (§6) |
| take-up | imputed receipt of *other* programmes leaks in as income; suppressed | `would_claim_uc`, `would_claim_pc`, etc. **default `True`** in calculator mode ("generated stochastically in the dataset"). Assert them explicitly rather than inherit the default |
| immigration | `immigration_status`, 11 statuses | **absent.** No NRPF, habitual-residence or right-to-reside variable exists anywhere in the package |

**Same traps as the US, already visible:**

- **Eligible ≠ receives.** `is_uc_eligible` is `True` for a single person earning £50k whose
  `universal_credit` is 0. Same shape as `ctc` vs `ctc_value`. Also unverified: whether
  `universal_credit` is before or after the `benefit_cap` reduction. Resolve it with the
  extreme sweep, not by reading names.
- **Silent defaults that move answers.** `age` → 40, `country` → ENGLAND,
  **`region` → LONDON**. The London default visibly changed an answer: a single person with no
  income and age omitted got `benefit_cap` = 16,967, the London single rate. Also: rent of
  £9,600/yr gave a housing element of exactly £9,600 in **both** London and the North East,
  so the Local Housing Allowance cap was not applied. The likely cause is another silent
  default (tenure type, or the Broad Rental Market Area), but **I have not traced it.** It
  must be characterised before any housing figure is scored.
- The `assert_no_unstated_income` invariant ports conceptually and must be rebuilt with teeth.

**Environment:** `policyengine-uk` declares `policyengine-core>=3.30.1`, so our direct
3.31.1 pin satisfies it on paper. Untested. A UK build needs its own `generate-uk` extra, its
own determinism reference, and its own version-drift policy. The package is at 2.99.1, so its
release cadence needs measuring the way the US one was.

---

## 3. Schemas: what is UK-specific

`T1Answer` is US through and through (`snap`, `eitc`, `ctc`, `medicaid`). A UK answer object
would be new: `universal_credit {period: month, eligible, amount}`,
`pension_credit {period: week, …}`, `child_benefit {week}`,
`scottish_child_payment {week}` (Scotland only), `income_tax {tax_year}`,
`national_insurance {tax_year}`, plus the unchanged `cannot_determine` list.

What carries over unchanged: `_answer_shape()` rendering the prompt from the schema, the
parse path, `cannot_determine` semantics, the ±tolerance idea (rescaled: £1/month or £0.10/week
is a decision), pair consistency. `metrics._scored_fields` is US-specific and needs to be
generic over the answer class.

---

## 4. Determinability fact space: where California's lessons will NOT hold

Measured (lone parent with one child, £12k earnings, £9.6k rent, 2025-26, unless stated):

| fact withheld | effect in the engine | category or quantity? |
|---|---|---|
| **savings** £5k → £17k | UC 14,028 → **0**, `is_uc_eligible` 1 → 0 | **category**, via a capital test |
| **partner's age** (70+70 vs 70+50) | Pension Credit 17,607.72 → 0, UC 0 → 7,537.20 | **category**: the mixed-age-couple rule switches programme |
| **own age** (omitted→40 vs 67) | UC 4,801.68 ↔ Pension Credit 11,536.20 | **category**: programme switch |
| **country** (England vs Scotland) | child gets Scottish Child Payment 1,411.80; income tax at £50k 7,486 → 9,013.80 | category (SCP) *and* quantity (tax) |
| region | benefit cap level (London vs elsewhere) | quantity |
| earnings, rent, children | taper, housing element, child element | mostly quantity; can reach a zero award |

**Predicted departures from California, to verify rather than assume:**

1. **Income-type tests DO flip eligibility in the UK.** California's heuristic ("income tests
   get absorbed by broad-based categorical eligibility; composition rules survive") fails
   here. There is no categorical-eligibility override, and the £16,000 UC capital limit is a
   hard cutoff (confirmed on GOV.UK: "have £16,000 or less in money, savings and
   investments"). Expect *more* flips, from more directions.
2. **Age is a programme switch, not a threshold.** In the US it only nudged SNAP (elderly
   deduction). Here it decides UC versus Pension Credit outright, and the partner's age does
   too (GOV.UK confirms mixed-age couples claim UC).
3. **The US's most-missed fact does not exist here.** Opus 5 caught a withheld immigration
   status 0.133 of the time, the worst of any fact. The UK engine cannot represent immigration
   status at all, so that cell of the fact-by-fact comparison can't be made. Narratives must
   state or imply settled status, because the engine would silently ignore anything else.
4. **Disability is an assessment outcome, not a narrative fact.** PIP rates and UC's
   LCW/LCWRA are *determinations*, like `is_ssi_disabled` in the US. They can only enter as
   declared awards, which makes PIP a table lookup rather than a determination (§6).

This matters for part 1's question. If the category/quantity split holds in the UK, it has
to hold over a *different* set of category facts (capital, age as programme switch, country),
which is a stronger test of generalisation than re-running California.

---

## 5. External validation: sources, and what does not exist

**No TAXSIM equivalent.** Nothing in the UK takes a hypothetical household and returns an
independent tax-and-benefit calculation as a public service. Candidates, each with a caveat:

| source | gives | status |
|---|---|---|
| DWP *Benefit and pension rates 2025 to 2026* (GOV.UK) | every DWP rate, both years | **read 2026-09-18**; used below |
| HMRC income tax / NI rates and thresholds; Scottish Government Scottish income tax 2025-26 | bands, allowances, thresholds | not yet read |
| Social Security Benefits Up-rating Order 2025 | the legally binding rates | **not yet read**: this is the primary source |
| DWP *Advice for Decision Making* (ADM) | decision-maker guidance; I believe it has worked UC examples | **unverified.** It's the best candidate for whole-calculation validation because it's written by DWP, independently of PolicyEngine |
| HMRC manuals | worked tax/NI examples | not yet read |
| Commons Library briefings | secondary summaries | PolicyEngine's YAML **cites them**, so a match proves transcription, not independent correctness |
| entitledto / Turn2us / Policy in Practice calculators | full calculations | **excluded.** PolicyEngine's YAML cites entitledto ("From gov.uk and entitledto"), so it isn't independent, and automated querying of a live calculator breaks the no-live-systems rule |
| UKMOD (CeMPA, Essex) | an independent microsimulation engine | licence terms and hypothetical-household support **unverified**. It's the nearest thing to an independent engine and is not a shortcut |
| DWP / HMRC statistics | caseloads, aggregates | aggregate only; not per-household ground truth (the OIG/CERT lesson from claims work) |

### The first parameter check already found a divergence

| | DWP published, 2025/26 | `policyengine-uk` 2.99.1 |
|---|---|---|
| Pension Credit standard minimum guarantee, single | **£227.10/wk** | **£221.85/wk** |
| … couple | **£346.60/wk** | **£338.61/wk** |

Measured end to end, not just read from YAML: a single 67-year-old with no income gets
`pension_credit` = 11,536.20 = 221.85 × 52. The arithmetic suggests the cause. 218.15 × 1.041
= 227.09 (the 4.1% earnings rise DWP applied) versus 218.15 × 1.017 = 221.86 (CPI). The YAML
says `uprating: gov.benefit_uprating_cpi`, and its hardcoded 2025 value matches a CPI uprating.
2024-25 (218.15) and 2026-27 (238.00 ≈ 227.10 × 1.048) look right. **Only 2025-26 is off.**

**Confidence: medium.** It's confirmed against DWP's own published rates page (raw text read,
not a summary), but the binding Up-rating Order has not been read. Not filed upstream. The
#9374 lesson applies before anything is: check for an override elsewhere in the parameter tree
(a triple-lock or pension-credit uprating reform) and state the finding only at the scope measured.

It's the HR 1 SUA pattern again, and it is the strongest argument in this document for
**porting `test_parameter_drift.py` before anything else.** One table checked, one divergence.

---

## 6. Scored-programme candidates

| programme | score? | reason |
|---|---|---|
| Universal Credit | **yes, if** the benefit-cap ordering and LHA/tenure defaults are resolved | the main means-tested benefit; carries the capital and age flips |
| Pension Credit | **not for 2025-26 while §5 stands** | an oracle known to be wrong in the scored year is the circularity rule in reverse |
| Child Benefit | yes | flat weekly rates, externally checkable; high-income charge is a tax-side interaction to check |
| Income tax, NI | yes | HMRC tables; Scotland adds a genuinely devolved layer (measured 7,486 vs 9,013.80 at £50k) |
| Scottish Child Payment | candidate | the Scotland-only programme; category flip on country |
| PIP | **no** | an assessment outcome; only enters as a declared award, so scoring it scores a table lookup |
| Housing Benefit, tax credits, legacy | **no** | tax credits ended April 2025; how the engine chooses UC versus legacy is unverified |

**Jurisdiction.** Recommend England + Scotland. Scotland is the analogue of California's state
layer. Wales measured identical to England on income tax at £50k and adds only council-tax-reduction
differences. Northern Ireland has its own UC mitigations and domestic rates instead of council
tax, and its modelling is unverified.

---

## 7. Phase plan

The same shape as Redtape Phase 1: each phase ends at a checkpoint where work **stops** for
review.

**Phase UK-0: verify before building (research only, no generator).**
Read the Up-rating Order 2025, the HMRC and Scottish Government 2025-26 tables, and check
whether the ADM really contains worked UC examples and how many. Trace the housing element
(tenure, BRMA, LHA). Establish whether `universal_credit` is pre- or post-benefit-cap by the
extreme sweep. Establish how the engine chooses UC versus legacy benefits. Check two-child-limit
status in each candidate year (I believe policy changed for 2026-27; unverified). Measure the
`policyengine-uk` release cadence.
**Checkpoint UK-0:** go/no-go; scored-programme list; tax year chosen; decision on the Pension
Credit divergence.

**Phase UK-1: oracle, schema, external parameter validation.**
UK oracle wrapper with period mapping (no calendar-month queries, ever). UK answer schema with
per-field periods. `test_parameter_drift_uk.py` with every expected value from DWP, HMRC or
the Scottish Government, never the engine. Extreme sweep for eligible-versus-received pairs.
Determinism reference. No-unstated-income invariant with verified teeth. Ten checkpoint
households computed and read by a human.
**Checkpoint UK-1:** every scored programme validated at parameter level; whole-calculation
validation against N independent worked examples (N set at UK-0 by what the ADM actually
contains); silent-default audit complete.

**Phase UK-2: determinability.**
Port the perturbation prober and measure flip rates per fact: capital, own age, partner's age,
country. Build the generator and narratives, and exercise `render()` on everything the
generator emits (LIMITS §31). Build the smoke split, then run baselines and the perfect-agent
ceiling.
**Checkpoint UK-2:** the class mix is achievable, especially the flip class; the ceiling scores
1.000 on all three headlines through the *prompt → parse* path, not a Python-built answer.

**Phase UK-3: split and one live model.** Dev and held-out splits (new seed and new
fingerprint), baselines, then one live model under the budget cap, estimate and approval in
separate turns.

---

## 8. Decisions **[decided 2026-09-18]**

1. **Tax year 2025-26.** Complete, with published rates; the same reasoning as TY2025 for
   California. How to score Pension Credit in that year is settled at Checkpoint UK-0, after
   decision 4's verification.
2. **England only.** Scotland and Wales multiply the validation surface for no gain in v0;
   the same call as California-only. §4's Scotland rows and §6's recommendation are superseded:
   the Scottish Child Payment and Scottish income tax are out of v0. Country becomes a fixed,
   stated fact, not a withheld one.
3. **Tolerance: NOT set yet.** Set it against real amounts, not guessed. UK weekly benefits
   are stated to the penny (Pension Credit £227.10/wk per DWP; Child Benefit £26.05/wk is
   the engine's value, not yet checked against HMRC), so ±£1 is a
   far coarser tolerance relative to the figure than ±$1 was against a monthly SNAP benefit
   in the hundreds. UK-0 measures the distribution of each scored field's amounts over
   generated households, then proposes a tolerance per field.
4. **The Pension Credit divergence is NOT reported.** First read the binding statutory
   instrument (the Social Security Benefits Up-rating Order 2025), and check for an override in
   the engine's parameter tree (the triple-lock and earnings-uprating mechanics are the
   obvious candidate explanation for a CPI-vs-earnings gap). File only if it holds against
   the SI. This is the #9374 lesson: that report checked one source, never looked for the
   override, and half of it was wrong.
