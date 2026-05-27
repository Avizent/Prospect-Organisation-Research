"""Read non-secret application configuration from ``config.yaml``.

The file at the repository root holds:

  * ``budgets``    — per-job, daily, and monthly caps (USD)
  * ``models``     — model id used by each agent group
  * ``keychain``   — service and account names (already enforced by
                     :mod:`backend.credentials.keychain`)
  * ``concurrency`` — ``max_concurrent_jobs``

Secrets never appear here; the keychain section names *accounts*, not
keys. The loader is therefore safe to import and call from anywhere.

Two design choices worth knowing
--------------------------------
1. **No env-var overrides.** This is a single-user desktop tool. Adding
   env-var overrides expands the attack surface (a stray ``BUDGETS_*``
   in a parent shell silently raising caps would be a runaway risk).
2. **Defaults baked in.** If the file is missing or a key is absent,
   the loader falls back to the values in this module and prints a
   one-line warning to stderr so the operator notices. This keeps
   tests from needing a fixture ``config.yaml`` everywhere.

Implemented in step 5.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Defaults — these MUST match config.yaml. Treated as a safety net only.
# ---------------------------------------------------------------------------

_DEFAULT_BUDGETS: dict[str, float] = {
    "per_job_usd": 2.00,
    "per_job_max_via_ui_usd": 10.00,
    "daily_soft_usd": 10.00,
    "daily_hard_usd": 25.00,
    "monthly_hard_usd": 200.00,
}

_DEFAULT_CONCURRENCY_MAX_JOBS: int = 3


# ---------------------------------------------------------------------------
# Dataclasses returned to callers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Budgets:
    """Resolved budget caps, all in USD."""

    per_job_usd: float
    per_job_max_via_ui_usd: float
    daily_soft_usd: float
    daily_hard_usd: float
    monthly_hard_usd: float


@dataclass(frozen=True)
class Concurrency:
    max_concurrent_jobs: int


# ---------------------------------------------------------------------------
# File location
# ---------------------------------------------------------------------------

def _default_config_path() -> Path:
    """Return the canonical location of ``config.yaml``.

    The file lives at the repository root: two parents up from this
    module (``backend/cost_control/config_loader.py`` → repo root).
    """
    return Path(__file__).resolve().parents[2] / "config.yaml"


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    """Read and parse the YAML file, or return ``{}`` if unreadable.

    Any parse error is downgraded to a stderr warning and an empty
    dict: a malformed config must not crash the cost-control layer
    on import, because the cost-control layer is what protects the
    user from runaway spend in the first place. The defaults take over.
    """
    target = path or _default_config_path()
    try:
        with target.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                print(
                    f"warning: {target} is not a mapping; "
                    "falling back to baked-in defaults",
                    file=sys.stderr,
                )
                return {}
            return data
    except FileNotFoundError:
        print(
            f"warning: {target} not found; "
            "falling back to baked-in defaults",
            file=sys.stderr,
        )
        return {}
    except yaml.YAMLError as exc:
        print(
            f"warning: {target} could not be parsed ({exc}); "
            "falling back to baked-in defaults",
            file=sys.stderr,
        )
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _cached_raw() -> dict[str, Any]:
    return _load_raw()


def reset_cache() -> None:
    """Drop the cached config (used by tests that point at a fixture file)."""
    _cached_raw.cache_clear()


def budgets(path: Path | None = None) -> Budgets:
    """Return the resolved :class:`Budgets`.

    Missing keys fall back to ``_DEFAULT_BUDGETS``. The result is a
    frozen dataclass so callers cannot mutate the global config by
    accident.
    """
    raw = _load_raw(path) if path is not None else _cached_raw()
    section = raw.get("budgets") or {}

    def _read(key: str) -> float:
        value = section.get(key, _DEFAULT_BUDGETS[key])
        try:
            return float(value)
        except (TypeError, ValueError):
            print(
                f"warning: config budgets.{key}={value!r} is not numeric; "
                f"using default {_DEFAULT_BUDGETS[key]}",
                file=sys.stderr,
            )
            return float(_DEFAULT_BUDGETS[key])

    return Budgets(
        per_job_usd=_read("per_job_usd"),
        per_job_max_via_ui_usd=_read("per_job_max_via_ui_usd"),
        daily_soft_usd=_read("daily_soft_usd"),
        daily_hard_usd=_read("daily_hard_usd"),
        monthly_hard_usd=_read("monthly_hard_usd"),
    )


def concurrency(path: Path | None = None) -> Concurrency:
    """Return the resolved :class:`Concurrency` settings."""
    raw = _load_raw(path) if path is not None else _cached_raw()
    section = raw.get("concurrency") or {}
    raw_value = section.get(
        "max_concurrent_jobs", _DEFAULT_CONCURRENCY_MAX_JOBS
    )
    try:
        max_jobs = int(raw_value)
    except (TypeError, ValueError):
        print(
            f"warning: concurrency.max_concurrent_jobs={raw_value!r} "
            f"is not an int; using default {_DEFAULT_CONCURRENCY_MAX_JOBS}",
            file=sys.stderr,
        )
        max_jobs = _DEFAULT_CONCURRENCY_MAX_JOBS
    if max_jobs < 1:
        # A 0 or negative value would deadlock the semaphore. Treat as
        # a misconfiguration and fall back to the default.
        print(
            f"warning: concurrency.max_concurrent_jobs={max_jobs} "
            f"is < 1; using default {_DEFAULT_CONCURRENCY_MAX_JOBS}",
            file=sys.stderr,
        )
        max_jobs = _DEFAULT_CONCURRENCY_MAX_JOBS
    return Concurrency(max_concurrent_jobs=max_jobs)
