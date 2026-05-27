"""Tests for :func:`backend.jobs.approval.open_for_editing`.

The function performs one job: apply the ``briefing_ready →
user_editing`` transition. Three things must be true after a
successful call:

* ``state.json``'s ``current_state`` flips to ``user_editing`` and a
  transition record is appended to the ``transitions`` list.
* The DB ``Job.status`` column mirrors the new state.
* The function returns a :class:`TransitionApplied` describing the
  transition for the route layer to log.

The only legal starting state is :attr:`JobState.BRIEFING_READY`.
Every other state raises :class:`EditNotAllowed`; the transition
must not appear in any projection.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.jobs.approval import (
    EditNotAllowed,
    TransitionApplied,
    open_for_editing,
)
from backend.jobs.state import JobState
from backend.jobs.storage import read_state


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_returns_transition_applied(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    result = open_for_editing(
        db=db_session, job_id=briefing_ready_job, now=fixed_now
    )
    assert isinstance(result, TransitionApplied)
    assert result.job_id == briefing_ready_job
    assert result.from_state is JobState.BRIEFING_READY
    assert result.to_state is JobState.USER_EDITING
    assert result.at == fixed_now


def test_state_json_flips_to_user_editing(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    before = read_state(briefing_ready_job)
    assert before.current_state is JobState.BRIEFING_READY
    prior_transition_count = len(before.transitions)

    open_for_editing(db=db_session, job_id=briefing_ready_job, now=fixed_now)

    after = read_state(briefing_ready_job)
    assert after.current_state is JobState.USER_EDITING
    assert len(after.transitions) == prior_transition_count + 1
    last = after.transitions[-1]
    assert last["from"] == JobState.BRIEFING_READY.value
    assert last["to"] == JobState.USER_EDITING.value
    assert last["reason"] == "open_for_editing"


def test_db_job_status_mirrors_new_state(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    open_for_editing(db=db_session, job_id=briefing_ready_job, now=fixed_now)
    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == briefing_ready_job)
    ).scalar_one()
    assert job.status == JobState.USER_EDITING.value


def test_default_now_is_utc(
    db_session: Session,
    briefing_ready_job: str,
) -> None:
    """Omitting ``now`` uses the UTC wall clock."""
    result = open_for_editing(db=db_session, job_id=briefing_ready_job)
    assert result.at.tzinfo is not None
    offset = result.at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name",
    ["created_job", "researching_job", "editing_job", "approved_job"],
)
def test_illegal_state_raises(
    request: pytest.FixtureRequest,
    db_session: Session,
    fixture_name: str,
    fixed_now: datetime,
) -> None:
    """Every state other than ``briefing_ready`` must reject the call."""
    job_id: str = request.getfixturevalue(fixture_name)
    before = read_state(job_id)
    starting_state = before.current_state
    starting_transitions = len(before.transitions)

    with pytest.raises(EditNotAllowed) as exc_info:
        open_for_editing(db=db_session, job_id=job_id, now=fixed_now)

    assert exc_info.value.operation == "open_for_editing"
    assert exc_info.value.current_state is starting_state

    # No transition appended, no DB bump.
    after = read_state(job_id)
    assert after.current_state is starting_state
    assert len(after.transitions) == starting_transitions
