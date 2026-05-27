"""Job state machine — the canonical lifecycle and live edge set.

The state list mirrors the handover §9.1 exactly:

    created → researching → briefing_ready → user_editing →
    regenerating_section → approved → generating_documents →
    complete | failed

Live edges after Step 10 (briefing approval gate):

* ``created → researching``           (orchestrator picks the job up)
* ``researching → briefing_ready``    (ResearchAgent run succeeded;
  the row is ready for the human approval gate)
* ``researching → failed``            (RunawayTrap, AgentOutputInvalid,
  or any other exception from the agent run)
* ``briefing_ready → user_editing``   (operator opens the briefing for
  inline edits on the approval screen)
* ``briefing_ready → approved``       (operator approves the briefing
  as-is, without editing)
* ``user_editing → approved``         (operator approves the briefing
  after one or more edits)
* ``user_editing → regenerating_section`` (operator asks the system to
  rewrite a single section based on free-text instructions)
* ``regenerating_section → user_editing`` (section regeneration
  produced a valid result; the row returns to the editor)
* ``regenerating_section → failed``   (RunawayTrap, AgentOutputInvalid,
  or any other exception during regeneration)

No other edges are live yet. ``approved → generating_documents``
and beyond are out of scope until Stage 2 lands; any call to
:func:`apply_transition` for those pairs still raises
:class:`IllegalTransition`. Self-loops and reverse edges remain
illegal in every state.

Why this exact set
------------------

A live edge implies *something* is allowed to drive it. Step 10
adds the six approval-gate edges above because Step 10 introduces
the approval-boundary module (:mod:`backend.jobs.approval`) which
drives them. Adding ``approved → generating_documents`` or the
``complete`` edge now would be premature: nothing drives them in
this step, and a permissive transition would silently disguise a
broken handoff into the Stage 2 work.

The test ``tests/jobs/test_state_machine.py`` pins both halves —
every legal edge works, every other pair is illegal — so a future
PR cannot quietly activate an edge without updating the test
(and therefore the review surface).

The transition primitive itself remains pure: it validates input
types, records the timestamp, and returns a typed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import FrozenSet, Tuple


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------

class JobState(str, Enum):
    """The canonical job lifecycle.

    Values are stable strings — they appear verbatim in ``Job.status``
    (database) and ``current_state`` (``state.json``). Renaming a
    member is a migration event.
    """

    CREATED = "created"
    RESEARCHING = "researching"
    BRIEFING_READY = "briefing_ready"
    USER_EDITING = "user_editing"
    REGENERATING_SECTION = "regenerating_section"
    APPROVED = "approved"
    GENERATING_DOCUMENTS = "generating_documents"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class IllegalTransition(Exception):
    """Raised when a (from, to) pair is not in :data:`_ALLOWED_TRANSITIONS`.

    Carries the rejected pair so callers can log structure without
    re-formatting the message.
    """

    def __init__(self, *, from_state: JobState, to_state: JobState) -> None:
        super().__init__(
            f"transition not allowed: {from_state.value} → {to_state.value}"
        )
        self.from_state = from_state
        self.to_state = to_state


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

#: Live edges after Step 10. Step 7b shipped the first three
#: (created→researching, researching→briefing_ready,
#: researching→failed); Step 10 adds the six approval-gate edges
#: that :mod:`backend.jobs.approval` drives. Tests assert this
#: exact set so a future PR cannot activate an edge without
#: updating the test (and therefore the review surface).
_ALLOWED_TRANSITIONS: FrozenSet[Tuple[JobState, JobState]] = frozenset({
    # Step 7b — research stage
    (JobState.CREATED, JobState.RESEARCHING),
    (JobState.RESEARCHING, JobState.BRIEFING_READY),
    (JobState.RESEARCHING, JobState.FAILED),
    # Step 10 — approval gate
    (JobState.BRIEFING_READY, JobState.USER_EDITING),
    (JobState.BRIEFING_READY, JobState.APPROVED),
    (JobState.USER_EDITING, JobState.APPROVED),
    (JobState.USER_EDITING, JobState.REGENERATING_SECTION),
    (JobState.REGENERATING_SECTION, JobState.USER_EDITING),
    (JobState.REGENERATING_SECTION, JobState.FAILED),
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionRecord:
    """Pure-data record of a single transition application.

    Returned by :func:`apply_transition` so callers can persist the
    event (e.g. append to ``state.json``'s ``transitions`` list)
    without re-deriving the timestamp.
    """

    from_state: JobState
    to_state: JobState
    at: datetime
    reason: str | None


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------

def apply_transition(
    *,
    from_state: JobState,
    to_state: JobState,
    reason: str | None = None,
    now: datetime | None = None,
) -> TransitionRecord:
    """Validate and record a state transition.

    Pure function — no I/O, no side effects. Returns a
    :class:`TransitionRecord` describing the event; callers persist
    it however they like.

    Raises :class:`IllegalTransition` if the pair is not in
    :data:`_ALLOWED_TRANSITIONS`. See the module docstring for the
    current edge set.

    The ``now`` argument is injection-friendly for tests; in
    production callers should leave it ``None`` and accept the UTC
    wall clock.
    """
    if not isinstance(from_state, JobState):
        raise TypeError(
            f"from_state must be a JobState, got {type(from_state).__name__}"
        )
    if not isinstance(to_state, JobState):
        raise TypeError(
            f"to_state must be a JobState, got {type(to_state).__name__}"
        )

    if (from_state, to_state) not in _ALLOWED_TRANSITIONS:
        raise IllegalTransition(from_state=from_state, to_state=to_state)

    return TransitionRecord(
        from_state=from_state,
        to_state=to_state,
        at=now if now is not None else datetime.now(timezone.utc),
        reason=reason,
    )
