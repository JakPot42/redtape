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
./.venv/bin/python -m pytest tests/ -q      # 76 tests
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

3. **The oracle is externally validated for SNAP amounts in California only, and
   narrowly.** 22 comparisons against published CalFresh tables match exactly — but only
   9 of those exercise calculation logic (sizes 1–6, FFY2025, months before 2025-07-04);
   8 test parameter loading and 5 are direct parameter reads. **Medicaid, EITC, CTC and
   every eligibility boolean have had no external comparison of any kind.** See
   `docs/LIMITS.md` §7 for the exact cells.

4. **Modelled programme take-up is suppressed.** PolicyEngine models a household as
   receiving the benefits it qualifies for — CalWORKs at $930/mo for a parent with a
   child, SSI at $967/mo for a senior — and those count as SNAP unearned income. The
   narrative states neither, so the answer key would depend on an assumption the agent
   cannot see. v0 answers "what would this household receive given only the stated
   facts", not "what does it actually receive". `docs/LIMITS.md` §12.

5. **HR 1's 2025 SUA changes are not modelled by the engine.** California's
   `always_standard` utility-allowance flag is unchanged across both the 2025-07-04 and
   2025-10-31 effective dates. Disclosed as a scope limitation with the affected months
   named, not scored against the oracle. `docs/LIMITS.md` §11.

## Rules table confidence

10 rules · high **0** · medium **6** · low **4** · scored (excludes `low`) **6**

Three citations were found to be wrong while checking them against the regulation text and have been corrected (`docs/LIMITS.md` §10). No confidence level was changed.

No rule is at `high`. Claude drafts rules and never promotes their confidence; only the
human reviewer does, via `rules/REVIEW_CHECKLIST.md`.

## License

Apache-2.0.
