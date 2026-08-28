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
./.venv/bin/python -m pytest tests/ -q      # 27 tests
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

3. **The oracle has no external validation yet.** The engine's own 1,310 shipped
   fixture assertions pass, but that is circular evidence. Independently-sourced worked
   examples are an open item — see `docs/LIMITS.md` §7.

## Rules table confidence

10 rules · high **0** · medium **6** · low **4** · scored (excludes `low`) **6**

No rule is at `high`. Claude drafts rules and never promotes their confidence; only the
human reviewer does, via `rules/REVIEW_CHECKLIST.md`.

## License

Apache-2.0.
