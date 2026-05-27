"""JobBudgetGuard — enforces Trap 3 (per-job budget cap).

One instance lives for the duration of a single job. Every Claude
call that runs inside that job goes through:

  1. :meth:`check_estimate` — pre-call, raises :class:`JobBudgetExceeded`
     if the *upper-bound* cost estimate would push spend above the cap.
  2. :meth:`record` — post-call, persists the realised cost on
     ``jobs.cost_usd`` and updates the in-memory ``spent_usd`` running
     total.

Atomicity on the DB row
-----------------------
``record`` uses ``UPDATE jobs SET cost_usd = cost_usd + :delta WHERE
id = :id`` so a concurrent write to the same row (there shouldn't be
one — one orchestrator per job — but defence in depth) cannot double-
count or lose a partial.

The in-memory ``spent_usd`` is what the *next* :meth:`check_estimate`
uses, so a freshly-constructed guard reads the persisted total once
on construction and keeps it in lock-step from then on. If you create
two guards for the same job, you have bigger problems than this class.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from backend.cost_control.exceptions import JobBudgetExceeded


class JobBudgetGuard:
    """Per-job budget enforcement (Trap 3)."""

    def __init__(
        self,
        *,
        job_id: str,
        cap_usd: float,
        db: Session,
    ) -> None:
        if cap_usd < 0:
            raise ValueError("cap_usd must be >= 0")
        self._job_id = job_id
        self._cap_usd = float(cap_usd)
        self._db = db
        self._spent_usd: float = self._read_persisted_spend()

    # ------------------------------------------------------------------
    # Properties used by audit logging
    # ------------------------------------------------------------------

    @property
    def cap_usd(self) -> float:
        return self._cap_usd

    @property
    def spent_usd(self) -> float:
        return self._spent_usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self._cap_usd - self._spent_usd)

    # ------------------------------------------------------------------
    # Trap 3 pre-call check
    # ------------------------------------------------------------------

    def check_estimate(self, estimate_usd: float) -> None:
        """Raise :class:`JobBudgetExceeded` if *estimate* breaches the cap."""
        if estimate_usd < 0:
            raise ValueError("estimate_usd must be >= 0")
        if self._spent_usd + estimate_usd > self._cap_usd:
            raise JobBudgetExceeded(
                f"estimate {estimate_usd:.4f} + spent "
                f"{self._spent_usd:.4f} > cap {self._cap_usd:.4f}",
                job_id=self._job_id,
                context={
                    "cap_usd": self._cap_usd,
                    "spent_usd": self._spent_usd,
                    "estimate_usd": estimate_usd,
                },
            )

    # ------------------------------------------------------------------
    # Post-call spend record
    # ------------------------------------------------------------------

    def record(self, actual_usd: float) -> None:
        """Add *actual_usd* to the in-memory and persisted job spend."""
        if actual_usd < 0:
            raise ValueError("actual_usd must be >= 0")
        # Atomic UPDATE rather than ORM-load-then-mutate so we don't
        # depend on whether the row is already in the session identity
        # map. Works against the same row regardless of concurrency.
        self._db.execute(
            sa.text(
                "UPDATE jobs SET cost_usd = COALESCE(cost_usd, 0) + :delta "
                "WHERE id = :id"
            ),
            {"delta": float(actual_usd), "id": self._job_id},
        )
        self._db.commit()
        self._spent_usd += float(actual_usd)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_persisted_spend(self) -> float:
        row = self._db.execute(
            sa.text("SELECT COALESCE(cost_usd, 0) FROM jobs WHERE id = :id"),
            {"id": self._job_id},
        ).scalar()
        if row is None:
            # No such job. Treat as zero spent; the orchestrator that
            # constructed this guard is responsible for the job row's
            # existence and will trip on the next FK touch.
            return 0.0
        return float(row)
