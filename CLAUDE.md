# CLAUDE.md — Redtape v0

Working conventions for this repo. `SPEC.md` is the source of truth for *what* we build; this file is the source of truth for *how*. Where they disagree, SPEC.md wins on scope and this file wins on process — raise the conflict rather than silently picking.

Decisions below marked **[decided]** were settled by the human on 2026-08-26 and are not to be relitigated without asking.

---

## Platform: WSL2, not Windows **[decided]**

**All development, dataset generation, and evaluation happen inside WSL2 / Ubuntu.** Windows-native is a fallback for editing only — never for producing an artifact that ships.

Rationale: the Environments Hub runs Linux; `verifiers.v1` cannot import on Windows; T2/T3 will want Docker.

**The repo lives at `~/redtape` on the native Linux filesystem** **[decided]** — never on `/mnt/c`. The `/mnt/c` performance and permissions problems would surface first in the determinism and reproducibility tests, which are exactly the tests that cannot be allowed to be flaky. Edit via VS Code's WSL remote.

**Run as `jak`, never as root** **[decided]**. The distro originally defaulted to root; a normal user (`jak`, uid 1000, in `sudo`) was created before the repo existed, and `/etc/wsl.conf` sets it as the default. Nothing in this project should be owned by root. If you find yourself running as root, stop and fix it rather than working around the permissions.

Verified environment (2026-08-26):

- WSL2, Ubuntu 26.04 LTS, kernel 6.18.33.2-microsoft-standard-WSL2
- `git` 2.53.0, `uv` 0.12.6 at `~/.local/bin`, CPython 3.13.15 via `uv python install`
- Distro default Python is **3.14.4 — too new**, see below. Never use it directly.
- **Docker is not installed in the distro.** Not a Phase 1 blocker; it is a prerequisite for T3 and must be resolved before that work starts.

**Cross-platform determinism is verified, not assumed.** The same household produced bit-identical oracle output on Windows and Linux (`snap` 969.0, `eitc` 4328.0, `ctc` 2200.0, `household_net_income` 32658.21, `medicaid` [11360.863, 7112.096]). Re-run this check after any dependency bump; if it ever diverges, stop and report before generating anything.

### Python 3.13 — not the machine default anywhere

- `verifiers` requires `>=3.11,<3.14`.
- `policyengine-us` requires `>=3.11,<3.15`.
- Windows system Python is 3.14.2; WSL Ubuntu 26.04 ships 3.14.4. **Both are rejected by `verifiers`.**
- Use `uv venv --python 3.13`. Record the exact interpreter version in every result file.

The 3.13 pin is **not a workaround for a packaging inconvenience — it is what makes the cross-platform determinism check meaningful.** Two machines agreeing bit-for-bit is only evidence about the oracle if they are running the same interpreter; without the pin, an identical result would be luck and a divergent one uninterpretable. Treat the pin as part of the measurement apparatus, not as environment setup.

**Pin every dependency to an exact version** (`==`, never `>=`). See "Version drift" below.

Reference versions: `verifiers==0.3.1`, `policyengine-us==1.821.4`, `policyengine-core==3.31.0`, `pandas==3.0.5`, `numpy==2.5.2`.

---

## `verifiers`: build against v1

`verifiers` 0.3.1 ships two parallel APIs. The top-level names (`vf.SingleTurnEnv`, `vf.ToolEnv`, `vf.Rubric`, `vf.Parser`) are aliased by an import hook to **`verifiers.legacy.*`**, which is on a deprecation path. The forward API is **`verifiers.v1.*`** (`Task`, `Taskset`, `Env`, `Judge`, `Harness`, `Agent`).

We target **v1**. It imports cleanly on Linux (verified: `v1.task`, `v1.taskset`, `v1.judge`, `v1.env`, `v1.rollout`, `v1.harness`, `v1.judges.rubric`, `v1.cli.eval.main` all OK) and fails entirely on Windows at `v1/runtimes/limiters.py` on an unguarded `import fcntl`. This is the second reason the platform decision is not optional.

Two v1 properties we depend on directly:

- **`boundary()` re-raises; it does not swallow.** `verifiers/v1/errors.py::boundary` wraps scoring and re-raises any exception as a typed `TaskError`. Legacy's `Rubric._call_individual_reward_func` does the opposite — bare `except Exception`, log, **return 0.0** — which silently converts a crashed scorer into "the model got it wrong." v1 is structurally compatible with the scorer-error rule below; legacy actively fights it.
- **`Task.hash` / `Task.key`** give a content hash of the task's wire data, which is exactly the stable task identity SPEC.md §5 needs for split reproducibility and quarterly rotation. Use them; do not invent our own IDs.

  **Record `Task.hash` for every held-out task in the results files** **[decided]**. This is the contamination and rotation story, handled by the library rather than by something we invented — and it is the first thing a lab will ask about. It lets anyone verify that a held-out task is the one that was scored, that a rotation actually changed the task set, and that a published number corresponds to a specific set of task hashes, all without publishing held-out answers. The hashes are safe to publish; the task data is not.

Note the spelling is `Taskset`, not `TaskSet`.

**Isolation rule.** All `verifiers` contact stays inside `redtape/envs/`. The generator, the oracles, and every scoring function in `redtape/scoring/` must not import `verifiers` at all — they take plain Python inputs and return plain floats and dicts. This keeps the eventual v1 API churn confined to one directory and makes the scorers unit-testable without a rollout.

---

## Two hard rules

These are requirements, not preferences. Violating either corrupts published numbers.

### 1. Scorer errors are always explicit **[decided]**

Every scoring function wraps its own body in try/except, emits an explicit **`scorer_error`** metric, and re-raises or records — it never returns a bare `0.0` on failure. A silently-zeroed scorer reads as "the model is bad" and would corrupt every number we publish.

`scorer_error` is reported separately from wrong answers, the same way malformed JSON is (SPEC.md §4). Any run with a non-zero `scorer_error` count is not publishable until the cause is understood. This holds even though v1's `boundary` already re-raises — belt and braces, because the metric is what makes the failure *visible in the results file*, not just in a traceback.

### 2. Answer keys are baked at generation time **[decided]**

The oracle is **never** called during a rollout. Answer keys are computed once, during dataset generation, in a single long-lived process, and serialized into the task data.

Rationale: `from policyengine_us import Simulation` costs ~30 seconds cold. Building a `Simulation` is ~0.2s and each `calculate()` is ~1s. Calling the oracle inside a rollout loop, or fanning out across processes that each pay the import, is both ruinously slow and a determinism hazard (it would let an engine version bump silently change the answer key mid-run).

Corollary: every generated dataset file records the `policyengine-us` version, the interpreter version, and the seed used to produce it.

---

## Ground truth

- **PolicyEngine is the oracle for amounts and eligibility** (SNAP, Medicaid, EITC, CTC — **[decided]**, all four, nothing else in v0). Every ground-truth claim traces to either a named PolicyEngine variable or a `rules/verification_requirements.yaml` citation. No ground-truth value is ever hand-computed, hardcoded, or inferred from memory.
- **State: California only** **[decided]**. Verified as the best-covered state in the engine: 296 CA-specific variables (next: IL 198, MA 148, CO 122, NY 115) and 295 CA-related test fixtures.
- **Tax year 2025, month varies** **[decided]**. 2025 is complete and stable and can be checked against published tables; 2026 parameters may still be provisional. Every narrative states **both the month and the tax year explicitly**.

### Entity and period are per-variable and inconsistent

Never assume. Look up `entity`, `value_type`, and `definition_period` before using any variable.

| variable | entity | type | period |
|---|---|---|---|
| `snap` | `spm_unit` | float | **month** |
| `is_snap_eligible` | `spm_unit` | bool | **month** |
| `snap_excess_shelter_expense_deduction` | `spm_unit` | float | **month** |
| `housing_cost` | `spm_unit` | float | **year** |
| `is_medicaid_eligible` | `person` | bool | year |
| `medicaid` | `person` | float | year |
| `eitc`, `ctc` | `tax_unit` | float | year |

`sim.calculate()` returns a numpy array whose length is the entity count, not a scalar.

**SNAP is scored monthly** **[decided]** — `snap` is natively a monthly variable and the answer field is `monthly_benefit`. The ±$1 tolerance **[decided]** applies to the monthly figure, never to an annualized one. Do not annualize SNAP anywhere in the pipeline.

**Every answer-schema field carries its period explicitly** **[decided]**. A schema that leaves the reader to infer whether a number is monthly or annual is a bug. The T1 answer object mixes a monthly SNAP question with three annual ones, and the schema must say so per field.

**Unresolved and blocking:** the exact semantics of querying a monthly variable at an *annual* period. An annual query on `is_snap_eligible` agreed with January in both cases tested, which establishes nothing about the rule. If it means "eligible in at least one month" while the prompt asks "eligible," the answer key is silently wrong. Characterize this and write down the canonical query period per program before building any answer key.

### Other engine facts

- `medicaid` (dollar amount) is **not** `is_medicaid_eligible` (bool). 1,036 of 6,150 variables have "eligib" in the name; per-program variable selection is research, not lookup.
- **Every missing input silently becomes a plausible default.** PolicyEngine has no representation of "unknown" and never raises on an absent fact. Measured: `employment_income` → `0`, `age` → `40.0`, `state_name` → `California`, `is_disabled` → `False`, `immigration_status` → `CITIZEN`, `housing_cost` → `0`. **Omitting a fact and stating it as zero are indistinguishable to the engine.** Anything depending on knowing a fact was absent must track that outside the situation dict.
- **Version drift is a first-class hazard.** `policyengine-us` shipped ~124 releases in July 2026 and ~70 in August — three to four per day. Rules change between them. The pinned version goes in every `results/*.json` and every dataset file. Re-labeling after a version bump is a deliberate, reviewed, diffed step — never an incidental consequence of a dependency sync.
- The wheel ships **4,624 YAML test fixtures** (90 SNAP-related, 295 CA-related). Use them for wiring correctness and version-drift regression. They are the library's own tests, so passing them is circular and proves nothing about external validity — that requires separately-sourced worked examples. Keep the two tracks distinct in `docs/LIMITS.md`.

---

## T1b abstention: three classes, not two

The naive framing ("omit a required fact, reward *cannot determine*") does not survive contact with the engine, because omission is indistinguishable from zero and the engine always answers. Until the SMT upgrade below, T1b labels come from **perturbation-based determinability** **[decided]**: sweep the omitted fact across a declared plausible range, recompute, and classify.

Every T1b case is labeled as exactly one of:

1. **Determinate — complete facts.** Ordinary T1. Model should answer.
2. **Indeterminate.** The omitted fact flips the outcome within its plausible range. Abstention is correct; the answer must name the affected program and the missing fact.
3. **Incomplete but determinate.** A fact is missing, but the outcome does not flip across its range. **The model should answer anyway.** A `cannot_determine` here is a needless abstention and scores zero.

Class 3 is the half the original heuristic could not see, and it is what makes the benchmark hard to game by always abstaining.

Two measured cautions for whoever builds the prober:

- **Determinability plateaus.** Shelter cost at 0 → SNAP $93/mo; at 12,000 → $306; at 36,000 → $306. Past the deduction cap the fact stops mattering. The sweep range must be declared and justified per fact, and recorded with the label.
- **Effects can run counterintuitively.** Omitting `immigration_status` defaults to CITIZEN → $93/mo, while `UNDOCUMENTED` → **$199/mo** (the parent is excluded from the unit and income is prorated, so the benefit goes *up*). Any generator assuming "missing status → lower benefit" will mislabel its own answer key. Never infer the direction; compute it.

The 25% T1b fraction in SPEC.md §4 is **provisional** and must not be fixed until the prober has shown which facts can actually carry class 2 on real generated households.

---

## Working conventions (SPEC.md §9)

- **Read the installed source before using an API. Never assume from memory, and do not trust the docs alone.** On this project the installed source has already contradicted both the documentation and SPEC.md's sketches repeatedly. When they disagree, the installed source wins; note the discrepancy.
- **Determinism everywhere.** Same seed, same output. Every household reproducible from `(seed, index)`. Tests enforce it; a determinism test is not optional for any new generator.
- **No real PII.** All households synthetic. No scraping of live government systems. No live portals.
- **Every ground-truth claim traces to a source** — a PolicyEngine variable name or a rules-table citation. If it traces to neither, it is not ground truth and cannot be scored.
- **Honest limits over impressive claims.** An approximate verifier is labeled approximate. Anything that could not be validated says so in `docs/LIMITS.md`. Negative and awkward results go in the writeup, not the footnotes.
- **Small commits, each with passing tests. Show the test output, don't describe it.** A claim that something works is not a substitute for the output proving it.
- **When uncertain about a rule's legal meaning, add it at `low` confidence and flag it for the reviewer.** Do not guess. Claude never promotes a rule's confidence on its own: every rule starts at `medium`, and only the human reviewer promotes to `high` after checking the citation. `low`-confidence rules are excluded from scoring until promoted.
- **Stop at every checkpoint and wait.** "Stop" means stop and report — not stop and quietly start the next phase's prep work.

---

---

## Modelled programme take-up: suppressed globally **[decided]**

**The problem.** PolicyEngine models a household as *receiving* the programmes it would
be eligible for, and those receipts count as unearned income for SNAP. A zero-earnings
California household with a child is modelled as receiving CalWORKs at **$930/month**; a
zero-earnings 67-year-old is modelled as receiving SSI at **$967/month**. The narrative
states neither. Without intervention the answer key for every generated household
silently depends on a take-up assumption the agent cannot see — an undeclared
determinability problem underneath the entire benchmark. When suppression was first
applied, **4 of the 10 checkpoint households changed answer key**, which measures how
much of the benchmark was resting on it.

**Decision: suppress modelled take-up globally** (`redtape/oracle/takeup.py`), and state
the scope limit in the README. PolicyBench avoided this by excluding take-up entirely;
we follow that precedent.

**Why not the alternative** (state actual receipt of each cash-aid programme in every
narrative): it fails *open*. You would have to state receipt for CalWORKs, SSI, the CA
state supplement, Social Security, unemployment, and every other modelled programme —
and any one you forget silently reintroduces the hidden assumption for exactly the
household shapes that trigger it. Suppression fails *closed*: the invariant catches a
programme nobody thought of. The leak is shape-dependent — CalWORKs appears only with a
child present, SSI only once someone is old enough — so "we listed them all" is not a
claim anyone can verify by inspection.

**Not permanent.** Stating take-up as an explicit fact is a good v1 feature: it becomes a
new determinability axis rather than a hidden assumption. It is the wrong thing to do
half-way in v0.

**A declared list is necessary but not sufficient.** `SUPPRESSED_PROGRAMS` can only
suppress programmes we thought of. The real guard is `assert_no_unstated_income`, which
asserts the engine gave the household no income the narrative did not state, and runs on
every `compute()`. `test_invariant_has_teeth` removes the suppression and asserts the
invariant *fails* — without it, the take-up tests could pass because the invariant is
vacuous rather than because the leak is closed.

**The invariant is one-sided, deliberately.** SNAP legitimately *excludes* some stated
earnings — notably a student child's earned income (7 CFR 273.9(c)(7)). Engine earned
income *below* the stated figure is correct behaviour. Only invented income is a leak. A
two-sided version produced a false positive the moment a determinability sweep aged an
earner into childhood.

**Suppress imputation, permit declaration.** These are different things and the code
keeps them apart. Imputation — the engine deciding on its own that a household takes up a
programme — is always suppressed, because it is invisible to the agent and so can never
be part of a fair answer key. Declaration — the narrative stating that the household
receives a benefit, exactly as it states employment income — is always permitted and
passed through. An earlier version zeroed the variables outright, which conflated the two
and cost a real determinability axis.

**How disability actually works for SNAP.** 7 CFR 271.2 defines an elderly-or-disabled
member by *receipt of a qualifying benefit*, and PolicyEngine implements this as
`is_usda_disabled`, an OR over `gov.usda.disabled_programs`:

    is_ssi_disabled                          (bool — an SSI DETERMINATION)
    social_security_disability               (float — SSDI receipt)
    is_permanently_disabled_veteran          (bool)
    is_surviving_spouse_of_disabled_veteran  (bool)
    is_surviving_child_of_disabled_veteran   (bool)

Three consequences, all measured (`scripts/probe_decisive.py`), 2-person household,
$1,500/mo earned, $2,500/mo rent, CA, 2025-04, FFY2025 shelter cap $712:

| declared | shelter deduction | SNAP $/mo | elderly-or-disabled? |
|---|---|---|---|
| nothing | 712.00 (capped) | 450.00 | no |
| `is_disabled=True` only | 712.00 (capped) | 450.00 | **no** |
| SSI **amount** $967/mo | 712.00 (capped) | 160.00 | **no** |
| **SSDI $1,200/mo** | 2,647.00 (uncapped) | **536.00** | **yes** |
| **disabled veteran** | 2,647.00 (uncapped) | **536.00** | **yes** |
| age 60 | 2,647.00 (uncapped) | 536.00 | yes (elderly) |

1. A self-reported `is_disabled` flag is inert. It always was — it is not what the
   regulation keys on.
2. **Declaring an SSI dollar amount does not establish disability**, because `ssi` is not
   in `gov.usda.disabled_programs`; only `is_ssi_disabled`, the determination, is. A
   narrative saying "receives $967/month in SSI" does *not* make the household
   elderly-or-disabled. It is still decisive, but through the income channel and in the
   opposite direction ($450 → $160).
3. **Declaring SSDI receipt or veteran disability status does establish it**, and the
   axis returns: the excess-shelter-cap exemption applies and the benefit moves
   $450 → $536.

So disability **is** a usable T1b fact for SNAP, provided the narrative states the right
thing. Age 60 is decisive independently, via `is_usda_elderly` (threshold 60).

---

## Rules table: every citation is read before it is written **[decided]**

**Why this changed.** Three of the ten seed citations were wrong — a **30% error rate** —
and only one had been flagged as doubtful. The other two were believed correct and were
not; reading the actual section text found them (`docs/LIMITS.md` §10). Drafting from
memory of a regulation's structure and spot-checking a sample does not work at an
acceptable error rate.

Consequently:

- **The full rules table cannot be drafted and spot-checked.** Every rule requires its
  primary source opened and read before the rule is written. No exceptions for rules that
  look obvious — two of the three errors were in rules that looked obvious.
- **`medium` is no longer an acceptable default for an unread citation.** `medium` now
  means "the cited section has been read and the rule matches it, but the interaction
  with California's manual is unverified". A rule whose citation has not been read is
  `low`, and `low` is excluded from scoring.
- **The rules phase gets a longer budget than SPEC.md §7 assumes**, and it is its own
  phase with its own checkpoint. Recorded here so the timeline is not quietly compressed
  later. If the schedule is under pressure, cut the number of rules, never the reading.
- **Prefer LSNC (`calfresh.guide`) as the primary California source.** It was reachable,
  is well-cited to the MPP and the CFR, and is maintained. eCFR, USDA FNS, and CBPP all
  blocked or timed out; Cornell LII works for federal regulation text.

---

## Oracle freshness is a structural risk **[decided]**

PolicyEngine implements `is_snap_abawd_hr1_in_effect` but not the HR 1 SUA changes, so
its coverage of major legislation is **partial and lagging by an unknown amount**. That is
a risk to the "PolicyEngine as ground truth" thesis itself, not a one-off defect.

`tests/test_parameter_drift.py` asserts the engine's parameter values against externally
published figures — allotments, standard deduction, shelter cap, SUA, homeless shelter
deduction, income-limit multipliers, elderly threshold, fiscal-year boundary — and **fails
the build on divergence**. This gives continuous evidence of where the oracle is stale
instead of discovering it case by case.

Two rules for that file:

- **Every expected value must come from an external published source, never from the
  engine.** A value copied from the engine makes the test self-confirming and worthless.
  Where no external source exists — FFY2026 standard deduction for sizes 4+ — the cell is
  deliberately absent rather than filled in from the engine.
- **`test_known_divergence_hr1_sua_still_unmodelled` asserts the current, known-WRONG
  behaviour on purpose.** When upstream implements the rule, that test fails, which is the
  notification we want. Do not "fix" it by changing the assertion.

---

## No automated PDF table extraction without a second source **[decided]**

An automated extraction of the FNS FY2026 allotments PDF returned a $688 shelter cap, a
$193 standard deduction, and allotments 291/535/768/**1,023**/1,219/… — matching no other
source. Government benefit PDFs routinely carry separate tables for the 48 states + DC,
Alaska, Hawaii, Guam and the USVI; an extraction that does not respect column boundaries
splices values across jurisdictions, which is exactly how $1,023 appeared where $994
belongs.

**Rule: no figure obtained by automated PDF table extraction enters the validation corpus
without a second independent source confirming it.** A human reading the PDF counts as
that source; a second automated extraction of the same document does not.

This is the same failure mode as a silent default standing in for a real value, one layer
up: a plausible number appears where a real one belongs, and nothing errors. The whole
project exists to catch that class of mistake, so we do not get to make it in our own
evidence base.

---

## Only externally validated cells are scored **[decided]**

`SCORED_PROGRAMS = ("snap", "eitc", "ctc")`. **Medicaid is computed, recorded with
provenance, and NOT scored** — it has no external validation and none was obtainable.
Scoring a cell backed only by the engine agreeing with itself is the circularity this
project exists to avoid, and the thesis is deterministic ground truth, so an unvalidated
scored cell would undercut the whole claim.

The rule generalises: **a program enters the scored answer only once an external source
confirms it.** Adding one is a deliberate step with its own validation, never a
side-effect of the engine happening to produce a number.

**Report what is received, not what is owed.** `ctc` is the gross credit before
limitation; `ctc_value` is what the household actually gets. A zero-income family with
two children has `ctc = 4,400` and `ctc_value = 0`. The scored answer uses `ctc_value`,
with the gross kept alongside in `gross_entitlement`. This is the same trap as `medicaid`
(a dollar amount) versus `is_medicaid_eligible` (a boolean): the plausible-looking
variable is not the one that answers the question.

---

## Deferred design decisions

**T1b abstention scoring.** T1b abstention scoring is currently specified heuristically (omit a required fact, reward "cannot determine"). The intended Phase 2 upgrade is SMT-based determinability: encode the program's eligibility rules as constraints, treat missing facts as free variables, and check whether the known facts are satisfiable with BOTH eligible and ineligible outcomes. If both are satisfiable, abstention is the provably correct answer and the model difference names the deciding fact. Do not implement yet; revisit before Phase 2.

The perturbation prober described above is the interim stand-in. It is a finite-sample *under-approximation* of that check: it can prove a fact is deciding (it found a flip) but cannot prove one isn't (it only sampled). Label it approximate in `docs/LIMITS.md` and keep its output shape compatible with the SMT version, which will answer the same question exactly.

---

## Repo-specific notes

- Layout follows SPEC.md §3 with one change: **`redtape/verifiers/` is renamed `redtape/scoring/`** **[decided]**, to avoid shadowing the installed `verifiers` package. Do not reorganize further without raising it first.
- **The rules table is its own phase with its own checkpoint** **[decided]**, not part of Phase 1. Phase 1 delivers only the schema, the linter, and ~10 seed rules proving the format. `rules/verification_requirements.yaml` is the highest-risk artifact in the project (SPEC.md §6); it gets reviewed unhurried, never under phase-schedule pressure. Changes to it require re-running the rules lint and re-checking `REVIEW_CHECKLIST.md`.
- `data/heldout/` is never committed. The private seed lives in a **gitignored `.env` at repo root** **[decided]**.
- License: **Apache-2.0** **[decided]**.
- API keys and evaluation budget are **deferred to Phase 3** **[decided]**; nothing before then should require a model call.
