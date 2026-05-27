"""Exceptions raised by the runaway-protection layer.

Every trap that fires raises a subclass of :class:`RunawayTrapFired`,
so a single ``except RunawayTrapFired`` catches all seven of them. The
orchestrator and CLI use that single catch-point to:

  * stop the job immediately (no retry, per handover §8)
  * append a structured row to ``~/.ans-tool/incidents.jsonl``
  * fire a macOS desktop notification

The exception subclasses below are intentionally *thin*: the message
they carry is purely human-readable. Programmatic decisions (which
trap, severity, context payload) are driven by attributes set when
the exception is raised, not by parsing the message.

Reminder for future authors
---------------------------
None of these messages may contain secret material. ``reason`` strings
are forwarded verbatim into the incident log and a macOS notification
title/body — anything you put here ends up on disk and in the
Notification Center. Use numerics, model names, and short labels only.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class RunawayTrapFired(Exception):
    """Common base for every trap exception.

    Carries enough structured context for the audit/incident writer to
    record a useful row without re-parsing the message string.
    """

    #: Short, stable trap identifier (e.g. ``"JobBudgetExceeded"``).
    #: Set by each subclass; the base value is the class name so it is
    #: never accidentally empty.
    trap: str = "RunawayTrapFired"

    #: Severity label for the incident log.
    severity: str = "high"

    def __init__(
        self,
        reason: str,
        *,
        job_id: str | None = None,
        agent: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.job_id = job_id
        self.agent = agent
        self.context: dict[str, Any] = dict(context or {})


# ---------------------------------------------------------------------------
# Trap 1 — per-call max_tokens ceiling
# ---------------------------------------------------------------------------

class MaxTokensExceeded(RunawayTrapFired):
    """Caller requested a ``max_tokens`` above the per-agent ceiling."""

    trap = "MaxTokensExceeded"


class UnknownAgent(RunawayTrapFired):
    """No ``max_tokens`` ceiling registered for the named agent.

    Treated as a trap firing: there must never be a default fallback.
    """

    trap = "UnknownAgent"


# ---------------------------------------------------------------------------
# Trap 2 — tool-call ceiling
# ---------------------------------------------------------------------------

class ToolCallLimitExceeded(RunawayTrapFired):
    """Agent attempted more tool calls than its per-tool ceiling allows."""

    trap = "ToolCallLimitExceeded"


# ---------------------------------------------------------------------------
# Trap 3 — per-job budget
# ---------------------------------------------------------------------------

class JobBudgetExceeded(RunawayTrapFired):
    """Pre-call estimate would push job spend above its cap."""

    trap = "JobBudgetExceeded"


# ---------------------------------------------------------------------------
# Trap 4 — daily / monthly caps
# ---------------------------------------------------------------------------

class DailyBudgetExceeded(RunawayTrapFired):
    """Daily hard cap reached. Override only via the ``--force`` CLI flag."""

    trap = "DailyBudgetExceeded"


class MonthlyBudgetExceeded(RunawayTrapFired):
    """Monthly hard cap reached. Override only by editing config.yaml."""

    trap = "MonthlyBudgetExceeded"


# ---------------------------------------------------------------------------
# Trap 5 — wall-clock timeouts
# ---------------------------------------------------------------------------

class CallTimeoutExceeded(RunawayTrapFired):
    """A single Claude API call exceeded its 90-second deadline."""

    trap = "CallTimeoutExceeded"


class AgentTimeoutExceeded(RunawayTrapFired):
    """An agent's full run exceeded its 5-minute deadline."""

    trap = "AgentTimeoutExceeded"


class JobTimeoutExceeded(RunawayTrapFired):
    """A job's orchestrator exceeded its 20-minute deadline."""

    trap = "JobTimeoutExceeded"


# ---------------------------------------------------------------------------
# Trap 6 — critic-revise loop bound
# ---------------------------------------------------------------------------

class CriticReviseLimitExceeded(RunawayTrapFired):
    """Critic-revise loop attempted a second revision pass."""

    trap = "CriticReviseLimitExceeded"


# ---------------------------------------------------------------------------
# Trap 7 — concurrent job semaphore
# ---------------------------------------------------------------------------

class ConcurrentJobLimitExceeded(RunawayTrapFired):
    """Too many jobs in flight; caller did not wait on the semaphore.

    The semaphore in :mod:`backend.cost_control.concurrency` normally
    *blocks* until a slot frees. This exception only fires if a caller
    explicitly opts into the non-blocking ``try_acquire`` path.
    """

    trap = "ConcurrentJobLimitExceeded"


# ---------------------------------------------------------------------------
# Misc — model registry
# ---------------------------------------------------------------------------

class UnknownModel(RunawayTrapFired):
    """The model name is not in :data:`backend.cost_control.pricing.PRICES`.

    Treated as a trap firing because we cannot safely cost-control a
    call against a model whose price we do not know. The fix is to
    update the pricing table, not to silently accept the call.
    """

    trap = "UnknownModel"
