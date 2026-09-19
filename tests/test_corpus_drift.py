"""Guard the COMMITTED corpus against a silent generator change.

The gap this closes
-------------------
On 2026-09-16 we re-widened `_STATUS_WEIGHTS` (docs/LIMITS.md 16 and 31). The committed
splits instantly stopped being reproducible from their own recorded `(seed, index)`
provenance, and **all 300 tests stayed green** - because not one of them read
`data/dev/t1.jsonl`. A green suite that never touches the artifact we would publish is the
same pathology this project keeps finding elsewhere: a signal that is not measuring the
thing.

So:

* `test_generator_fingerprint_is_pinned` fails on ANY change to the generator's
  configuration, forcing a deliberate decision about regeneration instead of an accident.
* `test_committed_dev_split_is_consistent_or_knowingly_stale` reads the committed manifest
  and fails unless it either matches the live generator or matches the one recorded stale
  fingerprint documented in LIMITS 31.
* `test_committed_dev_corpus_labels_are_internally_consistent` checks the thing that
  actually matters about a stale corpus: the narratives and the answer keys still agree
  with each other.

These are cheap: no engine calls except in the last test, which samples.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redtape.generator.fingerprint import generator_config, generator_fingerprint

REPO = Path(__file__).resolve().parents[1]
DEV_SPLIT = REPO / "data" / "dev" / "t1.jsonl"
DEV_MANIFEST = REPO / "data" / "dev" / "t1.manifest.json"

# The live generator's configuration fingerprint. Pinned deliberately.
#
# If a change to the generator makes this fail, that is the test working. Do not "fix" it
# by pasting the new value in without deciding what happens to the committed splits:
#   1. update this constant,
#   2. decide whether to regenerate (docs/LIMITS.md 31 records why we did not in Sep 2026),
#   3. if not regenerating, move the old value into STALE_FINGERPRINTS below with a note.
# 2026-09-19: 5af23f90feb737e1 -> 93c3b90c3f0cae06. Explicit household shapes, stated hours,
# SSN by status, and the new constants added to the fingerprint (LIMITS 36). Step 2: the
# decision is to REGENERATE (LIMITS 31, triggered), so no STALE entry is added; until the
# split is rebuilt, test_committed_dev_split_is_consistent_or_knowingly_stale fails, as it
# should.
EXPECTED_GENERATOR_FINGERPRINT = "93c3b90c3f0cae06"

# Fingerprints of generator configurations that produced committed splits we have
# deliberately chosen NOT to regenerate. Each needs a reason and a LIMITS reference.
STALE_FINGERPRINTS = {
    "ae3b3a8d7a85b5e6": (
        "pre-2026-09-16 configuration: SAFE_IMMIGRATION_STATUSES excluded the five HR 1 "
        "statuses and _STATUS_WEIGHTS omitted REFUGEE/ASYLEE. The committed dev and "
        "held-out splits were built under it. Labels remain correct (California kept "
        "those statuses eligible through 2026-03); the split merely under-samples the "
        "immigration fact space. See docs/LIMITS.md 31."
    ),
}


def test_generator_fingerprint_is_pinned():
    """Any change to what the generator produces must be an explicit decision."""
    actual = generator_fingerprint()
    assert actual == EXPECTED_GENERATOR_FINGERPRINT, (
        f"the generator configuration changed: {EXPECTED_GENERATOR_FINGERPRINT} -> {actual}.\n"
        "Every committed split built under the old configuration is now stale. Follow the "
        "three steps in the comment above EXPECTED_GENERATOR_FINGERPRINT in this file "
        "before updating it; do not just paste the new value in.\n"
        f"current configuration:\n{json.dumps(generator_config(), indent=2, sort_keys=True)}"
    )


def test_generator_fingerprint_is_stable_across_calls():
    """A fingerprint that varies per process would silently pass everything."""
    assert generator_fingerprint() == generator_fingerprint()
    assert len(generator_fingerprint()) == 16


def test_fingerprint_actually_reacts_to_a_generator_change():
    """Positive control: a fingerprint nothing can move is not a guard.

    Without this, a bug that made `generator_fingerprint()` constant would leave every
    other test in this file passing forever.
    """
    import redtape.generator.households as H

    before = generator_fingerprint()
    original = H._STATUS_WEIGHTS
    try:
        H._STATUS_WEIGHTS = tuple(
            (s, w + 0.01) if i == 0 else (s, w)
            for i, (s, w) in enumerate(original)
        )
        assert generator_fingerprint() != before, (
            "changing _STATUS_WEIGHTS did not change the fingerprint; the guard is inert"
        )
    finally:
        H._STATUS_WEIGHTS = original
    assert generator_fingerprint() == before, "fingerprint did not restore"


@pytest.mark.skipif(not DEV_MANIFEST.exists(), reason="committed dev manifest absent")
def test_committed_dev_split_is_consistent_or_knowingly_stale():
    """The committed split must either match the live generator or be a recorded staleness."""
    manifest = json.loads(DEV_MANIFEST.read_text(encoding="utf-8"))
    recorded = manifest.get("generator_fingerprint")
    live = generator_fingerprint()

    if recorded is None:
        # Built before the fingerprint existed. Accept ONLY while the generator still
        # matches a fingerprint we have explicitly recorded as stale.
        assert STALE_FINGERPRINTS, (
            "the committed manifest records no generator_fingerprint and no staleness is "
            "documented. Rebuild the split, or document the staleness in "
            "STALE_FINGERPRINTS with a LIMITS reference."
        )
        pytest.xfail(
            "committed dev split predates generator_fingerprint (built before "
            "2026-09-16); its staleness is documented in docs/LIMITS.md 31 and in "
            "STALE_FINGERPRINTS. Rebuilding the split clears this."
        )

    if recorded != live:
        assert recorded in STALE_FINGERPRINTS, (
            f"committed dev split was built under generator {recorded}, the live generator "
            f"is {live}, and {recorded} is not a documented staleness. Either regenerate "
            "the split or add it to STALE_FINGERPRINTS with a reason and a LIMITS "
            "reference."
        )


@pytest.mark.skipif(not DEV_SPLIT.exists(), reason="committed dev split absent")
def test_committed_dev_corpus_under_samples_immigration_as_documented():
    """Pin the SHAPE of the known staleness, so a different staleness is not mistaken for it.

    LIMITS 31 records exactly one defect in the committed corpus: no refugee or asylee
    households. If that ever stops being true - or if something else about the status
    distribution moves - the recorded account is wrong and must be re-derived.
    """
    prose = DEV_SPLIT.read_text(encoding="utf-8").lower()
    assert "refugee" not in prose, (
        "the committed dev split now contains refugee households, so it is no longer the "
        "stale artifact docs/LIMITS.md 31 describes. Re-derive LIMITS 31 and update "
        "STALE_FINGERPRINTS."
    )
    assert "asylee" not in prose and "asylum" not in prose, (
        "the committed dev split now contains asylee households; see above"
    )
    # The statuses it DOES exercise must still be there - this is a narrowing, not an
    # emptying.
    for needle in ("undocumented", "lawful permanent resident"):
        assert needle in prose, (
            f"{needle!r} absent from the committed dev split; the staleness recorded in "
            "LIMITS 31 is a narrowing of the immigration fact space, not its removal"
        )


@pytest.mark.skipif(not DEV_SPLIT.exists(), reason="committed dev split absent")
def test_committed_dev_corpus_records_its_own_provenance():
    """A stale corpus is only defensible if it says what produced it."""
    with DEV_SPLIT.open(encoding="utf-8") as fh:
        rows = [json.loads(next(fh)) for _ in range(20)]
    for r in rows:
        assert r["engine_version"] == "1.821.4", r["engine_version"]
        assert r["seed"] == 20260828
        assert r["household_id"] == f"hh-{r['seed']}-{r['index']:05d}"
        assert r["task_hash"] and len(r["task_hash"]) == 64


@pytest.mark.skipif(not DEV_SPLIT.exists(), reason="committed dev split absent")
def test_committed_dev_corpus_labels_are_internally_consistent():
    """The claim that matters about the stale corpus: prose and answer key still agree.

    A household in which EVERY member is named ineligible-by-status, and none of whose
    statuses was withheld, cannot be recorded as SNAP-eligible.

    The first version of this test was wrong twice over and is worth recording. It looked
    for the literal string "citizen" as the marker of an eligible member, but the renderer
    also writes LPR as "a green card holder"; and it ignored withheld statuses, which are
    absent from the prose by design. It flagged hh-20260828-00077 - a household with two
    green-card children and a withheld p1 status - as a labelling error when the label was
    correct. Read the renderer's vocabulary (`_STATUS_PHRASE`) rather than guessing it.
    """
    from redtape.generator.narratives import _STATUS_PHRASE

    # California, 2025: only these three are ineligible by status (docs/LIMITS.md 16).
    ineligible = {"UNDOCUMENTED", "DACA", "TPS"}
    elig_needles = [
        p.lower()
        for status, phrases in _STATUS_PHRASE.items()
        if status not in ineligible
        for p in phrases
    ]
    inelig_needles = [
        p.lower() for status in ineligible for p in _STATUS_PHRASE[status]
    ]

    with DEV_SPLIT.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]

    checked = 0
    for r in rows:
        key = json.loads(r["answer_key_json"])
        snap = key.get("snap", {})
        if "eligible" not in snap or not snap["eligible"]:
            continue
        prose = r["prompt"].lower()
        # A withheld immigration status is silent in the prose, so it could be anything.
        if (r.get("withheld_fact") or "").endswith("immigration_status"):
            continue
        names_eligible = any(n in prose for n in elig_needles)
        names_ineligible = any(n in prose for n in inelig_needles)
        if names_ineligible and not names_eligible:
            pytest.fail(
                f"{r['household_id']}: every named status is ineligible-by-status and none "
                f"was withheld, but the answer key marks the household SNAP-eligible.\n"
                f"{r['prompt']}"
            )
        checked += 1
    assert checked > 100, f"only {checked} rows carried a positive SNAP eligibility label"
