"""Tests for the approval and regeneration boundaries.

Five functions are exercised here:

* :func:`approve` — supports both ``briefing_ready → approved``
  (approve-as-is) and ``user_editing → approved`` (after edits).
  Must reload + validate briefing.json before transitioning;
  failure to validate must leave the job in its prior state.
* :func:`request_regeneration` — ``user_editing →
  regenerating_section``, with the chosen section recorded in the
  transition reason.
* :func:`complete_regeneration` — ``regenerating_section →
  user_editing`` after a successful rewrite.
* :func:`fail_regeneration` — ``regenerating_section → failed``
  with ``last_error`` stamped on state.json and ``Job.status``
  updated.

Every test asserts disk + DB stay aligned and that illegal-state
calls raise :class:`EditNotAllowed` without touching any
projection.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Job
from backend.jobs.approval import (
    BriefingSection,
    EditNotAllowed,
    TransitionApplied,
    approve,
    complete_regeneration,
    fail_regeneration,
    request_regeneration,
)
from backend.jobs.state import JobState
from backend.jobs.storage import (
    JobNotFound,
    read_state,
)


# ---------------------------------------------------------------------------
# approve() — happy paths
# ---------------------------------------------------------------------------

def test_approve_as_is_from_briefing_ready(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    """Approve-as-is: briefing_ready → approved without an intervening
    user_editing visit."""
    result = approve(
        db=db_session,
        job_id=briefing_ready_job,
        approved_by="rcp@avizent.com",
        now=fixed_now,
    )

    assert isinstance(result, TransitionApplied)
    assert result.from_state is JobState.BRIEFING_READY
    assert result.to_state is JobState.APPROVED

    after = read_state(briefing_ready_job)
    assert after.current_state is JobState.APPROVED
    assert after.transitions[-1]["from"] == "briefing_ready"
    assert after.transitions[-1]["to"] == "approved"
    assert "approved_by=rcp@avizent.com" in after.transitions[-1]["reason"]

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == briefing_ready_job)
    ).scalar_one()
    assert job.status == JobState.APPROVED.value


def test_approve_after_edit_from_user_editing(
    db_session: Session,
    editing_job: str,
    fixed_now: datetime,
) -> None:
    """Approve-after-edit: user_editing → approved."""
    result = approve(
        db=db_session,
        job_id=editing_job,
        approved_by="rcp@avizent.com",
        now=fixed_now,
    )
    assert result.from_state is JobState.USER_EDITING
    assert result.to_state is JobState.APPROVED

    after = read_state(editing_job)
    assert after.current_state is JobState.APPROVED


def test_approved_by_recorded_verbatim(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    approve(
        db=db_session,
        job_id=briefing_ready_job,
        approved_by="  Operator Name  ",
        now=fixed_now,
    )
    after = read_state(briefing_ready_job)
    # Stripped, embedded verbatim in the transition reason.
    assert after.transitions[-1]["reason"] == (
        "approved_by=Operator Name"
    )


# ---------------------------------------------------------------------------
# approve() — guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "regenerating_job",
        "approved_job",
    ],
)
def test_approve_in_illegal_state_raises(
    request: pytest.FixtureRequest,
    db_session: Session,
    fixture_name: str,
    fixed_now: datetime,
) -> None:
    job_id: str = request.getfixturevalue(fixture_name)
    before = read_state(job_id)
    starting_state = before.current_state
    starting_transitions = len(before.transitions)

    with pytest.raises(EditNotAllowed):
        approve(
            db=db_session,
            job_id=job_id,
            approved_by="rcp@avizent.com",
            now=fixed_now,
        )

    after = read_state(job_id)
    assert after.current_state is starting_state
    assert len(after.transitions) == starting_transitions


def test_approve_rejects_empty_approver(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(ValueError):
        approve(
            db=db_session,
            job_id=briefing_ready_job,
            approved_by="   ",
            now=fixed_now,
        )
    # No state change either.
    assert read_state(briefing_ready_job).current_state is JobState.BRIEFING_READY


def test_approve_rejects_non_string_approver(
    db_session: Session,
    briefing_ready_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(TypeError):
        approve(
            db=db_session,
            job_id=briefing_ready_job,
            approved_by=42,  # type: ignore[arg-type]
            now=fixed_now,
        )


def test_approve_with_missing_briefing_propagates_and_leaves_state(
    db_session: Session,
    briefing_ready_job: str,
    isolated_jobs_root,
    fixed_now: datetime,
) -> None:
    """If briefing.json is missing the read raises :class:`JobNotFound`
    and approve() leaves the job in BRIEFING_READY."""
    briefing_path = isolated_jobs_root / briefing_ready_job / "briefing.json"
    briefing_path.unlink()

    with pytest.raises(JobNotFound):
        approve(
            db=db_session,
            job_id=briefing_ready_job,
            approved_by="rcp@avizent.com",
            now=fixed_now,
        )

    after = read_state(briefing_ready_job)
    assert after.current_state is JobState.BRIEFING_READY
    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == briefing_ready_job)
    ).scalar_one()
    assert job.status == JobState.BRIEFING_READY.value


def test_approve_with_malformed_briefing_propagates(
    db_session: Session,
    briefing_ready_job: str,
    isolated_jobs_root,
    fixed_now: datetime,
) -> None:
    """Unparsable JSON surfaces as :class:`json.JSONDecodeError`."""
    briefing_path = isolated_jobs_root / briefing_ready_job / "briefing.json"
    briefing_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        approve(
            db=db_session,
            job_id=briefing_ready_job,
            approved_by="rcp@avizent.com",
            now=fixed_now,
        )

    assert read_state(briefing_ready_job).current_state is JobState.BRIEFING_READY


def test_approve_with_schema_invalid_briefing_propagates(
    db_session: Session,
    briefing_ready_job: str,
    isolated_jobs_root,
    fixed_now: datetime,
) -> None:
    """A briefing whose ``source_indices`` point out of range fails
    :class:`Briefing.model_validate` and :func:`approve` propagates
    the :class:`pydantic.ValidationError`."""
    briefing_path = isolated_jobs_root / briefing_ready_job / "briefing.json"
    raw = json.loads(briefing_path.read_text(encoding="utf-8"))
    # Point the first news claim at a source index that does not exist.
    raw["business_context"]["news"][0]["source_indices"] = [99]
    briefing_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        approve(
            db=db_session,
            job_id=briefing_ready_job,
            approved_by="rcp@avizent.com",
            now=fixed_now,
        )

    assert read_state(briefing_ready_job).current_state is JobState.BRIEFING_READY


# ---------------------------------------------------------------------------
# request_regeneration()
# ---------------------------------------------------------------------------

def test_request_regeneration_happy_path(
    db_session: Session,
    editing_job: str,
    fixed_now: datetime,
) -> None:
    result = request_regeneration(
        db=db_session,
        job_id=editing_job,
        section=BriefingSection.OPPORTUNITY,
        instructions="Rewrite the angle to lead with security.",
        now=fixed_now,
    )
    assert result.from_state is JobState.USER_EDITING
    assert result.to_state is JobState.REGENERATING_SECTION

    after = read_state(editing_job)
    assert after.current_state is JobState.REGENERATING_SECTION
    assert "section=opportunity" in after.transitions[-1]["reason"]
    assert "Rewrite the angle" in after.transitions[-1]["reason"]

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == editing_job)
    ).scalar_one()
    assert job.status == JobState.REGENERATING_SECTION.value


def test_request_regeneration_truncates_long_instructions(
    db_session: Session,
    editing_job: str,
    fixed_now: datetime,
) -> None:
    long = "x" * 500
    request_regeneration(
        db=db_session,
        job_id=editing_job,
        section=BriefingSection.OPPORTUNITY,
        instructions=long,
        now=fixed_now,
    )
    after = read_state(editing_job)
    reason = after.transitions[-1]["reason"]
    # 200-char cap (plus "section=opportunity; instructions=" prefix and "...")
    assert len(reason) < 300
    assert reason.endswith("...")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "briefing_ready_job",
        "regenerating_job",
        "approved_job",
    ],
)
def test_request_regeneration_illegal_state_raises(
    request: pytest.FixtureRequest,
    db_session: Session,
    fixture_name: str,
    fixed_now: datetime,
) -> None:
    job_id: str = request.getfixturevalue(fixture_name)
    before = read_state(job_id)
    starting = before.current_state

    with pytest.raises(EditNotAllowed):
        request_regeneration(
            db=db_session,
            job_id=job_id,
            section=BriefingSection.SNAPSHOT,
            instructions="x",
            now=fixed_now,
        )

    after = read_state(job_id)
    assert after.current_state is starting


def test_request_regeneration_rejects_empty_instructions(
    db_session: Session,
    editing_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(ValueError):
        request_regeneration(
            db=db_session,
            job_id=editing_job,
            section=BriefingSection.OPPORTUNITY,
            instructions="   ",
            now=fixed_now,
        )


def test_request_regeneration_rejects_non_section_enum(
    db_session: Session,
    editing_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(TypeError):
        request_regeneration(
            db=db_session,
            job_id=editing_job,
            section="opportunity",  # type: ignore[arg-type]
            instructions="x",
            now=fixed_now,
        )


# ---------------------------------------------------------------------------
# complete_regeneration()
# ---------------------------------------------------------------------------

def test_complete_regeneration_returns_to_user_editing(
    db_session: Session,
    regenerating_job: str,
    fixed_now: datetime,
) -> None:
    result = complete_regeneration(
        db=db_session,
        job_id=regenerating_job,
        section=BriefingSection.SNAPSHOT,
        now=fixed_now,
    )
    assert result.from_state is JobState.REGENERATING_SECTION
    assert result.to_state is JobState.USER_EDITING

    after = read_state(regenerating_job)
    assert after.current_state is JobState.USER_EDITING
    assert "completed_section=snapshot" in after.transitions[-1]["reason"]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "briefing_ready_job",
        "editing_job",
        "approved_job",
    ],
)
def test_complete_regeneration_illegal_state_raises(
    request: pytest.FixtureRequest,
    db_session: Session,
    fixture_name: str,
    fixed_now: datetime,
) -> None:
    job_id: str = request.getfixturevalue(fixture_name)
    before = read_state(job_id)
    with pytest.raises(EditNotAllowed):
        complete_regeneration(
            db=db_session,
            job_id=job_id,
            section=BriefingSection.SNAPSHOT,
            now=fixed_now,
        )
    after = read_state(job_id)
    assert after.current_state is before.current_state


# ---------------------------------------------------------------------------
# fail_regeneration()
# ---------------------------------------------------------------------------

def test_fail_regeneration_records_failure_and_transitions(
    db_session: Session,
    regenerating_job: str,
    fixed_now: datetime,
) -> None:
    result = fail_regeneration(
        db=db_session,
        job_id=regenerating_job,
        category="runaway_trap",
        details="STOP_PHRASE fired during snapshot rewrite",
        now=fixed_now,
    )
    assert result.from_state is JobState.REGENERATING_SECTION
    assert result.to_state is JobState.FAILED

    after = read_state(regenerating_job)
    assert after.current_state is JobState.FAILED
    assert after.last_error == {
        "category": "runaway_trap",
        "details": "STOP_PHRASE fired during snapshot rewrite",
    }
    assert after.transitions[-1]["from"] == "regenerating_section"
    assert after.transitions[-1]["to"] == "failed"
    assert after.transitions[-1]["reason"] == "runaway_trap"

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == regenerating_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value


@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "briefing_ready_job",
        "editing_job",
        "approved_job",
    ],
)
def test_fail_regeneration_illegal_state_raises(
    request: pytest.FixtureRequest,
    db_session: Session,
    fixture_name: str,
    fixed_now: datetime,
) -> None:
    job_id: str = request.getfixturevalue(fixture_name)
    before = read_state(job_id)
    with pytest.raises(EditNotAllowed):
        fail_regeneration(
            db=db_session,
            job_id=job_id,
            category="crashed",
            details="boom",
            now=fixed_now,
        )
    after = read_state(job_id)
    assert after.current_state is before.current_state
    assert after.last_error == before.last_error


def test_fail_regeneration_rejects_empty_category(
    db_session: Session,
    regenerating_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(ValueError):
        fail_regeneration(
            db=db_session,
            job_id=regenerating_job,
            category="   ",
            details="x",
            now=fixed_now,
        )


def test_fail_regeneration_rejects_empty_details(
    db_session: Session,
    regenerating_job: str,
    fixed_now: datetime,
) -> None:
    with pytest.raises(ValueError):
        fail_regeneration(
            db=db_session,
            job_id=regenerating_job,
            category="crashed",
            details="",
            now=fixed_now,
        )
