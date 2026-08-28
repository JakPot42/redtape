"""Phase 1 step 2 smoke test: one hard-coded California household.

Prints the source variable, entity, and period beside every value, because on this
project a number without its provenance is not ground truth (CLAUDE.md).

Run:  ./.venv/bin/python scripts/smoke.py
"""

from policyengine_us import CountryTaxBenefitSystem, Simulation

TAX_YEAR = 2025
MONTH = "2025-03"

SITUATION = {
    "people": {
        "parent": {
            "age": {str(TAX_YEAR): 35},
            "employment_income": {str(TAX_YEAR): 24_000},
            "immigration_status": {str(TAX_YEAR): "CITIZEN"},
        },
        "child": {
            "age": {str(TAX_YEAR): 5},
            "immigration_status": {str(TAX_YEAR): "CITIZEN"},
        },
    },
    "tax_units": {"tu": {"members": ["parent", "child"]}},
    "families": {"fam": {"members": ["parent", "child"]}},
    "spm_units": {"spm": {"members": ["parent", "child"], "housing_cost": {str(TAX_YEAR): 18_000}}},
    "households": {"hh": {"members": ["parent", "child"], "state_name": {str(TAX_YEAR): "CA"}}},
    "marital_units": {"mu": {"members": ["parent"]}},
}

# (variable, period to query). SNAP is queried monthly and never annually:
# is_snap_eligible is quantity_type=stock, so an annual query silently returns
# December's value alone. See docs/LIMITS.md.
QUERIES = [
    ("is_snap_eligible", MONTH),
    ("snap", MONTH),
    ("is_medicaid_eligible", TAX_YEAR),
    ("eitc", TAX_YEAR),
    ("ctc", TAX_YEAR),
]


def main() -> None:
    vs = CountryTaxBenefitSystem().variables
    sim = Simulation(situation=SITUATION)

    print(f"California household, tax year {TAX_YEAR}, month {MONTH}")
    print(f"  parent age 35, employment_income $24,000/yr; child age 5; housing_cost $18,000/yr")
    print()
    print(f"{'variable':<24} {'entity':<10} {'period':<7} {'qty':<6} {'queried':<9} value")
    print("-" * 78)
    for name, period in QUERIES:
        v = vs[name]
        val = sim.calculate(name, period)
        qt = getattr(v.quantity_type, "name", str(v.quantity_type)).lower()
        shown = ", ".join(f"{x:.2f}" if v.value_type is float else str(bool(x)) for x in val)
        print(
            f"{name:<24} {v.entity.key:<10} {v.definition_period:<7} {qt:<6} {str(period):<9} [{shown}]"
        )
    print("-" * 78)
    print("Every value above traces to the named PolicyEngine variable at the stated period.")


if __name__ == "__main__":
    main()
