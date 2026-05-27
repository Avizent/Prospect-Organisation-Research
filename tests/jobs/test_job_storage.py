"""Tests for :mod:`backend.jobs.storage`.

Three responsibilities under test:

* jobs-root resolution honours ``ANS_JOBS_ROOT`` (autouse fixture in
  ``conftest.py`` sets it per test)
* atomic JSON write semantics: tmp file appears, then atomic rename
* dossier round-trip through :func:`write_dossier` /
  :func:`read_dossier` using the ``sample_dossier`` fixture — no
  ResearchAgent involved per Step 7a scope
* state.json round-trip and validation of the on-disk shape
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.agents.research_models import ResearchDossier
from backend.jobs.state import JobState, TransitionRecord
from backend.jobs.storage import (
    JobNotFound,
    append_transition,
    create_job_folder,
    job_folder,
    jobs_root,
    read_dossier,
    read_state,
    record_failure,
    write_dossier,
    write_initial_state,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Jobs-root resolution
# ---------------------------------------------------------------------------

def test_jobs_root_uses_env_var(isolated_jobs_root: Path) -> None:
    """The autouse fixture sets ANS_JOBS_ROOT; this test pins that the
    resolver picks it up."""
    assert jobs_root() == isolated_jobs_root


def test_job_folder_is_under_jobs_root(isolated_jobs_root: Path) -> None:
    job_id = _new_job_id()
    folder = job_folder(job_id)
    assert folder == isolated_jobs_root / job_id


def test_job_folder_rejects_non_uuid_id() -> None:
    with pytest.raises(ValueError):
        job_folder("../etc/passwd")
    with pytest.raises(ValueError):
        job_folder("")


# ---------------------------------------------------------------------------
# Folder creation
# ---------------------------------------------------------------------------

def test_create_job_folder_makes_directory(isolated_jobs_root: Path) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    assert folder.exists()
    assert folder.is_dir()


def test_create_job_folder_is_idempotent(isolated_jobs_root: Path) -> None:
    """A second call must not error even though the folder exists."""
    job_id = _new_job_id()
    create_job_folder(job_id)
    # No exception on the second call:
    folder = create_job_folder(job_id)
    assert folder.exists()


# ---------------------------------------------------------------------------
# state.json round-trip
# ---------------------------------------------------------------------------

def test_write_initial_state_creates_minimum_shape(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)

    record = write_initial_state(
        job_id=job_id,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        created_at=when,
    )
    assert record.current_state is JobState.CREATED
    assert record.transitions == []
    assert record.last_error is None

    on_disk = json.loads(
        (isolated_jobs_root / job_id / "state.json").read_text("utf-8")
    )
    assert on_disk == {
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "current_state": "created",
        "created_at": "2026-05-27T12:00:00Z",
        "transitions": [],
        "last_error": None,
    }


def test_read_state_round_trips(isolated_jobs_root: Path) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    write_initial_state(
        job_id=job_id,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        created_at=when,
    )
    loaded = read_state(job_id)
    assert loaded.job_id == job_id
    assert loaded.company_name == "Acme Ltd"
    assert loaded.company_url == "https://acme.example.com/"
    assert loaded.current_state is JobState.CREATED
    assert loaded.created_at == when


def test_read_state_raises_when_file_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    with pytest.raises(JobNotFound):
        read_state(job_id)


def test_read_state_rejects_invalid_current_state(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "state.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "company_name": "Acme Ltd",
                "company_url": "https://acme.example.com/",
                "current_state": "not_a_real_state",
                "created_at": "2026-05-27T12:00:00Z",
                "transitions": [],
                "last_error": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_state(job_id)


# ---------------------------------------------------------------------------
# Atomic write: no .tmp file left behind
# ---------------------------------------------------------------------------

def test_atomic_write_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    write_initial_state(
        job_id=job_id,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )
    folder = isolated_jobs_root / job_id
    tmps = list(folder.glob("*.tmp"))
    assert tmps == []


# ---------------------------------------------------------------------------
# Dossier round-trip (fixture, no ResearchAgent)
# ---------------------------------------------------------------------------

def test_write_dossier_creates_file(
    isolated_jobs_root: Path, sample_dossier: ResearchDossier
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    path = write_dossier(job_id, sample_dossier)
    assert path.exists()
    assert path.name == "research_dossier.json"


def test_dossier_round_trip_preserves_data(
    isolated_jobs_root: Path, sample_dossier: ResearchDossier
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    write_dossier(job_id, sample_dossier)
    loaded = read_dossier(job_id)
    assert loaded.company_name == sample_dossier.company_name
    assert loaded.retrieved_at == sample_dossier.retrieved_at
    assert len(loaded.senior_hires) == 1
    assert loaded.senior_hires[0].summary == sample_dossier.senior_hires[0].summary
    assert (
        loaded.senior_hires[0].sources[0].confidence
        == sample_dossier.senior_hires[0].sources[0].confidence
    )
    assert loaded.gaps == sample_dossier.gaps


def test_write_dossier_is_idempotent_on_rewrite(
    isolated_jobs_root: Path, sample_dossier: ResearchDossier
) -> None:
    """A second write replaces the first cleanly — no .tmp lingering,
    no exception."""
    job_id = _new_job_id()
    create_job_folder(job_id)
    write_dossier(job_id, sample_dossier)
    write_dossier(job_id, sample_dossier)
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_read_dossier_raises_when_file_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)  # folder exists, file does not
    with pytest.raises(JobNotFound):
        read_dossier(job_id)


def test_read_dossier_validates_with_pydantic(
    isolated_jobs_root: Path,
) -> None:
    """A corrupt file surfaces as Pydantic ValidationError, not a half-
    shaped object — caller decides how to react."""
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    # Missing required fields; Pydantic should reject.
    (folder / "research_dossier.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        read_dossier(job_id)


def test_write_dossier_auto_creates_folder_if_missing(
    isolated_jobs_root: Path, sample_dossier: ResearchDossier
) -> None:
    """Defence: if the folder is missing, write_dossier must create
    it rather than raising — the intake flow may race with cleanup
    on rare error paths."""
    job_id = _new_job_id()
    # Intentionally do not call create_job_folder.
    path = write_dossier(job_id, sample_dossier)
    assert path.exists()


# ---------------------------------------------------------------------------
# append_transition
# ---------------------------------------------------------------------------

def _seed_initial(job_id: str) -> None:
    create_job_folder(job_id)
    write_initial_state(
        job_id=job_id,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        created_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_append_transition_updates_current_state_and_grows_list(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_initial(job_id)
    record = TransitionRecord(
        from_state=JobState.CREATED,
        to_state=JobState.RESEARCHING,
        at=datetime(2026, 5, 27, 12, 1, 0, tzinfo=timezone.utc),
        reason="orchestrator pickup",
    )
    updated = append_transition(job_id, record)

    assert updated.current_state is JobState.RESEARCHING
    assert len(updated.transitions) == 1
    on_disk = json.loads(
        (isolated_jobs_root / job_id / "state.json").read_text("utf-8")
    )
    assert on_disk["current_state"] == "researching"
    assert on_disk["transitions"] == [
        {
            "from": "created",
            "to": "researching",
            "at": "2026-05-27T12:01:00Z",
            "reason": "orchestrator pickup",
        }
    ]


def test_append_transition_appends_in_order(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_initial(job_id)
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.RESEARCHING,
            at=datetime(2026, 5, 27, 12, 1, 0, tzinfo=timezone.utc),
            reason="pickup",
        ),
    )
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.RESEARCHING,
            to_state=JobState.BRIEFING_READY,
            at=datetime(2026, 5, 27, 12, 5, 0, tzinfo=timezone.utc),
            reason="dossier persisted",
        ),
    )
    loaded = read_state(job_id)
    assert loaded.current_state is JobState.BRIEFING_READY
    assert [t["to"] for t in loaded.transitions] == [
        "researching",
        "briefing_ready",
    ]


def test_append_transition_rejects_non_record_value(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_initial(job_id)
    with pytest.raises(TypeError):
        append_transition(job_id, {"from": "created"})  # type: ignore[arg-type]


def test_append_transition_raises_when_no_state_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    with pytest.raises(JobNotFound):
        append_transition(
            job_id,
            TransitionRecord(
                from_state=JobState.CREATED,
                to_state=JobState.RESEARCHING,
                at=datetime.now(timezone.utc),
                reason=None,
            ),
        )


def test_append_transition_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_initial(job_id)
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.RESEARCHING,
            at=datetime(2026, 5, 27, 12, 1, 0, tzinfo=timezone.utc),
            reason=None,
        ),
    )
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# record_failure
# ---------------------------------------------------------------------------

def test_record_failure_writes_last_error_without_changing_state(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_initial(job_id)
    # Move it to researching first so we can prove the state is preserved.
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.RESEARCHING,
            at=datetime(2026, 5, 27, 12, 1, 0, tzinfo=timezone.utc),
            reason="pickup",
        ),
    )
    # And then to failed via append_transition.
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.RESEARCHING,
            to_state=JobState.FAILED,
            at=datetime(2026, 5, 27, 12, 2, 0, tzinfo=timezone.utc),
            reason="runaway_trap",
        ),
    )

    updated = record_failure(
        job_id, category="runaway_trap", details="JobBudgetExceeded fired"
    )

    assert updated.current_state is JobState.FAILED
    assert updated.last_error == {
        "category": "runaway_trap",
        "details": "JobBudgetExceeded fired",
    }
    on_disk = json.loads(
        (isolated_jobs_root / job_id / "state.json").read_text("utf-8")
    )
    assert on_disk["current_state"] == "failed"
    assert on_disk["last_error"] == {
        "category": "runaway_trap",
        "details": "JobBudgetExceeded fired",
    }
    # Transitions list is untouched.
    assert len(on_disk["transitions"]) == 2


def test_record_failure_raises_when_no_state_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    with pytest.raises(JobNotFound):
        record_failure(job_id, category="crashed", details="boom")


def test_record_failure_overwrites_previous_last_error(
    isolated_jobs_root: Path,
) -> None:
    """Second failure on the same row replaces the first — last_error
    is the *most recent* diagnostic, not a list."""
    job_id = _new_job_id()
    _seed_initial(job_id)
    record_failure(job_id, category="output_invalid", details="first")
    updated = record_failure(job_id, category="crashed", details="second")
    assert updated.last_error == {"category": "crashed", "details": "second"}
