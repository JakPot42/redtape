# Reply to Max Ghenis on #9374 — POSTED 2026-09-16

Posted as https://github.com/PolicyEngine/policyengine-us/issues/9374#issuecomment-5705863923
(author `JakPot42`, 2026-09-16T23:13:20Z). The text below is what was posted, verbatim.

**Open item: Max's answer on the SUAS date.** The PR cannot encode the California date until
he picks option 1 or 2 — see `docs/HR1_SUA_PR_SCOPE.md` §3a.

---

Thanks — the immigrant-eligibility half was our error and I've retracted it.

I checked your account against 1.821.4 rather than taking it on trust. Both layers are
there: the federal `2025-07-01` entry, and the `2026-04-01` entry in
`gov/states/ca/cdss/snap/eligibility/eligible_immigration_statuses.yaml` citing ACL 25-92,
applied via `ca_snap_immigration_status_eligible` and OR'd in by
`is_snap_immigration_status_eligible`. Measured directly: a California refugee is eligible
at 2025-07 and 2026-03 and ineligible at 2026-04; a Texas refugee is eligible at 2025-06
and ineligible at 2025-07.

The cause was ours and specific: our probe hardcoded `state_name: CA` and swept only months
of 2025 — the one cell where a correctly modelled state delay and a federal omission look
identical. We've corrected the documentation and removed the corpus restriction.

I'll open the SUA PR, gated like `is_snap_abawd_hr1_in_effect`.

On the leg you didn't address: our original report called "not already receiving the maximum
allotment" the hard part of the SUAS test and declined to propose a resolution, since the
allotment depends on the deduction which depends on the SUA. I think it does resolve, and I
checked rather than argued it.

`snap_net_income` is floored at zero, so `snap_expected_contribution >= 0` and "at the
maximum allotment" is exactly `snap_net_income == 0` — 0 mismatches between that and
`snap >= snap_max_allotment` across 288 configurations (144 cells of income × housing ×
household size × elderly, each with the SUA on and off). And the utility allowance reaches
the benefit only through `max_(expense_share * housing + UA - subtracted, 0)` and then
`min_(uncapped, cap)`, both monotone, so adding the SUA can only move a household *toward*
maximum allotment, never away: 0 monotonicity violations and 0 at-max violations over the
same cells.

So evaluating that leg against the allotment computed *without* the SUAS allowance is
non-circular and, I'd argue, the correct reading rather than just the convenient one — the
test asks whether the household is already at max before the payment is granted, and a
household at max without it is still at max with it. I'll implement it that way with the
monotonicity argument in a code comment and a regression test on it, but flag it for review:
it holds in 1.821.4 as a property of the current deduction formula, not as a guarantee.

One question before I encode a date. You're right that ACL 25-68 keys the SUAS limitation
to completion of automation, at initial certification and next recertification, with a
120-day hold-harmless from 2025-07-04, and never names a date. We took 2025-10-31 from ACIN
I-46-25. Which would you prefer:

1. `2025-10-31`, with ACIN I-46-25 cited as the source for the date; or
2. the parameter structured around the hold-harmless instead, so the
   automation-completion dependency stays visible rather than being resolved into one day?

I lean to (2) as more faithful to the guidance, but (1) matches the shape of
`hr1_in_effect` for ABAWD, so I'd rather follow your convention.
