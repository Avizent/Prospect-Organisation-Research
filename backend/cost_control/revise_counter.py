"""Critic-revise loop bound — primitive for Trap 6.

Handover §8 trap 6: maximum *one* revision pass. Flow:

    Writer v1 → Critic → if issues → Writer v2 → Critic → ship v2

A second revision attempt must raise :class:`CriticReviseLimitExceeded`
so the orchestrator (Step 10) can ship v2 with a warning flag rather
than spiral into v3.

This module exposes a tiny counter; the orchestrator wires it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict

from backend.cost_control.exceptions import CriticReviseLimitExceeded


# Default: a writer may be revised at most once.
MAX_REVISIONS_PER_AGENT: int = 1


class CriticReviseLimiter:
    """Count revisions per ``(job_id, agent)`` and trap a 2nd attempt."""

    def __init__(self, max_revisions: int = MAX_REVISIONS_PER_AGENT) -> None:
        if max_revisions < 0:
            raise ValueError("max_revisions must be >= 0")
        self._max = max_revisions
        self._counts: DefaultDict[tuple[str, str], int] = defaultdict(int)

    def attempt(self, *, job_id: str, agent: str) -> None:
        """Record an intent to start a revision pass.

        Call this *before* the writer re-runs. Raises
        :class:`CriticReviseLimitExceeded` if the limit is already
        reached; the orchestrator must catch and proceed with the
        existing draft, flagging the job.
        """
        key = (job_id, agent)
        if self._counts[key] >= self._max:
            raise CriticReviseLimitExceeded(
                f"agent {agent!r} already revised {self._counts[key]} "
                f"time(s); cap is {self._max}",
                job_id=job_id,
                agent=agent,
                context={
                    "agent": agent,
                    "revisions_taken": self._counts[key],
                    "max_revisions": self._max,
                },
            )
        self._counts[key] += 1

    def revisions_taken(self, *, job_id: str, agent: str) -> int:
        return self._counts[(job_id, agent)]
