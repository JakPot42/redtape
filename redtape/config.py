"""Seed resolution and `.env` loading. The held-out path fails closed.

**Why this module exists.** `scripts/build_split.py` used to resolve its seed as

    seed = int(os.environ.get("REDTAPE_SEED")) if set else 20260828

which meant a held-out build with no `.env` did not fail. It silently produced a split
with *public* provenance and called it held-out. That is the third instance of this
project's own pathology appearing in our own tooling, after `medicaid` (a dollar amount
where a boolean was meant) and `ctc` (gross entitlement where received value was meant):
a plausible default standing exactly where a real value belongs, with nothing raised.

The defence has three parts, and each one is load-bearing:

1. **Two variable names, never one.** The dev seed lives in `REDTAPE_SEED` and the
   held-out seed in `REDTAPE_HELDOUT_SEED`. A single variable plus a "is this held-out?"
   flag would still let one value serve both roles, so a stale export or a copied shell
   could put the public seed behind a held-out build. Separate names make that
   impossible rather than unlikely.

2. **No default on the held-out path, and no override either.** `resolve_seed("heldout")`
   raises `MissingHeldoutSeed` when the variable is absent. It also refuses an explicit
   `--seed`, because a seed typed on a command line is a seed that can be typed wrong,
   copied from a notebook, or lifted from shell history into a published manifest. One
   source of truth.

3. **`.env` loads automatically.** A file that has to be exported by hand is a file
   somebody forgets to export, and the whole point is that forgetting must not be
   survivable. Loading happens without overriding anything already in the environment,
   so a deliberate export in the shell still wins.

The dev seed keeps its default. `20260828` is public by design - it is the seed behind the
committed dev split, and there is nothing to protect.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# The dev seed is PUBLIC by design. Never reuse it for a held-out build.
DEV_SEED = 20260828

DEV_SEED_VAR = "REDTAPE_SEED"
HELDOUT_SEED_VAR = "REDTAPE_HELDOUT_SEED"

DEV, HELDOUT = "dev", "heldout"
SPLIT_KINDS = (DEV, HELDOUT)


class MissingHeldoutSeed(RuntimeError):
    """No held-out seed in the environment.

    A `RuntimeError` rather than a `ValueError`: nothing was passed in wrongly. The
    environment is not in a state where a held-out build is a legitimate thing to attempt,
    and the correct response is to stop, not to substitute anything.
    """


class SeedMisuse(RuntimeError):
    """A held-out build was asked to take its seed from somewhere it must not."""


def repo_root() -> Path:
    """The repo root, located from this file rather than from the cwd.

    `.env` must load the same way whether the caller ran from the repo root, from
    `scripts/`, or from a worker process with an inherited cwd.
    """
    return Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load `KEY=VALUE` lines from `.env` into `os.environ`. Returns the names set.

    Deliberately minimal, and deliberately not `python-dotenv`: CLAUDE.md pins every
    dependency to an exact version because `policyengine-us` ships three releases a day,
    and adding a package to read six lines of `KEY=VALUE` buys nothing worth a new pin.

    **Never overrides by default.** An explicit `export` in the shell is a deliberate act
    and outranks the file. The auto-load exists to stop a *forgotten* export from falling
    back silently, not to overrule a deliberate one.

    A missing `.env` is not an error here. It becomes one at `resolve_seed`, which is
    where the consequence actually lands.
    """
    path = path or repo_root() / ".env"
    if not path.is_file():
        return []

    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def resolve_seed(split_kind: str, *, override: int | None = None,
                 autoload: bool = True) -> int:
    """The only supported way to obtain a seed for a split build.

    `dev` falls back to the public `DEV_SEED`. `heldout` never falls back and never takes
    an override - it reads `REDTAPE_HELDOUT_SEED` or raises.
    """
    if split_kind not in SPLIT_KINDS:
        raise ValueError(f"split_kind must be one of {SPLIT_KINDS}, got {split_kind!r}")

    if autoload:
        load_dotenv()

    if split_kind == DEV:
        if override is not None:
            return override
        env = os.environ.get(DEV_SEED_VAR)
        return int(env) if env else DEV_SEED

    # ---- held-out: fail closed -------------------------------------------------------
    if override is not None:
        raise SeedMisuse(
            "a held-out build will not accept an explicit seed. Its seed comes from "
            f"{HELDOUT_SEED_VAR} in .env and nowhere else, so that no seed a held-out "
            "split was built from can reach a shell history, a manifest, or a notebook."
        )

    env = os.environ.get(HELDOUT_SEED_VAR)
    if not env:
        raise MissingHeldoutSeed(
            f"{HELDOUT_SEED_VAR} is not set, so there is no held-out seed to build from.\n"
            f"\n"
            f"This build has been STOPPED rather than defaulted. A held-out split built "
            f"from the public seed ({DEV_SEED}) would be reproducible by anyone, which is "
            f"the single property a held-out split exists to have - and it would look "
            f"entirely normal while being worthless.\n"
            f"\n"
            f"Set {HELDOUT_SEED_VAR} in .env at the repo root. See CLAUDE.md, "
            f'"Held-out seed".'
        )

    try:
        return int(env)
    except ValueError as exc:
        raise MissingHeldoutSeed(
            f"{HELDOUT_SEED_VAR} is set but is not an integer. Refusing to guess."
        ) from exc


def seed_fingerprint(seed: int) -> str:
    """A stable public identifier for a seed, safe to print, log and publish.

    `sha256(str(seed))[:16]`. Recovering the seed means guessing it and hashing, and an
    18-digit seed is a ~2^60 space, so the fingerprint identifies a run without disclosing
    what produced it. Quote this in manifests, terminal output and issue threads; quote
    the seed nowhere.

    Sixteen hex characters is 64 bits, far past collision trouble for the handful of seeds
    this project will ever hold, and short enough to read aloud.
    """
    return hashlib.sha256(str(seed).encode()).hexdigest()[:16]
