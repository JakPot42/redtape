# DRAFT: notice for the Environments Hub listing (and the top of the GitHub README)

**Status: DRAFT for review. Not posted anywhere.** Per the 2026-09-19 decision: post a
notice, do not withdraw. The environment is technically sound; the corpus has a known defect.
Withdrawing would destroy the install path and the provenance, and a quiet withdrawal reads
worse than disclosure.

---

> **Known defect in v0.1.0: answer keys for EITC, CTC and SNAP student eligibility.**
>
> The task corpus shipped in `jakpotvin/redtape` v0.1.0 has known answer-key defects. The
> environment code, the scoring and the install are unaffected; the problem is in the
> ground truth for some tasks:
>
> - **Two-adult households are keyed as married couples filing jointly**, although the case
>   files never state a relationship. Among them are parents and adult children keyed as
>   spouses. This affects EITC and CTC on about a third of dev tasks.
> - **Undocumented filers are keyed as if they held a Social Security number**, so the key
>   credits EITC and CTC where the correct answer is $0.
> - **Students with stated earnings are keyed as working zero hours**, which denies the SNAP
>   20-hour student exemption. This affects SNAP eligibility for student households.
>
> **Every published result from v0.1.0 is superseded.** That includes the Claude Opus 5
> numbers previously in the README ("it notices a missing category, not a missing
> quantity"). Please do not cite them.
>
> A corrected corpus is coming. In it every premise the answer key depends on is stated in
> the case file: relationships and filing structure, weekly hours, Social Security
> status, and heating and cooling costs. A new test fails if any scored answer rests on a
> fact the case file does not state. The full account, including how the defect was found,
> is in `docs/LIMITS.md` §35–§36.

---

Notes for the reviewer:

- The figures are deliberately qualitative ("about a third"). The exact counts in LIMITS §36
  (393, 163, 81, 97/110) are exposure bounds from narrative text, not per-task verdicts, and
  a notice should not present bounds as counts.
- Nothing here claims an engine bug. The primary sources show the engine matches the
  statute; the defects were our oracle's unset defaults (docs/PRIMARY_SOURCES_2026-09.md).
  This matters because PolicyEngine is named on the listing.
- The legal characterisation ("the correct answer is $0") rests on IRC §32(c)(1)(E)/(m) and
  §24(h)(7), both **read** on 2026-09-19, so it is stated flatly.
