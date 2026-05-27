"""Tests for :mod:`backend.jobs.state`.

After Step 10 the live edge set is:

* ``created → researching``
* ``researching → briefing_ready``
* ``researching → failed``
* ``briefing_ready → user_editing``        (Step 10)
* ``briefing_ready → approved``            (Step 10, approve-as-is)
* ``user_editing → approved``              (Step 10)
* ``user_editing → regenerating_section``  (Step 10)
* ``regenerating_section → user_editing``  (Step 10)
* ``regenerating_section → failed``        (Step 10)

These tests assert both halves: every legal pair succeeds and
returns a well-shaped :class:`TransitionRecord`; every other pair
(including self-loops, reverse edges, and any future-stage edge
not yet enabled — e.g. ``approved → generating_documents``) raises
:class:`IllegalTransition`.

A future PR that enables a new edge must update :data:`_LEGAL_EDGES`
here. That's intentional — every new edge in ``_ALLOWED_TRANSITIONS``
is a real protocol change and should be visible in the review
surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.jobs import state as state_mod
from backend.jobs.state import (
    IllegalTransition,
    JobState,
    TransitionRecord,
    apply_transition,
)


# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------

_CANONICAL_VALUES = {
    "created",
    "researching",
    "briefing_ready",
    "user_editing",
    "regenerating_section",
    "approved",
    "generating_documents",
    "complete",
    "failed",
}


def test_enum_matches_canonical_state_list_exactly() -> None:
    """The handover §9.1 list and the enum must agree byte-for-byte."""
    assert {member.value for member in JobState} == _CANONICAL_VALUES


def test_enum_has_no_extra_off_spec_states() -> None:
    """Defence-in-depth: catch a future PR adding a state that bypasses
    the canonical doc (e.g. ``research_complete``)."""
    assert len(JobState) == len(_CANONICAL_VALUES)


# ---------------------------------------------------------------------------
# Allowed-transitions set
# ---------------------------------------------------------------------------

_LEGAL_EDGES: frozenset[tuple[JobState, JobState]] = frozenset({
    # Step 7b
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


def test_allowed_transitions_set_matches_live_edges_exactly() -> None:
    """The live set must match :data:`_LEGAL_EDGES`. A future PR that
    adds an edge must update both ``_ALLOWED_TRANSITIONS`` and this
    file — which forces the change through review."""
    assert state_mod._ALLOWED_TRANSITIONS == _LEGAL_EDGES


# ---------------------------------------------------------------------------
# apply_transition — legal pairs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "from_state,to_state",
    sorted(_LEGAL_EDGES, key=lambda p: (p[0].value, p[1].value)),
    ids=lambda v: v.value if isinstance(v, JobState) else str(v),
)
def test_legal_edge_returns_transition_record(
    from_state: JobState, to_state: JobState
) -> None:
    fixed = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    record = apply_transition(
        from_state=from_state,
        to_state=to_state,
        reason="test edge",
        now=fixed,
    )
    assert isinstance(record, TransitionRecord)
    assert record.from_state is from_state
    assert record.to_state is to_state
    assert record.at == fixed
    assert record.reason == "test edge"


# ---------------------------------------------------------------------------
# apply_transition — illegal pairs
# ---------------------------------------------------------------------------

_ALL_PAIRS = [
    (a, b) for a in JobState for b in JobState
]
_ILLEGAL_PAIRS = [pair for pair in _ALL_PAIRS if pair not in _LEGAL_EDGES]


@pytest.mark.parametrize(
    "from_state,to_state",
    _ILLEGAL_PAIRS,
    ids=lambda v: v.value if isinstance(v, JobState) else str(v),
)
def test_illegal_pair_raises(
    from_state: JobState, to_state: JobState
) -> None:
    with pytest.raises(IllegalTransition) as exc_info:
        apply_transition(from_state=from_state, to_state=to_state)
    assert exc_info.value.from_state is from_state
    assert exc_info.value.to_state is to_state


def test_self_loop_researching_to_researching_is_illegal() -> None:
    """Self-loops are not in the legal set; pin this explicitly so a
    future regression cannot quietly permit them."""
    with pytest.raises(IllegalTransition):
        apply_transition(
            from_state=JobState.RESEARCHING,
            to_state=JobState.RESEARCHING,
        )


def test_reverse_of_legal_edge_is_illegal() -> None:
    """The legal edges are directed; the reverse direction is not
    permitted (a job cannot un-research)."""
    with pytest.raises(IllegalTransition):
        apply_transition(
            from_state=JobState.RESEARCHING,
            to_state=JobState.CREATED,
        )
    with pytest.raises(IllegalTransition):
        apply_transition(
            from_state=JobState.BRIEFING_READY,
            to_state=JobState.RESEARCHING,
        )


def test_approved_to_generating_documents_is_not_yet_enabled() -> None:
    """Sanity-check that downstream Stage 2 edges are still illegal.
    When Stage 2 enables this edge, this test should flip — and that
    flip must appear in the diff.

    Step 10 enabled ``briefing_ready → user_editing``; the next
    illegal edge to pin is the one immediately after the approval
    gate."""
    with pytest.raises(IllegalTransition):
        apply_transition(
            from_state=JobState.APPROVED,
            to_state=JobState.GENERATING_DOCUMENTS,
        )


# ---------------------------------------------------------------------------
# apply_transition — type checks
# ---------------------------------------------------------------------------

def test_from_state_must_be_jobstate_instance() -> None:
    with pytest.raises(TypeError):
        apply_transition(
            from_state="created",  # type: ignore[arg-type]
            to_state=JobState.RESEARCHING,
        )


def test_to_state_must_be_jobstate_instance() -> None:
    with pytest.raises(TypeError):
        apply_transition(
            from_state=JobState.CREATED,
            to_state="researching",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# apply_transition — default timestamp
# ---------------------------------------------------------------------------

def test_transition_default_now_is_utc() -> None:
    """Default ``now`` is wall clock; assert tz-aware UTC."""
    record = apply_transition(
        from_state=JobState.CREATED, to_state=JobState.RESEARCHING
    )
    assert record.at.tzinfo is not None
    offset = record.at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


def test_reason_defaults_to_none() -> None:
    record = apply_transition(
        from_state=JobState.CREATED, to_state=JobState.RESEARCHING
    )
    assert record.reason is None
