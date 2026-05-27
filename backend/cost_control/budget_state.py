"""Daily and monthly spend state — enforces Trap 4.

State lives in a single JSON file at ``{ANS_HOME}/budget_state.json``::

    {
      "version": 1,
      "today": {"date": "YYYY-MM-DD", "spend_usd": 0.0},
      "month": {"yyyymm": "YYYY-MM",  "spend_usd": 0.0}
    }

Reads and writes are guarded by ``fcntl.flock(LOCK_EX)`` on a sibling
lock file. Writes are atomic: the new payload is written to a
temporary file in the same directory and ``os.replace``\\ d into place
so a power loss never leaves the file half-written.

Cap semantics (handover §8, trap 4)
-----------------------------------
* ``daily_soft_usd``  $10 — emit a warning, allow the call.
* ``daily_hard_usd``  $25 — block, unless caller passed ``force=True``
                            (only the ``ans-tool`` CLI does so).
* ``monthly_hard_usd`` $200 — block, no in-process override; the user
                              must edit ``config.yaml``.

Reset semantics
---------------
On every ``check_and_reserve`` / ``commit`` call, if the stored ``date``
is not today (UTC), the daily total is reset to 0. Same logic for the
``yyyymm`` key on the month. Resets happen on read, so there is no
background job and no migration needed.

macOS-only assumption
---------------------
``fcntl.flock`` is POSIX-only. This tool is macOS-only by design
(handover §1, target hardware MacBook Pro M4). The Windows runtime
path does not exist.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.cost_control import audit as audit_mod
from backend.cost_control.config_loader import Budgets
from backend.cost_control.exceptions import (
    DailyBudgetExceeded,
    MonthlyBudgetExceeded,
)


# ---------------------------------------------------------------------------
# File location helpers (use audit_mod.ANS_HOME so tests' monkey-patches
# of ANS_HOME flow through both modules without having to patch twice).
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    return audit_mod.ANS_HOME / "budget_state.json"


def _lock_path() -> Path:
    return audit_mod.ANS_HOME / "budget_state.lock"


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "today": {"date": _today_str(), "spend_usd": 0.0},
        "month": {"yyyymm": _month_str(), "spend_usd": 0.0},
    }


# ---------------------------------------------------------------------------
# Public decision type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a pre-call budget check.

    ``proceed`` is always ``True`` here; the caller relies on the
    exception path for blocking. ``warn_daily_soft`` flags a soft-cap
    breach so the orchestrator can surface a banner without stopping.
    """

    proceed: bool
    warn_daily_soft: bool
    spent_today_usd: float
    spent_month_usd: float
    remaining_day_usd: float
    remaining_month_usd: float


# ---------------------------------------------------------------------------
# BudgetState
# ---------------------------------------------------------------------------

class BudgetState:
    """Atomic, fcntl-guarded wrapper around ``budget_state.json``.

    Use one instance per call site. The class itself holds no mutable
    state across calls — every public method opens the state file
    under an exclusive flock, reads, mutates a fresh dict, writes
    atomically, and releases the lock.
    """

    def __init__(self, caps: Budgets) -> None:
        self._caps = caps

    # ------------------------------------------------------------------
    # Internal: read+write under flock
    # ------------------------------------------------------------------

    def _ensure_home(self) -> None:
        home = audit_mod.ANS_HOME
        if not home.exists():
            home.mkdir(parents=True, exist_ok=True)
            try:
                home.chmod(0o700)
            except OSError:
                pass

    def _read_locked(self, lock_fd: int) -> dict[str, Any]:
        """Read the state file with the lock already held.

        Resets daily/monthly counters if the stored stamp has rolled
        over. Returns a fresh dict (callers may mutate freely).
        """
        path = _state_path()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not a mapping")
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            data = _empty_state()

        # Roll over if needed.
        today = _today_str()
        month = _month_str()
        if data.get("today", {}).get("date") != today:
            data["today"] = {"date": today, "spend_usd": 0.0}
        if data.get("month", {}).get("yyyymm") != month:
            data["month"] = {"yyyymm": month, "spend_usd": 0.0}
        data.setdefault("version", 1)
        return data

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        """Atomically replace the state file. Caller holds the flock."""
        path = _state_path()
        is_new = not path.exists()
        # Same-directory tempfile so ``os.replace`` is atomic on the
        # same filesystem.
        fd, tmp_name = tempfile.mkstemp(
            prefix="budget_state.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        if is_new:
            try:
                path.chmod(0o600)
            except OSError:
                pass

    class _Locked:
        """Context manager that opens (and creates) the lock file and
        holds an exclusive ``flock`` on it for the duration of the block.

        The file descriptor is closed on exit, which releases the lock.
        """

        def __init__(self, parent: "BudgetState") -> None:
            self._parent = parent
            self._fd: int | None = None

        def __enter__(self) -> int:
            self._parent._ensure_home()
            self._fd = os.open(
                _lock_path(),
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX)
            return self._fd

        def __exit__(self, exc_type, exc, tb) -> None:
            if self._fd is not None:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                finally:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the current state (with rollover applied)."""
        with self._Locked(self) as fd:
            state = self._read_locked(fd)
            # Write back if we rolled over so the file reflects today.
            self._write_atomic(state)
            return state

    def check(self, estimate_usd: float, *, force: bool = False) -> BudgetDecision:
        """Test whether *estimate_usd* would breach a cap, without recording.

        Used by the cost approval gate UI; the live spend reservation
        path uses :meth:`check_and_reserve` instead.
        """
        if estimate_usd < 0:
            raise ValueError("estimate_usd must be >= 0")
        with self._Locked(self) as fd:
            state = self._read_locked(fd)
            self._write_atomic(state)  # persist any rollover
            return self._evaluate(state, estimate_usd, force=force, reserve=False)

    def check_and_reserve(
        self,
        estimate_usd: float,
        *,
        force: bool = False,
    ) -> BudgetDecision:
        """Verify caps for *estimate_usd*. Does **not** mutate the spend.

        Spend is recorded only in :meth:`commit`, after the call returns
        with realised usage. This split keeps a refused/timed-out call
        from polluting the daily total.

        ``force=True`` allows a daily-hard breach to proceed; the
        ``--force`` flag on the CLI is the only authorised caller.
        Monthly-hard is *never* bypassed in process.
        """
        if estimate_usd < 0:
            raise ValueError("estimate_usd must be >= 0")
        with self._Locked(self) as fd:
            state = self._read_locked(fd)
            self._write_atomic(state)
            return self._evaluate(state, estimate_usd, force=force, reserve=False)

    def commit(self, actual_usd: float) -> None:
        """Add *actual_usd* to today's and this month's totals."""
        if actual_usd < 0:
            raise ValueError("actual_usd must be >= 0")
        with self._Locked(self) as fd:
            state = self._read_locked(fd)
            state["today"]["spend_usd"] = (
                float(state["today"]["spend_usd"]) + float(actual_usd)
            )
            state["month"]["spend_usd"] = (
                float(state["month"]["spend_usd"]) + float(actual_usd)
            )
            self._write_atomic(state)

    # ------------------------------------------------------------------
    # Internal cap evaluation
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        state: dict[str, Any],
        estimate_usd: float,
        *,
        force: bool,
        reserve: bool,  # reserved for future use; currently unused
    ) -> BudgetDecision:
        spent_today = float(state["today"]["spend_usd"])
        spent_month = float(state["month"]["spend_usd"])
        projected_today = spent_today + estimate_usd
        projected_month = spent_month + estimate_usd

        caps = self._caps

        # Monthly hard cap — no in-process override.
        if projected_month > caps.monthly_hard_usd:
            raise MonthlyBudgetExceeded(
                f"monthly spend {spent_month:.2f} + estimate "
                f"{estimate_usd:.2f} > cap {caps.monthly_hard_usd:.2f}",
                context={
                    "spent_month_usd": spent_month,
                    "estimate_usd": estimate_usd,
                    "cap_usd": caps.monthly_hard_usd,
                },
            )

        # Daily hard cap — overridable only by --force CLI flag.
        if projected_today > caps.daily_hard_usd and not force:
            raise DailyBudgetExceeded(
                f"daily spend {spent_today:.2f} + estimate "
                f"{estimate_usd:.2f} > cap {caps.daily_hard_usd:.2f}",
                context={
                    "spent_today_usd": spent_today,
                    "estimate_usd": estimate_usd,
                    "cap_usd": caps.daily_hard_usd,
                },
            )

        warn_soft = projected_today > caps.daily_soft_usd

        return BudgetDecision(
            proceed=True,
            warn_daily_soft=warn_soft,
            spent_today_usd=spent_today,
            spent_month_usd=spent_month,
            remaining_day_usd=max(0.0, caps.daily_hard_usd - spent_today),
            remaining_month_usd=max(0.0, caps.monthly_hard_usd - spent_month),
        )


# ---------------------------------------------------------------------------
# Test/debug helper
# ---------------------------------------------------------------------------

def _debug_reset(path: Path | None = None) -> None:
    """Delete the state file, for tests. Not part of the public API."""
    target = path or _state_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"warning: _debug_reset failed: {exc!r}", file=sys.stderr)
