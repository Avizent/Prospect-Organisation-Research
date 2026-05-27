"""Tests for :mod:`backend.jobs.state`.

Step 7a wires the enum and the transition primitive, but ships **no
allowed transitions**. These tests pin both halves of that promise:

* the canonical handover state list appears verbatim in the enum
* the transition primitive raises :class:`IllegalTransition` for
  *every* pair (including same-state self-loops)
* the primitive validates types and records the timestamp
* :data:`_ALLOWED_TRANSITIONS` is empty (a future PR that adds an
  edge must update this test, which makes the change reviewable)
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

def test_allowed_transitions_set_is_empty_in_step_7a() -> None:
    """Step 7a deliberately enables no edges. Step 7b will add the
    first. A future PR that activates an edge must update this test."""
    assert state_mod._ALLOWED_TRANSITIONS == frozenset()


# ---------------------------------------------------------------------------
# apply_transition — illegal pairs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("to_state", list(JobState))
def test_every_transition_from_created_is_illegal_in_step_7a(
    to_state: JobState,
) -> None:
    with pytest.raises(IllegalTransition) as exc_info:
        apply_transition(from_state=JobState.CREATED, to_state=to_state)
    assert exc_info.value.from_state is JobState.CREATED
    assert exc_info.value.to_state is to_state


def test_self_loop_created_to_created_is_illegal() -> None:
    with pytest.raises(IllegalTransition):
        apply_transition(
            from_state=JobState.CREATED, to_state=JobState.CREATED
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
# apply_transition — return shape (exercised once the edge set is non-empty)
# ---------------------------------------------------------------------------

def test_transition_record_shape_via_temporary_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """We need at least one legal pair to exercise the success-path
    return contract. Patch the allowed set to contain one edge for
    the duration of this test only."""
    monkeypatch.setattr(
        state_mod,
        "_ALLOWED_TRANSITIONS",
        frozenset({(JobState.CREATED, JobState.RESEARCHING)}),
    )
    fixed_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    record = apply_transition(
        from_state=JobState.CREATED,
        to_state=JobState.RESEARCHING,
        reason="orchestrator pickup",
        now=fixed_now,
    )
    assert isinstance(record, TransitionRecord)
    assert record.from_state is JobState.CREATED
    assert record.to_state is JobState.RESEARCHING
    assert record.at == fixed_now
    assert record.reason == "orchestrator pickup"


def test_transition_default_now_is_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        state_mod,
        "_ALLOWED_TRANSITIONS",
        frozenset({(JobState.CREATED, JobState.FAILED)}),
    )
    record = apply_transition(
        from_state=JobState.CREATED, to_state=JobState.FAILED
    )
    assert record.at.tzinfo is not None
    assert record.at.utcoffset() == record.at.tzinfo.utcoffset(record.at)
    # Specifically UTC offset zero.
    assert record.at.utcoffset().total_seconds() == 0
