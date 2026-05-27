"""Job state machine — the canonical lifecycle, plus an empty edge set.

The state list mirrors the handover §9.1 exactly:

    created → researching → briefing_ready → user_editing →
    regenerating_section → approved → generating_documents →
    complete | failed

Step 7a deliberately enables **no transitions** — every call to
:func:`apply_transition` raises :class:`IllegalTransition`. Intake
opens the row at :attr:`JobState.created` and stops.

Why no edges yet
----------------

A live edge implies *something* is allowed to drive it. In Step 7a
nothing drives anything: no orchestrator, no agent, no semaphore. If
we shipped a permissive edge here, the next step's wiring would have
to thread carefully around a half-built transition table. Empty is
honest, and the test in
``tests/jobs/test_state_machine.py`` pins it so a future PR cannot
silently activate edges without updating the test (and therefore the
review surface).

The transition primitive itself, however, is real — it validates
input types, records the timestamp, and returns a typed result. Step
7b will populate ``_ALLOWED_TRANSITIONS`` to enable the canonical
edges in order.
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

#: Step 7a: deliberately empty. Step 7b will populate this. Tests
#: assert the set is empty so accidental activations are caught in
#: review.
_ALLOWED_TRANSITIONS: FrozenSet[Tuple[JobState, JobState]] = frozenset()


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
    :data:`_ALLOWED_TRANSITIONS`. In Step 7a that set is empty, so
    every call raises. This is intentional — see module docstring.

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
