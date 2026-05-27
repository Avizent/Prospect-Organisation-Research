"""Tests for :mod:`backend.jobs.intake`.

Covers the end-to-end intake contract: validation, normalisation,
DB row creation, company upsert, folder creation, initial state.json
write. No state transitions, no ResearchAgent — Step 7a scope.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Company, Job
from backend.jobs.intake import (
    IntakeResult,
    IntakeValidationError,
    create_job,
)
from backend.jobs.state import JobState


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_create_job_inserts_job_row_at_created(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    assert isinstance(result, IntakeResult)
    # Job id is a UUID string.
    uuid.UUID(result.job_id)

    job = db_session.execute(
        select(Job).where(Job.id == result.job_id)
    ).scalar_one()
    assert job.status == JobState.CREATED.value
    assert job.company_id == result.company_id
    assert job.folder_path == str(result.folder_path)
    assert job.started_at is not None


def test_create_job_creates_company_row(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    company = db_session.execute(
        select(Company).where(Company.name == "Acme Ltd")
    ).scalar_one()
    assert company.website_url == "https://acme.example.com/"
    assert company.research_count == 1
    assert company.first_researched_at is not None
    assert company.last_researched_at is not None


def test_create_job_creates_folder_and_state_file(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    folder = isolated_jobs_root / result.job_id
    assert folder.exists()
    state_path = folder / "state.json"
    assert state_path.exists()

    payload = json.loads(state_path.read_text("utf-8"))
    assert payload["job_id"] == result.job_id
    assert payload["current_state"] == "created"
    assert payload["transitions"] == []
    assert payload["last_error"] is None
    assert payload["company_name"] == "Acme Ltd"


def test_folder_path_in_db_is_absolute(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    assert Path(result.folder_path).is_absolute()


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_company_name_whitespace_is_collapsed(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    result = create_job(
        db=db_session,
        company_name="  Acme   Ltd  ",
        company_url="https://acme.example.com/",
    )
    company = db_session.execute(
        select(Company).where(Company.id == result.company_id)
    ).scalar_one()
    assert company.name == "Acme Ltd"


def test_company_url_whitespace_is_stripped(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="  https://acme.example.com/  ",
    )
    company = db_session.execute(
        select(Company).where(Company.id == result.company_id)
    ).scalar_one()
    assert company.website_url == "https://acme.example.com/"


# ---------------------------------------------------------------------------
# Upsert semantics
# ---------------------------------------------------------------------------

def test_repeat_intake_for_same_company_reuses_company_row(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    first = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    second = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    # Same company_id, two different job_ids.
    assert first.company_id == second.company_id
    assert first.job_id != second.job_id

    company = db_session.execute(
        select(Company).where(Company.id == first.company_id)
    ).scalar_one()
    assert company.research_count == 2


def test_intake_for_different_url_creates_distinct_company(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    first = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    second = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.co.uk/",
    )
    assert first.company_id != second.company_id


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

def test_empty_company_name_rejected(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError) as exc_info:
        create_job(
            db=db_session,
            company_name="   ",
            company_url="https://acme.example.com/",
        )
    assert exc_info.value.field == "company_name"


def test_missing_company_name_rejected(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError) as exc_info:
        create_job(
            db=db_session,
            company_name=None,  # type: ignore[arg-type]
            company_url="https://acme.example.com/",
        )
    assert exc_info.value.field == "company_name"


def test_malformed_url_rejected(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError) as exc_info:
        create_job(
            db=db_session,
            company_name="Acme Ltd",
            company_url="not a url",
        )
    assert exc_info.value.field == "company_url"


def test_empty_url_rejected(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError) as exc_info:
        create_job(
            db=db_session,
            company_name="Acme Ltd",
            company_url="   ",
        )
    assert exc_info.value.field == "company_url"


def test_intake_validation_error_does_not_echo_offending_value() -> None:
    """The exception message must not contain the raw rejected value
    — defence against accidentally surfacing a typo'd name in logs."""
    exc = IntakeValidationError(field="company_name", reason="must not be empty")
    assert "must not be empty" in str(exc)
    # And nothing else suspicious; the message is short and structural.
    assert len(str(exc)) < 80


# ---------------------------------------------------------------------------
# Validation failure leaves no DB row / folder behind
# ---------------------------------------------------------------------------

def test_validation_failure_creates_no_job_row(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError):
        create_job(
            db=db_session,
            company_name="",
            company_url="https://acme.example.com/",
        )
    rows = db_session.execute(select(Job)).scalars().all()
    assert rows == []


def test_validation_failure_creates_no_folder(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    with pytest.raises(IntakeValidationError):
        create_job(
            db=db_session,
            company_name="Acme Ltd",
            company_url="not a url",
        )
    # Nothing should appear under jobs_root.
    if isolated_jobs_root.exists():
        assert list(isolated_jobs_root.iterdir()) == []


# ---------------------------------------------------------------------------
# Injected clock
# ---------------------------------------------------------------------------

def test_now_argument_is_used_for_timestamps(
    db_session: Session, isolated_jobs_root: Path
) -> None:
    fixed = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        now=fixed,
    )
    job = db_session.execute(
        select(Job).where(Job.id == result.job_id)
    ).scalar_one()
    # SQLAlchemy may strip tz on round-trip; compare on the naive UTC value.
    assert job.started_at.replace(tzinfo=timezone.utc) == fixed or job.started_at == fixed.replace(tzinfo=None)
