"""A fingerprint over everything that determines corpus CONTENT.

Why this exists
---------------
`data/dev/t1.jsonl` records `policyengine_us`, `python`, `seed` and `(seed, index)` per
household, and CLAUDE.md claims every household is reproducible from `(seed, index)` alone.
None of that catches a change to the GENERATOR ITSELF. On 2026-09-16 we re-widened
`_STATUS_WEIGHTS` (docs/LIMITS.md 16) and the committed splits silently stopped being
reproducible from their own recorded provenance - while all 300 tests stayed green, because
not one of them reads the committed corpus.

So this fingerprint covers the generator's configuration, `build_split.py` writes it into
every manifest, and `tests/test_corpus_drift.py` fails the build when the current generator
no longer matches the fingerprint the committed split was built under. The point is that a
silent divergence becomes a failing build; whether to regenerate is then a decision, not an
accident.

What it deliberately does NOT cover: the engine version and the Python version, which the
manifest already records separately, and the narrative renderer, which changes prose without
changing the household. Those are different questions with their own guards.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Fingerprint format version. Bump ONLY when the set of inputs below changes shape;
# bumping invalidates every recorded fingerprint by design, so it is a deliberate act.
# 2 (2026-09-19): generator LOGIC changed - explicit household shapes, children bounded
# by the youngest parent, hours derived from earnings (LIMITS 36). A logic change that
# moves no constant is invisible to the hash below, so the version is bumped by hand.
FINGERPRINT_VERSION = 2


def _generator_config() -> dict[str, Any]:
    """Every constant that can change which households come out of a given seed."""
    # Imported here rather than at module scope so that importing this module cannot
    # create a cycle with the generator.
    from redtape.generator import households as H
    from redtape.schemas import (
        CORPUS_STATE,
        CORPUS_TAX_YEAR,
        HOUSEHOLD_TYPES,
        SAFE_IMMIGRATION_STATUSES,
        SSN_BY_STATUS,
    )

    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "adult_ages": list(H._ADULT_AGES),
        "child_ages": list(H._CHILD_AGES),
        "n_children_weights": [list(x) for x in H._N_CHILDREN_WEIGHTS],
        "income_buckets": [[list(b), w] for b, w in H._INCOME_BUCKETS],
        "housing_buckets": [[list(b), w] for b, w in H._HOUSING_BUCKETS],
        "care_buckets": [[list(b), w] for b, w in H._CARE_BUCKETS],
        # Statuses by VALUE, so an unrelated reordering of the enum does not read as drift,
        # but a changed weight or a added/removed status does.
        "status_weights": [[s.value, w] for s, w in H._STATUS_WEIGHTS],
        "safe_immigration_statuses": sorted(SAFE_IMMIGRATION_STATUSES),
        "tax_year": H.TAX_YEAR,
        "corpus_state": CORPUS_STATE,
        "corpus_tax_year": CORPUS_TAX_YEAR,
        # Added 2026-09-19 with the explicit-structure generator (LIMITS 36).
        "household_types": list(HOUSEHOLD_TYPES),
        "p_married": H._P_MARRIED,
        "spouse_age_gap": H._SPOUSE_AGE_GAP,
        "min_parent_gap": H._MIN_PARENT_GAP,
        "p_student": H._P_STUDENT,
        "p_full_time": H._P_FULL_TIME,
        "ca_min_wage_2025": H.CA_MIN_WAGE_2025,
        "max_wage": H._MAX_WAGE,
        "max_hours": H._MAX_HOURS,
        "ssn_by_status": dict(sorted(SSN_BY_STATUS.items())),
    }


def generator_config() -> dict[str, Any]:
    """The configuration, for reporting. `generator_fingerprint()` is the comparable form."""
    return _generator_config()


def generator_fingerprint() -> str:
    """sha256 over the generator configuration, truncated to 16 hex chars.

    Contains no seed material - it is a function of committed source constants only, so it
    is safe to print and safe to commit (contrast `seed_fingerprint()`).
    """
    blob = json.dumps(_generator_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
