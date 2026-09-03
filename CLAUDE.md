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

Reference versions: `verifiers==0.3.1`, `policyengine-us==1.821.4`, `policyengine-core==3.31.1`, `pandas==3.0.5`, `numpy==2.5.2`. `policyengine-core` is a DIRECT pin as of 2026-09-01 and `uv.lock` is committed - see the bottom of this file.

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

## Every green signal must be checked for what it is NOT measuring **[decided]**

This is the standing principle, and it outranks any individual check below. It has now
been learned five times on this project, each time from a different direction, and each
time the failure looked exactly like success right up until someone asked what the signal
actually covered.

| # | The green signal | What it was not measuring |
|---|---|---|
| 1 | `medicaid` returns a plausible dollar amount | it is not `is_medicaid_eligible`; the boolean was what the question asked for |
| 2 | `ctc` returns 4,400 for a family with two children | it is the **gross** credit; `ctc_value` — what they receive — is 0 |
| 3 | a held-out split builds without error | it built from the **public** seed, so it had public provenance and was worthless |
| 4 | 183 tests pass locally | they passed because `python -m pytest` injects the cwd into `sys.path`. `eval/` was never importable; bare `pytest` — what CI runs — could not import it at all |
| 5 | CI reports "green" | `-q` in `addopts` cancelled `-v` in the workflow, hiding **5 skipped tests** on `test_env.py`, the module covering the real `verifiers.v1` env and scoring path |

**The general form.** A passing check reports on the region it covers and says nothing
whatsoever about the region it does not — but it is *read* as a statement about the whole.
The gap is invisible by construction, because the artifact that would reveal it is the one
that was not produced: the variable not queried, the file not built, the test not
collected, the line not printed.

**So the question is never "did it pass?" It is "what would still have passed if the thing
I care about were broken?"** If the honest answer is "this exact check", the check is
real. If it is "quite a lot", the green is decoration.

Practical obligations, all of which have caught something here:

- **Read the count, not the colour.** `202 passed, 5 skipped` is not `207 passed`. Any
  skip, xfail or deselect in a reported run must be surfaced with its reason (`pytest -rs`)
  and understood. A skip is a hole in the claim, not a neutral event.
- **Run the check the way the consumer runs it.** Local `python -m pytest` and CI's bare
  `pytest` are different programs with different `sys.path`. A signal that only holds under
  one invocation is a signal about the invocation.
- **Prefer a check that fails closed.** `resolve_seed` raises where the old code defaulted.
  A default is a green signal manufactured out of nothing.
- **Give the check teeth, then verify the teeth.** Break the thing on purpose and confirm
  the check goes red — `test_invariant_has_teeth`,
  `test_build_split_no_longer_contains_a_default_seed`, and the
  `test_known_divergence_hr1_sua_still_unmodelled` pattern all exist for this. An assertion
  never observed failing is an assumption.
- **Distrust a check whose expected value came from the thing under test.** The engine
  agreeing with itself is not validation; see "Only externally validated cells are scored".

**This principle is the thesis of the benchmark, applied to ourselves.** Redtape exists
because PolicyEngine answers every question plausibly and never says "I cannot determine" —
a system that is silently confident where it should abstain. Five times now our own tooling
has done the same thing to us. We do not get to ship a benchmark about undetectable
confident wrongness while running on undetectable confident greenness.

---

## Build the environment before scaling generation **[decided]**

`eval/` was written carefully, read carefully, and committed with an honest label saying it
had never been run. Running it found **eight defects. Six were invisible to reading.** Two
were not tooling bugs at all — they were product bugs that would have shipped in the
benchmark.

| # | defect | visible by reading? |
|---|---|---|
| 1 | `assert_publishable(seed=None)` defaulted, and every call site left it there — the text scan never ran once | no |
| 2 | `redact()` missed `pair_consistency.pairs[].pair_id`, so the seed survived redaction | no — found only once #1 was fixed |
| 3 | **`is_higher_ed_student` rendered only when true**, so a withheld boolean was textually identical to a stated false, making the eligibility-flip class unanswerable | no |
| 4 | **A withheld age deleted the person** — empty payload, `"No person found"`, and silent age/person misalignment in multi-person households | no |
| 5 | `tool_equipped_unknowns` marked only `housing_cost`, so all three conditions printed identical numbers | no |
| 6 | `--sample 12` returned 8, and could split a matched pair | no |
| 7 | the documented invocation (`python eval/run_eval.py`) never worked | yes, in principle |
| 8 | `oracle_agent` never abstains, so it capped at 0.375 on the abstention headline while reading as validation | yes, in principle |

**Why reading could not have caught most of these.** Every one of #1–#6 is an *absence*: a
scan that did not happen, a field nobody enumerated, a clause the renderer never emitted, a
person the parser never produced, a fact the agent never marked, a row the sampler never
picked. Reading code shows what it does. These defects were all in what it did not do, and
the artifact that would have revealed each one — the exception, the crash, the diverging
number — is exactly the artifact that only exists at runtime.

Note the pattern in #1 and #2: the broken guard *hid* the leak the guard existed to catch.
Fixing the first immediately surfaced the second. Layered defects do not appear one at a
time under inspection; they appear one at a time under execution.

**The rule.** Build and exercise the environment end-to-end on a small split **before**
scaling generation. Specifically:

- Run every mode of the eval harness against a committed dev fixture before any large build.
  Two 1200-task builds were killed mid-flight over defect #3; discovering it after
  publication would have meant retracting the benchmark's headline class.
- **Add a ceiling check whenever a metric is introduced.** `perfect_agent` scoring
  1.000/1.000/1.000 is the only evidence that a metric is achievable at all rather than
  accidentally unreachable, and `oracle_agent`'s 1.000 exact-match had been reading as that
  evidence while covering none of the abstention half.
- Treat "committed but never run" as **unvalidated**, and label it that way in the commit
  message. It was labelled `NOT YET EXERCISED`; that label was accurate and it was right to
  keep the code, but it is not a substitute for running it.

This is the strongest evidence in the project's history for the standing principle above.
Reading is how you form a hypothesis about code. Running it is how you find out.

---

## Tests that bypass the interface under test measure something else **[decided]**

The first live model run scored **0.000 on every headline metric**. It was not a model
result. It was a harness bug that 242 passing tests, five baselines, three tool conditions
and a 1.000/1.000/1.000 ceiling check had all failed to detect, and it would have shipped as
a published finding about frontier model capability.

**What was wrong.** `SYSTEM_PROMPT` instructed the model to "Answer with a single JSON object
and nothing else" and **named not one field**. The model returned well-reasoned, arithmetically
correct answers using `monthly_benefit`, `annual_amount` and `period` where `T1Answer`
requires `benefit`, `amount` and `period_label`. Every response was rejected as
`schema_invalid`. The metric measured whether a model can guess our field names.

**Why nothing caught it.** Every baseline in `eval/baselines.py`, every scripted tool agent,
`oracle_agent` and `perfect_agent` all construct a `T1Answer` **object, in Python**, and
serialise it with `.model_dump_json()`. They are structurally incapable of producing a schema
mismatch. So the suite exercised:

    T1Answer object -> JSON -> parser -> scorer          (covered many times over)

and never once exercised the only path a real model takes:

    SYSTEM_PROMPT -> model -> JSON -> parser -> scorer   (covered zero times)

The prompt was an untested input. `parse_answer` was tested against strings *we* generated
from the very class it parses into, which is a tautology dressed as a test: it can only fail
if serialisation and deserialisation disagree with each other, never if the prompt and the
schema disagree.

### The general form

**A test that constructs its input on the far side of the interface under test is not testing
that interface.** It is testing the code *after* it, using a fixture that is correct by
construction. The interface itself — the contract with whatever is outside the system — stays
unmeasured, and every metric downstream of it silently reports on a path nobody takes.

The tell is worth learning to see: **if your fixture is built by the same code that consumes
it, the fixture cannot be wrong in the way the real input can.** Here the "real input" is a
language model reading English and deciding on a JSON shape; the fixture was a Pydantic model
calling its own serialiser. Those two things can never disagree, which is exactly why the
test suite was quiet.

This is the sharpest instance of the standing principle above. The other instances were green
signals that covered less than they appeared to. This one covered a *different thing entirely*
and still read as coverage — 242 tests genuinely testing the scoring path, presented as
confidence in a pipeline whose first stage had never run.

### Obligations

- **At least one test must traverse every external interface end to end**, with input produced
  the way the outside world produces it — not by our own constructors. For this project that
  means a fixture of raw model-shaped *text* going through `parse_answer`, not a `T1Answer`
  round-trip.
- **Any prompt that specifies a format is code, and drifts like code.** `SYSTEM_PROMPT` now
  renders the answer shape from `T1Answer` itself (`_answer_shape()`), so a schema change
  updates the prompt automatically rather than leaving the two to diverge silently. A
  hand-written schema in a prompt is a copy waiting to go stale.
- **Ask what the fixtures cannot express.** A fixture set that cannot represent the failure
  mode you care about will never report it. Before trusting a suite, name the malformed input
  it is incapable of constructing.
- **A ceiling check does not cover this.** `perfect_agent` scoring 1.000 on all three
  headlines was real evidence that the metrics are achievable — and it was produced by
  building a `T1Answer` in Python, so it says nothing whatsoever about whether a model can
  produce one. Two different claims; only one was tested.

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

## v0 ships T1 and T1b only **[decided]**

**T2 (documentation completeness) is deferred to v1. T3 (intake) is the candidate second
family if T1b lands early.**

**Why.** The novelty is determinability, not documentation. T2 depends entirely on the
rules table, which today is ten seed entries, none at `high` confidence, with a measured
**30% citation error rate** and a review process we have already agreed is slower than
SPEC.md §7 assumed. A shallow T1b is worse than no T1b: a benchmark that scores abstention
badly is one someone will publicly take apart, and the abstention claim is the entire
reason this project is interesting. Shipping without T2 costs scope and nothing else.

**T3 before T2 if there is room.** T3 (intake) is conceptually closer to T1b than T2 is —
both are about knowing what you don't know, where T2 is about matching documents to a
rules corpus. If T1b lands early, T3 is the better second family.

The rules table stays alive as a **slow parallel track** — a few rules a week, every
citation read before the rule is written. It is the natural v1 and a compounding asset;
it is simply not on the v0 critical path.

---

## Sweep every scored variable to BOTH extremes before trusting it **[decided]**

Two bugs of the same shape have been found in this schema, and both survived review:

- `ctc` reports $4,400 for a zero-income family with two children who receive $0.
- `medicaid` is a dollar amount where `is_medicaid_eligible` (a boolean) was meant.

Both were invisible mid-range and separated only at an extreme. Both had a plausible
variable name, which is exactly why reading the code did not catch them. **Zero income and
very high income are where refundability, phase-outs and caps split gross entitlement from
received value.**

`tests/test_extreme_sweep.py` makes this mechanical: it sweeps every input dimension to
both ends, compares each oracle variable against same-type siblings the engine defines,
and **fails on any divergence not in its `EXPLAINED` registry**. A new scored field with no
extreme coverage also fails. Adding to `EXPLAINED` requires writing down the reason — the
point is that a human decides, not that the test goes quiet.

Its limits, stated so nobody over-trusts it: it can only compare variables it can *name*
as siblings, and it compares like types only, so a gross/received pair under an unrelated
name would still slip through. It narrows the class; it does not close it.

---

## Finding eligibility flips: look for composition rules, not income tests **[decided]**

Eligibility-flipping facts are the scarcest and most valuable T1b class, because they
cannot be reached by adjusting an amount. Three routes were probed and the pattern that
emerged generalises:

| route | flips eligibility? | why |
|---|---|---|
| gross income test exemption (elderly/disabled) | **no** | broad-based categorical eligibility already waives the gross test below the net threshold, and the exemption does not reach the *net* test above it |
| ABAWD time limits | **no** | California is a waived area and delayed HR 1 adoption |
| **student status** (7 CFR 273.5) | **yes** | it is a *composition* rule, not an income test |

**The heuristic: income-based routes get absorbed by categorical eligibility; composition
rules survive it.** BBCE and similar waivers operate on income tests, so anything
expressed as an income threshold tends to be neutralised. Rules about *who counts as a
household member* — student status, immigration status, institutional residence — are
applied before and independently of income, so they still bite.

Apply this when looking for flips in another state or program: ask whether the rule
removes a person from the unit, or merely changes a number.

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

---

## Dev fixtures may be committed; held-out data never is **[decided]**

The two are not the same kind of artifact and the `.gitignore` must not treat them alike.

**Committable: anything derived from the PUBLIC dev seed** (`DEV_SEED = 20260828`). It is
public by construction, deterministic, and reproducible by anyone from the seed alone, so
committing it discloses nothing that was not already disclosed. `data/dev/t1_smoke.jsonl`
(28KB) is committed for exactly this reason: without it, five tests in `tests/test_env.py`
skip on every CI run, and those are the tests covering the real `verifiers.v1` env and
scoring path — the code most likely to break silently. Five permanently skipped tests on
the most important module is a worse trade than three minutes of engine warm-up per run.

**Never committable, under any circumstances: anything derived from
`REDTAPE_HELDOUT_SEED`.** `data/heldout/` in full, any manifest naming the seed, any raw
results file from a held-out run. Publishing held-out numbers goes through `redact()` and
the `.public.json` it writes — never by committing an artifact and trusting a reader not
to look at the wrong field.

The test for which side a file falls on is one question: **could someone regenerate this
from information that is already public?** If yes, committing it costs nothing. If no, it
does not go in the repository, and a `.gitignore` entry is a backstop rather than the
mechanism.

---

## Held-out seed: established 2026-09-01 **[decided]**

**The private seed did not exist until 2026-09-01.** `.gitignore` had reserved `.env` from
the very first commit, and both this file and SPEC.md §5 described the seed as living
there — but the file was never created. `scripts/build_split.py` *used to* read
`REDTAPE_SEED` from the environment and, when it was absent, **fall back silently to the
public default `20260828`** — the same seed as the dev split. So a held-out build with no
`.env` did not fail; it produced a split with public provenance and called it held-out.
That is the third appearance of this project's own pathology in our own tooling, after
`medicaid` (a dollar amount where a boolean was meant) and `ctc` (gross entitlement where
received value was meant): a plausible value standing exactly where the real one belongs,
with nothing raised. It is fixed below.

**Any held-out split generated before 2026-09-01 is void** and must be regenerated. In
fact none was: `data/heldout/` was empty and `results/` had never been written, so nothing
was published and no number is retracted. The window closed before it cost anything.

The seed established on 2026-09-01 is an 18-digit CSPRNG value (`secrets`), stored in
`.env` at repo root, mode `0600`. Its fingerprint is

    sha256(str(seed))[:16] = b80dea37628d57fe   # in use, but EXPOSED 2026-09-02
                                                 # rotate before publishing anything
                              ab72ee672111f7fe   # VOID, rotated out 2026-09-02

Quote the **fingerprint** to identify which seed a run used. Never quote the seed.

### Rotated 2026-09-02 after partial exposure **[decided]**

**The seed established on 2026-09-01 was rotated on 2026-09-02 and is void.** So is
**any held-out split generated before the 2026-09-02 rotation**, in addition to anything
generated before the 2026-09-01 establishment. The held-out split was rebuilt from the new
seed the same day.

| | fingerprint |
|---|---|
| established 2026-09-01, now VOID | `ab72ee672111f7fe` |
| current, from 2026-09-02 | `b80dea37628d57fe` |

**What was exposed, precisely.** Not the seed. While verifying that `.env` loaded, a
household id was printed truncated — `hh-72959...` — as evidence that the id embeds the
seed. Since `household_id` is `f"hh-{seed}-{index:05d}"`, that disclosed the **leading 5 of
18 digits**, cutting the search space from ~9x10^17 to ~10^13. Confirmed on rotation: the
old seed did begin `72959`.

10^13 is not trivially brute-forceable, and reconstructing a split from a candidate seed
requires a full oracle pass per household, so the practical exposure was small. Rotation
was still the right call: the value of a private seed is entirely in its being private, a
partial disclosure is not a *kind* of privacy, and the file exists precisely to keep this
value out of transcripts.

**The mistake was mine and it was a demonstration, not an accident of tooling.** The
redaction machinery worked exactly as designed throughout — `redact()` stripped the seed
from every results file, `assert_publishable` scanned for it, `.gitignore` kept `.env` out
of every commit, and no seed-derived identifier ever reached the repository. The leak
happened in a place none of that covers: **a diagnostic print in a terminal.**

Two rules follow, and they are the point of this entry:

- **Never print a seed-derived identifier, truncated or not.** `household_id`, `pair_id`,
  and any future `f"...{seed}..."` string are seed material. A prefix is seed material.
  Use `seed_fingerprint()`, which exists for exactly this and discloses nothing.
- **Redaction covers artifacts, not conversation.** Every guard in this repo protects files
  that get written and committed. None of them sees a `print()`. The channel with no
  automated guard on it is the one a human is watching, which makes it the channel where
  discipline has to be manual — and the one where a plausible-looking, "obviously safe"
  truncation slips through. Same pathology as everywhere else in this project: the
  protection covered the region everyone was looking at, and the gap was outside it.

### Exposed a SECOND time 2026-09-02 — rotation DEFERRED, not skipped **[decided]**

**The seed established on 2026-09-02 (`b80dea37628d57fe`) is exposed.** The full contents of
`.env` were pasted into a chat log on 2026-09-02, which discloses the seed in its entirety —
not a prefix this time, the whole value.

**Rotation is deferred, not skipped.** This is a deliberate scheduling decision, not a
judgement that the exposure does not matter. It carries a hard gate:

> **The seed MUST be rotated before any held-out number is published or shared, and the
> held-out split MUST be regenerated from the new seed. Until both are done, the current
> held-out split is VOID FOR PUBLICATION.**

Rotating the seed alone is not sufficient. The split is a *function of* the seed, so the
existing `data/heldout/t1.jsonl` remains reproducible by anyone holding the exposed value.
It has to be rebuilt, and the new task hashes re-checked for zero overlap with dev.

**What the split may still be used for in the meantime:** local development, pipeline
testing, and anything whose output stays on this machine. What it may not be used for is any
number that leaves it — a paper, a leaderboard entry, a README results table, a message to a
lab, a screenshot. The distinction is disclosure, not correctness: the split is not *wrong*,
it is merely no longer *private*, and a held-out split that is not private is not held out.

**Exposure history, kept in full because the pattern is the finding:**

| date | seed fingerprint | what was disclosed | how |
|---|---|---|---|
| 2026-09-01 | `ab72ee672111f7fe` | leading 5 of 18 digits | a truncated `household_id` printed as diagnostic output |
| 2026-09-02 | `b80dea37628d57fe` | the entire seed | the full `.env` pasted into a chat log |

**Both exposures happened in conversation, and neither was caught by any automated guard.**
`redact()`, `assert_publishable`, and `.gitignore` all worked correctly throughout, on both
occasions — because all three protect *artifacts*, and both leaks were in *transcript*. The
rule already recorded above ("never print a seed-derived identifier, truncated or not") was
necessary and insufficient: it constrains what this tooling prints, and says nothing about
what a human pastes.

So the standing conclusion is stronger than the first entry implied. **A secret that is
routinely read by a person during debugging will eventually be pasted by that person.** The
durable fix is not more discipline about printing; it is to stop the seed needing to be
looked at. Concretely, for the next rotation: generate it in place, never display it, and
make `seed_fingerprint()` the only thing any tool, log, or human ever has cause to quote.

**Also exposed in the same paste: `ANTHROPIC_API_KEY`.** That one is already dead — it was
the July 7 rotated-out key and now returns 401 — so the practical damage is nil, but it
should be treated as burned and never reinstated.

### The three consequences, all now closed

**1. `.env` auto-loads.** `redtape/config.py::load_dotenv` reads it with the standard
library — no `python-dotenv`, because adding a pinned dependency to parse `KEY=VALUE` buys
nothing. It never overrides a variable already in the environment: the auto-load exists to
stop a *forgotten* export falling back silently, not to overrule a deliberate one.

**2. The held-out path fails closed.** `resolve_seed("heldout")` raises
`MissingHeldoutSeed`. There is no default and no `--seed` override — a seed typed on a
command line can be typed wrong and ends up in shell history. The two seeds also have
**separate variable names**, `REDTAPE_SEED` (dev, public) and `REDTAPE_HELDOUT_SEED`: one
variable plus an "is this held-out?" flag would still let a single value serve both roles.
`build_split.py` now takes `--split {dev,heldout}` and refuses to write a held-out split
anywhere but `data/heldout/`.

`tests/test_seed_discipline.py` (20 tests) covers it, including two with teeth:
`test_build_split_no_longer_contains_a_default_seed` asserts the literal `20260828` is
absent from the script, because the fix is the *absence* of a constant and no ordinary test
can see that; and an end-to-end test asserting the CLI exits non-zero **before the engine
warm-up**. Both were verified to fail when the fallback was temporarily reintroduced.

**3. Redaction is the mechanism; gitignore is the backstop.** `eval/metrics.py::redact`
strips `run.seed` and every seed-derived identifier; `assert_publishable` re-checks by
scanning the serialised text, deliberately rather than by field checklist — a checklist
only covers fields somebody remembered, and the failure being guarded against is a field
nobody thought of. `eval/run_eval.py::write` now emits a `.public.json` beside every
results file **always, not only for held-out runs**: a check that has to fire on the right
run is a check that eventually does not, and redacting a public dev run costs nothing.

Pairs are **renumbered, not hashed**. A hash of `pair-{seed}-{index}` is invertible by
anyone willing to enumerate seeds and hash a short string over a small known index range,
so hashing would look like protection while offering roughly none.

Quote numbers out of `.public.json`. Quote the seed nowhere; `seed_fingerprint()` gives a
publishable identifier.

---

## Determinism is a test now, not a paragraph **[decided]**

CLAUDE.md said to re-run the determinism check after any dependency bump and
`docs/LIMITS.md` §5 said it was "re-checked in CI". **Neither was true.** There was no CI,
no test, and no record of the household behind the published values (`snap` 969.0, `eitc`
4328.0, `ctc` 2200.0, `household_net_income` 32658.21) — so the check could not be re-run
by anyone, including us. `test_oracle_is_deterministic` asserts `compute(hh) == compute(hh)`
within one process, which returns a new value twice and passes after any version bump.

A documented control that does not exist is worse than no control, because work gets
approved on the strength of it.

- `tests/data/determinism_reference.json` — five households serialised **in full**, each
  with its exact oracle output and the versions that produced it. Stored in full rather
  than as `(seed, index)` so an engine change and a generator change cannot mask one
  another; `tests/test_determinism.py` checks each separately.
- Comparison is **exact, with no tolerance**. The ±$1 SNAP tolerance says what a *model*
  may get wrong; the engine reproducing its own arithmetic gets no slack.
- `.github/workflows/tests.yml` runs the suite on Linux/3.13 on every push, with
  `uv sync --frozen` so a lockfile disagreement fails rather than silently re-resolving.
  **If that workflow is ever removed, the CI claim in `docs/LIMITS.md` §5 goes with it in
  the same commit.**
- **Re-capturing the fixture is a decision, never a build step.** If it stops matching,
  that is the finding it exists to surface. Re-capturing to make the test pass deletes the
  evidence.

### CI is green but not complete: 5 tests skip there

The first fully green CI run read **202 passed, 5 skipped**, against 207 passed locally.
The five are in `tests/test_env.py`, which needs `data/dev/t1_smoke.jsonl` — a **gitignored**
file, so it never exists on a fresh checkout. Those are the tests that exercise the real
`verifiers.v1` env and scoring path, which makes them among the more valuable in the suite.

The workflow runs `pytest -rs` so the skips are always printed with their reason. That
stops the gap being silent; it does not close it. **CI green currently means "202 of 207",
and nobody should read it as more than that.**

Two ways to close it, and it is a decision rather than an obvious fix:

- **Build the smoke split as a CI step.** Keeps the artifact out of git, costs roughly
  three extra minutes per run (the engine cold import dominates).
- **Commit `data/dev/t1_smoke.jsonl`.** It is 28KB, deterministic, and built from the
  *public* dev seed, so nothing about it is sensitive. It would mean revisiting the
  `.gitignore` line that currently excludes it.

Until one of those happens, this note is the record that the number is 202 and not 207.

The old cross-*platform* claim is not re-established and will not be: `verifiers.v1` cannot
import on Windows at all, so Linux is the only platform that can produce a shipping
artifact. Determinism is now enforced across *versions* on one platform, which is the axis
that actually threatens an answer key — `policyengine-us` ships three to four releases a
day.

**These expected values are engine output.** The test proves the engine still says what it
said, not that it is right. External validation stays a separate track whose expected
values never come from the engine (`test_parameter_drift.py`, `test_external_validation.py`).
A golden master cited as validation would be the exact circularity this project exists to
avoid.

---

## `policyengine-core` is pinned directly, and the bump was tested **[decided]**

The four direct dependencies were pinned exactly, but the transitive closure was not
locked, so `uv sync` after a cache clear floated `policyengine-core` **3.31.0 → 3.31.1**
with nothing to stop it. "Pin every dependency" has to mean the closure, not just the names
we wrote down.

- `uv.lock` is committed. It never had been.
- `policyengine-core` is now a **direct** exact pin in `pyproject.toml`.
- The bump was made deliberately, in the order the fixture makes possible: pin back to
  3.31.0 → capture the reference there → **207 passed** → move to 3.31.1 → **207 passed,
  oracle output byte-identical**. 3.31.1 is what is pinned now.

Reference versions: `verifiers==0.3.1`, `policyengine-us==1.821.4`,
`policyengine-core==3.31.1`, `pandas==3.0.5`, `numpy==2.5.2`, CPython 3.13.15.
