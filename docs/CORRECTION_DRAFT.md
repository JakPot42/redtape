# Correction, drafted BEFORE the deciding run (2026-09-20)

Written now, on purpose, so the wording is not chosen under the temptation of a result we
would prefer. Both branches are drafted. When Opus 5 has run on the corrected corpus, one of
them is published at the top of the README, on the Hub listing, and anywhere else the
finding was claimed; the other is deleted.

**The experiment.** Opus 5 and GPT-5.6 Sol, both on the corrected dev split, same scorer,
same tasks. The question is whether abstention accuracy on the eligibility-flip class
(a missing fact that moves a *category*) exceeds accuracy on the indeterminate class (one
that moves a *quantity*), as the withdrawn result claimed at 0.396 against 0.050.

**Decision rule, fixed in advance.** "Reproduces" means the flip class exceeds the
indeterminate class by a margin that survives the n's actually collected, reported with the
interval, on the full 1,200-task split. Not a directional nudge, and not a ratio computed
from cells of 3 tasks. If the two classes land within noise of each other on both models,
that is branch B.

---

## Branch A — it reproduces on Opus but not on GPT-5.6 Sol

> **Correction (DATE): the category/quantity finding is model-specific.**
>
> This project's headline claim — that a frontier model notices a missing fact far more
> reliably when its absence changes a category than when it changes a quantity — was
> withdrawn on 2026-09-19 when the corpus it was measured on proved to have defective answer
> keys (`docs/LIMITS.md` §35–§36). It has now been re-measured on the corrected corpus.
>
> On Claude Opus 5 the gap is present (FLIP vs INDETERMINATE). On GPT-5.6 Sol it is not
> (FLIP vs INDETERMINATE). The claim therefore holds for one model and does not generalise
> across labs, which is narrower than the original text implied: it described "a frontier
> model" and was measured on exactly one.
>
> The original magnitudes (0.396 against 0.050) are **not** reinstated. They were measured on
> a corpus where a third of tasks rested on unstated premises, under a scorer that ignored
> which fact the model named, with a flip class inflated by a zero-hours default. The
> corrected numbers are the only ones to cite.

## Branch B — it reproduces on neither

> **Retraction (DATE): the category/quantity finding was an artifact.**
>
> This project's headline claim — that a frontier model notices a missing fact far more
> reliably when its absence changes a category than when it changes a quantity, measured at
> 0.396 against 0.050 — **is retracted.** It does not reproduce on the corrected corpus, on
> either Claude Opus 5 or GPT-5.6 Sol.
>
> The original measurement had three defects, each sufficient on its own to produce the gap
> (`docs/LIMITS.md` §35–§37):
>
> 1. A third of the corpus rested on premises no case file stated, so the answer keys for
>    those tasks were wrong.
> 2. The scorer credited or denied an abstention without reference to which fact the model
>    named.
> 3. The eligibility-flip class was inflated by students whose ineligibility came from an
>    engine default of zero working hours.
>
> What survives is the machinery, not the result: three metrics with a verified ceiling,
> baselines that discriminate, a corpus in which every premise an answer key depends on is
> stated in the case file, and a test that fails if that ever stops being true. The
> abstention question is still worth asking. This project has not yet answered it.

---

**Whichever branch runs, these go with it:**

- The superseded numbers stay visible, labelled, in the README's *Superseded results*
  section. They are not deleted: the record of what was claimed is part of the correction.
- The Hub listing carries the same text as the README, pushed as a new version (the listing
  is updated by `prime env push`, which republishes the environment).
- `docs/LIMITS.md` §37 keeps the full account, including that the deciding run was proposed,
  priced and approved separately before it was spent.
