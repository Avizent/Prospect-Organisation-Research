"""Tests for :class:`backend.orchestrator.Orchestrator.run_research`.

Step 7b ships the fake-only research orchestration path. These
tests exercise:

* happy path → ``briefing_ready`` (DB status, state.json transitions,
  dossier file on disk)
* :class:`RunawayTrapFired` from the agent → ``failed`` + trap label
  + ``trap_triggers`` JSON array populated with the trap class name
* :class:`AgentOutputInvalid` from the agent → ``failed`` +
  ``output_invalid`` label, **no** ``trap_triggers`` update
* other ``Exception`` → ``failed`` + ``crashed`` label, **no**
  ``trap_triggers`` update
* invocation when the job is not at ``created`` → raises
  :class:`JobNotInExpectedState` and changes nothing on disk
* ``user_context`` is forwarded into the agent prompt verbatim

All tests run through a hand-rolled :class:`FakeCloudClient` —
nothing here goes near the real Anthropic SDK or the Keychain.

Why ``asyncio.run`` rather than ``pytest.mark.asyncio``
------------------------------------------------------
The runaway tests under ``tests/runaway/`` already use the
``asyncio.run(main())``-inside-a-sync-test pattern. Keeping that
convention here means the suite has one async-test idiom across
the codebase and we don't depend on the optional
``pytest-asyncio`` plugin being installed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.output import AgentOutputInvalid
from backend.agents.research_models import ResearchDossier
from backend.cost_control.config_loader import Models
from backend.cost_control.exceptions import (
    JobBudgetExceeded,
    RunawayTrapFired,
)
from backend.db.models import Job
from backend.jobs.state import JobState, TransitionRecord
from backend.jobs.storage import append_transition
from backend.orchestrator import (
    JobNotInExpectedState,
    Orchestrator,
)
from tests.orchestrator.conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dossier_response(dossier: ResearchDossier) -> object:
    """Script a CloudCallResult whose text block is the dossier JSON.

    The base agent extracts the first ``text`` block from the SDK
    response, JSON-decodes it, and Pydantic-validates against
    :class:`ResearchDossier`. So the simplest way to make the
    fake agent succeed is to hand it the dossier as JSON text.
    """
    return make_response(text=dossier.model_dump_json())


def _state_payload(jobs_root: Path, job_id: str) -> dict:
    return json.loads(
        (jobs_root / job_id / "state.json").read_text(encoding="utf-8")
    )


def _make_orchestrator(
    *,
    client: FakeCloudClient,
    models: Models,
    session_factory,
) -> Orchestrator:
    return Orchestrator(
        client=client,
        models=models,
        db_session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_transitions_to_briefing_ready(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    fake_cloud_client.script([_dossier_response(sample_dossier)])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    dossier = asyncio.run(orch.run_research(job_id=seeded_job))

    assert isinstance(dossier, ResearchDossier)
    # DB status is the final canonical handover state.
    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.BRIEFING_READY.value
    # No trap fired → trap_triggers untouched.
    assert job.trap_triggers in (None, "")


def test_happy_path_state_json_contains_both_transitions(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    fake_cloud_client.script([_dossier_response(sample_dossier)])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_research(job_id=seeded_job))

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.BRIEFING_READY.value
    edges = [(t["from"], t["to"]) for t in payload["transitions"]]
    assert edges == [
        ("created", "researching"),
        ("researching", "briefing_ready"),
    ]
    assert payload["last_error"] is None


def test_happy_path_writes_dossier_file(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    fake_cloud_client.script([_dossier_response(sample_dossier)])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_research(job_id=seeded_job))

    dossier_path = isolated_jobs_root / seeded_job / "research_dossier.json"
    assert dossier_path.exists()
    payload = json.loads(dossier_path.read_text(encoding="utf-8"))
    assert payload["company_name"] == "Acme Ltd"


def test_user_context_is_forwarded_into_prompt(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    """``user_context`` lands inside the user message body verbatim.

    Pins the orchestrator's threading of the optional hint through
    to the agent's ``user_prompt`` builder."""
    fake_cloud_client.script([_dossier_response(sample_dossier)])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_research(
            job_id=seeded_job, user_context="focus on healthcare divisions"
        )
    )
    assert len(fake_cloud_client.calls) == 1
    body = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "focus on healthcare divisions" in body


# ---------------------------------------------------------------------------
# RunawayTrap path
# ---------------------------------------------------------------------------

def test_runaway_trap_marks_job_failed_and_records_trap(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="research",
    )
    fake_cloud_client.script([trap])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired) as exc_info:
        asyncio.run(orch.run_research(job_id=seeded_job))
    # The exact exception object propagates unchanged.
    assert exc_info.value is trap

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    assert job.trap_triggers is not None
    assert json.loads(job.trap_triggers) == ["JobBudgetExceeded"]


def test_runaway_trap_writes_failed_transition_and_last_error(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    trap = JobBudgetExceeded("would exceed per-job budget")
    fake_cloud_client.script([trap])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    with pytest.raises(RunawayTrapFired):
        asyncio.run(orch.run_research(job_id=seeded_job))

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.FAILED.value
    edges = [(t["from"], t["to"]) for t in payload["transitions"]]
    assert edges == [
        ("created", "researching"),
        ("researching", "failed"),
    ]
    assert payload["last_error"] == {
        "category": "runaway_trap",
        "details": "would exceed per-job budget",
    }


# ---------------------------------------------------------------------------
# AgentOutputInvalid path
# ---------------------------------------------------------------------------

def test_output_invalid_marks_job_failed_without_trap_trigger(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """Parse/validation failure twice → AgentOutputInvalid → failed.

    The wrapper retries once with a stricter reminder, so we script
    two unparseable responses.
    """
    bad = make_response(text="not json at all")
    fake_cloud_client.script([bad, bad])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(orch.run_research(job_id=seeded_job))

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    # Output failures are NOT runaway events.
    assert job.trap_triggers in (None, "")

    payload = _state_payload(isolated_jobs_root, seeded_job)
    edges = [(t["from"], t["to"]) for t in payload["transitions"]]
    assert edges == [
        ("created", "researching"),
        ("researching", "failed"),
    ]
    assert payload["last_error"]["category"] == "output_invalid"


# ---------------------------------------------------------------------------
# Generic crash path
# ---------------------------------------------------------------------------

def test_unexpected_exception_marks_job_failed_with_crashed_label(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    boom = RuntimeError("network exploded")
    fake_cloud_client.script([boom])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(orch.run_research(job_id=seeded_job))

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    assert job.trap_triggers in (None, "")

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"]["category"] == "crashed"
    assert "network exploded" in payload["last_error"]["details"]


# ---------------------------------------------------------------------------
# Wrong-starting-state guard
# ---------------------------------------------------------------------------

def test_run_research_rejects_job_not_in_created(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    # Manually move the on-disk state to researching, leaving the
    # script empty — the agent must never be called.
    append_transition(
        seeded_job,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.RESEARCHING,
            at=datetime(2026, 5, 27, 12, 1, 0, tzinfo=timezone.utc),
            reason="manual",
        ),
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    with pytest.raises(JobNotInExpectedState) as exc_info:
        asyncio.run(orch.run_research(job_id=seeded_job))
    assert exc_info.value.actual is JobState.RESEARCHING
    # Agent must not have been called.
    assert fake_cloud_client.calls == []
