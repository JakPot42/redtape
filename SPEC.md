# REDTAPE v0 — Build Brief for Claude Code

## How to use this file

1. Create an empty git repo called `redtape`. Put this file in it as `SPEC.md`.
2. Start Claude Code in that directory. First prompt, verbatim:

   > Read SPEC.md end to end. Do not write code yet. Before proposing anything, read the current documentation for the two libraries this project depends on (`verifiers` from Prime Intellect and `policyengine-us`) and confirm the APIs, because the sketches in SPEC.md may be stale. Then create a CLAUDE.md capturing the conventions in Section 9, and propose a plan for Phase 1 only, listing the decisions you need from me. Stop and wait for my answers.

3. Answer its questions (Section 8 lists the ones it will need). Then tell it to execute Phase 1. Review at every checkpoint in Section 7 before allowing the next phase.
4. Work in small commits. If it claims something works, ask it to show the test output.

Claude Code docs, if you need them: https://docs.claude.com/en/docs/claude-code/overview

---

## 1. What we are building

**Redtape v0** is a suite of verifiable training-and-evaluation environments for US public-benefits work, packaged for Prime Intellect's Environments Hub, plus a held-out benchmark and a public leaderboard of frontier models.

The thesis: bureaucratic work with public rulebooks is the most valuable under-built source of *deterministic* reward for training AI agents. Existing work (PolicyEngine's PolicyBench, Column Tax's TaxCalcBench, Stanford's HealthAdminBench) scores whether a model computes the right *amount*. Nobody scores the harder half: whether the agent knows **which facts and documents are required, which are missing, and when it cannot answer**. That half is where filings actually fail in the real world, and it is what v0 adds.

Two deliverables, in this order:

1. **The environments** (three task families, Section 4), with deterministic verifiers, installable and runnable through the `verifiers` library, pushed to the Environments Hub.
2. **The benchmark + leaderboard**: a public dev split, a private held-out split, evaluation runs across frontier models, a static results page, and a methodology writeup with an honest limits section.

Not in scope for v0: live government portals, real PII, LLM-as-judge scoring, a consumer product, any state beyond the one chosen in Section 8.

## 2. Ground truth sources

- **Amounts and eligibility:** `policyengine-us` (open source, OpenFisca-based). It validates against NBER TAXSIM and, under a 2025 MOU, the Atlanta Fed Policy Rules Database. Treat it as the oracle for SNAP, Medicaid eligibility, EITC, and CTC. Pin the exact package version in the repo and record it in every result file.
- **Cross-check (optional, Phase 3): ~~the Atlanta Fed Policy Rules Database~~ — DROPPED 2026-08-29.** The PRD is not an independent second opinion. PolicyEngine validates its own results against the PRD under a 2025 memorandum of understanding and the two parties collaborate on resolving discrepancies (policyengine.org/us/research/policyengine-atlanta-fed-mou-prd), so agreement has been actively engineered and would overstate independence. It is a reconciled model, not a second opinion. Published-table validation (docs/LIMITS.md §7) replaces it. The PRD dataset was in any case unreachable: atlantafed.org returned HTTP 403.
- **Documentation requirements:** a hand-built, cited rules table (`rules/verification_requirements.yaml`). Sources: 7 CFR 273.2(f) (federal SNAP verification requirements) and the chosen state's SNAP and Medicaid policy manuals. Every rule carries a citation, a plain-English summary, and a `confidence` field (`high` / `medium` / `low`). This table is the most fragile artifact in the project; see Section 6.

## 3. Repository layout

```
redtape/
  SPEC.md
  CLAUDE.md
  README.md                  # methodology, limits, how to reproduce
  LICENSE                    # Apache-2.0
  pyproject.toml
  redtape/
    generator/
      households.py          # seeded procedural household generator
      narratives.py          # turns a structured household into a natural-language case file
      packets.py             # generates submitted-document packets (complete, incomplete, decoy)
    oracle/
      policyengine_oracle.py # wraps policyengine-us; returns ground-truth amounts/eligibility
      documentation_oracle.py# applies rules/verification_requirements.yaml to a household
    verifiers/
      amounts.py             # exact and tolerance matching
      eligibility.py         # boolean flags per person / per program
      documentation.py       # set comparison: missing-doc precision/recall/F1
      abstention.py          # rewards correct "cannot determine" answers
      antihack.py            # formatting checks, trivial-baseline detection
    envs/
      t1_eligibility.py      # load_environment() for task family 1
      t2_documentation.py    # task family 2
      t3_intake.py           # task family 3 (multi-turn, tool-based)
    schemas.py               # pydantic models for household, packet, answer formats
  rules/
    verification_requirements.yaml
    REVIEW_CHECKLIST.md      # one line per rule for the human reviewer to sign off
  data/
    dev/                     # public split, committed
    heldout/                 # NOT committed; generated from a private seed
  eval/
    run_eval.py              # wraps vf-eval across models, writes results/*.json
    baselines.py             # trivial and heuristic baselines
    leaderboard/             # static site generator + built HTML
  tests/
  docs/
    HUB_SUBMISSION.md        # push checklist and bounty notes
    LIMITS.md                # what is deterministic, what is not, what we don't claim
```

## 3a. Scope decision for v0 (added 2026-08-30, supersedes §4 where they conflict)

**v0 ships T1 and T1b, done deeply. T2 is deferred to v1. T3 is the candidate second
family if T1b lands early.**

The novelty of this project is **determinability**, not documentation. T2 depends entirely
on `rules/verification_requirements.yaml`, which currently has ten seed entries, none at
`high` confidence, a measured 30% citation error rate, and a review process now agreed to
be slower than §7 assumed. A shallow T1b would be worse than no T1b — a benchmark that
scores abstention badly is one someone will publicly take apart, and abstention is the
whole reason this is interesting. Shipping without T2 costs scope and nothing else.

**If there is room for a second family, it should be T3, not T2.** T3 (intake) is
conceptually closer to T1b: both are about knowing what you don't know. T2 is a different
problem — matching submitted documents against a rules corpus — and it is gated on that
corpus being trustworthy.

The rules table continues as a slow parallel track, a few rules a week with every citation
read before the rule is written. It is the natural v1 and a compounding asset; it is not on
the v0 critical path.

README line when we get there: *"v0 scores what can be proven. Documentation completeness
requires a rules corpus we are building carefully rather than quickly, and it is v1."*

## 4. Task families

All tasks are generated from a seeded household record. The narrative (what the agent sees) is rendered from the record with randomized phrasing, field order, units, and distractor details, so the answer cannot be inferred from formatting.

Answer formats are strict JSON validated against `schemas.py`. Malformed JSON scores zero; log the failure separately from wrong answers.

### T1 — Eligibility and amounts

**Input:** a case-file narrative describing a household (members, ages, incomes by source and period, expenses, immigration/citizenship status, state, month).
**Output:**
```json
{
  "snap": {"eligible": true, "monthly_benefit": 291.0},
  "medicaid": {"person_ids": {"p1": true, "p2": false}},
  "eitc": 3995.0,
  "ctc": 2000.0,
  "cannot_determine": []
}
```
**Verifier:** eligibility flags exact; amounts within a tolerance (default ±$1, configurable); per-program partial credit reported but the headline metric is all-correct.

**T1b — Abstention.** A fixed fraction of T1 tasks (default 25%) omit a fact that is *required* for at least one program (for example, no shelter costs while the household would qualify for the excess shelter deduction, or no immigration status for one member). The correct answer names the affected program(s) in `cannot_determine` with the missing fact. A confident number where abstention was required scores zero for that program. A needless abstention also scores zero. This is the core of what v0 adds: the verifier distinguishes "not eligible" from "not determinable."

### T2 — Documentation completeness (the omission task)

**Input:** the same household narrative plus a structured list of documents the applicant submitted (each with type, subject person, date range, issuer). Packets are generated as complete, incomplete, or **decoy-complete**: the right document types are present but one is expired, covers the wrong period, names the wrong person, or is from an unacceptable issuer.
**Output:**
```json
{
  "missing_verifications": [
    {"requirement_id": "SNAP-INC-01", "person_id": "p2", "reason": "pay stubs cover a period outside the 30-day window"}
  ],
  "packet_sufficient": false
}
```
**Verifier:** set comparison against `documentation_oracle`. Report precision, recall, F1 on `requirement_id + person_id`; `reason` is stored for analysis but not scored in v0. Headline metric: exact set match.

### T3 — Intake (multi-turn, tool-based)

The agent is given only a thin opening statement ("Maria, 34, wants to apply for food assistance in <state> for her family") and a single tool, `ask_household(question: str) -> str`, backed by a scripted oracle that answers truthfully **only what is asked** and never volunteers information. The agent must decide what to ask, then submit a T1-format answer.
**Verifier:** T1 correctness of the final answer, plus a penalty for each mandatory fact the agent never elicited (from the rules table), plus a small per-question cost to discourage exhaustive interrogation. The budget of questions is configurable. This task tests whether the agent knows what it doesn't know.

Implement T3 last. If the `verifiers` library's multi-turn/tool environment types make it awkward, ship T1 and T2 first and flag it.

## 5. Splits, contamination, and anti-reward-hacking

- **Dev split** (public, committed): 300 T1 tasks, 300 T2 tasks, 100 T3 tasks, generated from a published seed.
- **Held-out split** (private): same sizes, generated from a seed stored only in a local `.env` and never committed. The generator is deterministic, so the held-out split can be regenerated at any time and **rotated quarterly** by changing the seed. Never publish held-out answers; publish only aggregate scores.
- **Formatting fuzz:** narratives vary phrasing, ordering, currency formatting, and include irrelevant details. Verify that no field in the narrative leaks the answer.
- **Trivial baselines** must be computed and reported: always-eligible, never-eligible, zero-missing-docs, all-docs-missing, and a rules-only heuristic (gross income vs. FPL threshold). If any frontier model fails to beat a trivial baseline on a task family, say so on the leaderboard.
- **Upper-bound baseline:** the same model given the PolicyEngine calculator as a tool. The gap between tool-less and tool-equipped runs separates intake and reasoning errors from arithmetic errors. Report both.

## 6. The rules table is the crown jewel and the biggest risk

`rules/verification_requirements.yaml` encodes which verifications are mandatory, for whom, under which conditions, and what counts as acceptable evidence. Rules:

- One entry per requirement. Fields: `id`, `program`, `applies_when` (a small condition language over household fields), `subject` (household / person / member-with-income, etc.), `acceptable_documents` (types, issuer constraints, recency window), `citation` (section of 7 CFR 273.2(f) or the state manual), `summary`, `confidence`.
- Claude Code drafts the table from the primary sources, but **must not mark anything `high` confidence on its own**. Every rule starts at `medium` and is promoted only by the human reviewer after checking the citation. `REVIEW_CHECKLIST.md` is the sign-off sheet.
- Any rule at `low` confidence is excluded from scoring until promoted; it can still appear in the narrative.
- The README must state how many rules are in each confidence tier at release time.

## 7. Phases and checkpoints

**Phase 1 — Oracle and generator (target: 5 working days)**
1. Confirm current APIs of `verifiers` and `policyengine-us` from their docs. Write a 20-line smoke script that computes SNAP and Medicaid eligibility for one hard-coded household and prints the result. Commit it.
2. Validate the oracle against at least ten published worked examples (state handbook examples, PolicyBench public cases, USDA SNAP eligibility examples). Log every discrepancy in `docs/LIMITS.md`. If more than two of ten disagree, stop and report before continuing.
3. Build the household generator with realistic distributions (household size, income sources, earned vs unearned, shelter and utility costs, ages, citizenship mixes). Every household must be reproducible from `(seed, index)`.
4. Draft the rules table (Section 6) and the documentation oracle.
5. Tests: oracle determinism, generator reproducibility, schema validation, rules-table linting (every rule has a citation and a confidence).

**Checkpoint 1:** show the human ten generated households, their oracle outputs, and the rules table. Do not proceed until the human has reviewed the rules table.

**Phase 2 — Environments (target: 5 working days)**
1. Implement T1 and T1b as a `verifiers` environment with a rubric composed of the amounts, eligibility, abstention, and anti-hack reward functions. Confirm `vf-eval` runs end to end against a small open model.
2. Implement T2 the same way.
3. Implement T3 with the `ask_household` tool.
4. Compute all trivial baselines.
5. Write `docs/HUB_SUBMISSION.md` and do a dry-run push to the Environments Hub.

**Checkpoint 2:** `vf-eval` output for T1, T2, and (if done) T3 on one model, plus baseline numbers.

**Phase 3 — Benchmark and leaderboard (target: 5 working days)**
1. `eval/run_eval.py` runs the dev and held-out splits across the models the human provides keys for. Budget cap per run is a CLI flag; stop when hit. Cache all responses.
2. Produce `results/*.json` with per-task scores, aggregate metrics, model/version, environment version, PolicyEngine version, seed identifiers, and timestamps.
3. Build the static leaderboard page: one table per task family, trivial baselines shown in the same table, tool-equipped upper bound shown as a separate column, held-out and dev scores side by side.
4. Write the README methodology section and `docs/LIMITS.md`. The limits section must state plainly: which scores are deterministic and which are not (all of v0 should be deterministic), the confidence composition of the rules table, that only one state is covered, that no live portals are involved, and how v0 relates to PolicyBench, TaxCalcBench, and HealthAdminBench.

**Checkpoint 3:** the leaderboard, the README, and the limits doc, before anything is published.

## 8. Decisions Claude Code needs from the human before Phase 1

1. **State.** Default: California (CalFresh, Medi-Cal), chosen for manual quality and PolicyEngine coverage. Claude Code should check PolicyEngine's state coverage tests first and propose an alternative if California is thin.
2. **Programs in v0.** Default: SNAP, Medicaid, EITC, CTC. Anything else waits.
3. **Tolerance** for amount matching. Default ±$1.
4. **API keys** and a per-run budget for Phase 3 evaluations.
5. **Private seed** location (a local `.env`, gitignored).
6. **License.** Default Apache-2.0.

## 9. Conventions (goes into CLAUDE.md)

- Read library docs before using an API; never assume from memory. Pin every dependency.
- Determinism everywhere: same seed, same output. Tests enforce it.
- No real PII. All households are synthetic. No scraping of live government systems.
- Every ground-truth claim traces to a source: a PolicyEngine variable name or a rules-table citation.
- Honest limits over impressive claims. If a verifier is approximate, it is labeled approximate. If something couldn't be validated, it says so in `docs/LIMITS.md`.
- Small commits, each with passing tests. Show test output rather than describing it.
- When uncertain about a rule's legal meaning, add it at `low` confidence and flag it for the reviewer rather than guessing.
- Stop at every checkpoint and wait.

## 10. Definition of done for v0

- `pip install -e .` then `vf-eval` runs T1, T2, T3 end to end.
- Dev split committed; held-out split regenerable from the private seed.
- All tests pass, including determinism and rules-table lint.
- Trivial and tool-equipped baselines computed and displayed.
- At least three frontier models evaluated on both splits, with cached responses.
- Leaderboard page builds from `results/*.json` with one command.
- README, `docs/LIMITS.md`, and `docs/HUB_SUBMISSION.md` complete.
- Rules table fully reviewed; no `low`-confidence rule is scored.
- Successful push to the Environments Hub.

## 11. What v1 might add (do not build now)

A second state; healthcare prior-authorization tasks; scoring the `reason` field; an LLM-judged track clearly separated from the deterministic one; a rotating public benchmark with quarterly held-out refreshes; a partnership integration with PolicyEngine so their engine version bumps trigger a re-validation run.
