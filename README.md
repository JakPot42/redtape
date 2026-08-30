# Redtape v0

Verifiable training-and-evaluation environments for US public-benefits work.

**Status: Phase 1 complete, awaiting Checkpoint 1 review. Nothing here is published or
claimed as validated.** See [`docs/LIMITS.md`](docs/LIMITS.md) before citing any number
in this repo.

## What this is

Existing benchmarks (PolicyEngine's PolicyBench, Column Tax's TaxCalcBench, Stanford's
HealthAdminBench) score whether a model computes the right *amount*. Redtape adds the
harder half: whether an agent knows **which facts are required, which are missing, and
when it cannot answer** — which is where real filings fail.

Three task families are planned (SPEC.md §4). Phase 1 built the oracle, the generator,
and the machinery that makes the abstention task labelable.

## What exists after Phase 1

| component | what it does |
|---|---|
| `redtape/schemas.py` | Households and answers. Every answer field tags its period; a fact is present or explicitly withheld, never absent. |
| `redtape/oracle/policyengine_oracle.py` | The only code that touches PolicyEngine. Refuses to answer a household with a withheld fact rather than let the engine substitute a default. Attaches provenance to every value. |
| `redtape/oracle/determinability.py` | Perturbation prober. Sweeps a withheld fact across a declared range and labels the case determinate / indeterminate / incomplete-but-determinate. |
| `redtape/generator/households.py` | Seeded generator. Reproducible from `(seed, index)` alone. |
| `redtape/oracle/takeup.py` | Suppresses imputed programme take-up while passing declared receipt through. The invariant, not the declared list, is the guard. |
| `redtape/scoring/rules_lint.py` | Rules-table linter. Fails the build if a rule reaches `high` confidence without reviewer sign-off. |
| `rules/verification_requirements.yaml` | **Ten-rule seed set only**, to prove the format. Not the finished table. |

## Reproduce

Requires **WSL2 / Linux** and **Python 3.13** — `verifiers` rejects 3.14, and this
project's determinism claims depend on the pinned interpreter.

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

./.venv/bin/python scripts/smoke.py         # one household, with provenance
./.venv/bin/python scripts/checkpoint1.py   # ten households + determinability table
./.venv/bin/python -m pytest tests/ -q      # 136 tests
./.venv/bin/python scripts/external_validation.py   # 10 published-table comparisons
./.venv/bin/python -m redtape.scoring.rules_lint rules/verification_requirements.yaml
```

## Three findings worth knowing before reading the code

1. **An annual query on a monthly `stock` variable returns December alone.**
   `is_snap_eligible` is such a variable, so asking it for "2025" silently reports
   December — not any month, not all months. SNAP is therefore always queried at an
   explicit month and always scored monthly. Locked by a regression test.

2. **PolicyEngine has no representation of "unknown."** Every omitted fact silently
   becomes a plausible default (`employment_income`→0, `age`→40, `state_name`→CA,
   `immigration_status`→CITIZEN). Omitting a fact and stating it as zero are
   indistinguishable to the engine. This is why abstention labels come from the
   perturbation prober rather than from the oracle.

3. **Only externally validated programs are scored.** `SCORED_PROGRAMS` is
   `("snap", "eitc", "ctc")`. **Medicaid is computed and recorded but NOT scored** — it
   has no external validation and none was obtainable, and scoring a cell backed only by
   the engine agreeing with itself is the circularity this project exists to avoid. That
   costs T1 its only per-person eligibility output; stated plainly rather than papered
   over. `docs/LIMITS.md` §20.

   EITC and CTC are now validated against published IRS figures across phase-in, plateau
   and phase-out for 0/1/2/3+ children. One correction fell out of it: `ctc` is the
   **gross** credit, not what the household receives — a zero-income family with two
   children has `ctc = 4,400` and `ctc_value = 0`. The scored answer uses `ctc_value`.
   `docs/LIMITS.md` §21.

4. **SNAP validation is narrower than the headline count suggests.** 22 comparisons against published CalFresh tables match exactly — but only
   9 of those exercise calculation logic (sizes 1–6, FFY2025, months before 2025-07-04);
   8 test parameter loading and 5 are direct parameter reads. **Medicaid, EITC, CTC and
   every eligibility boolean have had no external comparison of any kind.** See
   `docs/LIMITS.md` §7 for the exact cells.

5. **Imputed programme take-up is suppressed; declared receipt is permitted.** PolicyEngine models a household as
   receiving the benefits it qualifies for — CalWORKs at $930/mo for a parent with a
   child, SSI at $967/mo for a senior — and those count as SNAP unearned income. The
   narrative states neither, so the answer key would depend on an assumption the agent
   cannot see. A narrative *may* state benefit receipt, exactly as it states earnings, and
   that is passed through. v0 answers "what would this household receive given only the
   stated facts". `docs/LIMITS.md` §12.

6. **HR 1's 2025 immigrant eligibility restrictions are not modelled**, and this one
   affects eligibility rather than amounts. Refugees, asylees, people with deportation
   withheld, conditional entrants and one-year parolees are still treated as fully
   eligible. The corpus is **restricted** to statuses where the engine and published rules
   agree; refugee and asylee households were previously generated and have been removed.
   `docs/LIMITS.md` §16.

7. **HR 1's 2025 SUA changes are not modelled by the engine.** California's
   `always_standard` utility-allowance flag is unchanged across both the 2025-07-04 and
   2025-10-31 effective dates. Disclosed as a scope limitation with the affected months
   named, not scored against the oracle. `docs/LIMITS.md` §11.

## Rules table confidence

10 rules · high **0** · medium **6** · low **4** · scored (excludes `low`) **6**

Three citations were found to be wrong while checking them against the regulation text and have been corrected (`docs/LIMITS.md` §10). No confidence level was changed.

No rule is at `high`. Claude drafts rules and never promotes their confidence; only the
human reviewer does, via `rules/REVIEW_CHECKLIST.md`.

## Known upstream divergence

`docs/HR1_SUA_DIVERGENCE.md` is a draft report for the PolicyEngine maintainers: HR 1's
2025 SUA changes are not implemented for California, with evidence, affected month ranges,
and a proposed fix. Not yet filed.

`tests/test_parameter_drift.py` asserts engine parameters against published figures and
fails the build on divergence, so staleness surfaces continuously rather than case by case.

## License

Apache-2.0.
