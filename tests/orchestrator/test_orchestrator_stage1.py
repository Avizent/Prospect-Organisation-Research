"""Tests for :meth:`backend.orchestrator.Orchestrator.run_stage1`.

Step 9b ships the fake-only full Stage 1 orchestration path. These
tests exercise the four-agent chain (ResearchAgent → ContactExtractor
→ NeedsAgent → BriefingCompiler), shared trap/output-invalid/crash
failure handling, partial-artefact persistence on downstream failure,
prompt threading, and the pre-flight state guard.

Everything runs through :class:`tests.orchestrator.conftest.FakeCloudClient`
— the orchestrator never touches the real Anthropic SDK or the
Keychain.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.briefing_models import Briefing
from backend.agents.contact_models import ContactExtractionResult
from backend.agents.needs_models import NeedsAssessment
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

def _resp(model_instance) -> object:
    """Wrap a Pydantic instance in a text-block CloudCallResult.

    The base agent extracts the first text block from the SDK
    response, JSON-decodes it, and Pydantic-validates against the
    agent's ``output_model``. So the simplest way to script a
    successful call is to hand the fake the artefact as JSON text.
    """
    return make_response(text=model_instance.model_dump_json())


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


def _four_responses(
    *,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> list[object]:
    return [
        _resp(sample_dossier),
        _resp(sample_contacts),
        _resp(sample_needs_assessment),
        _resp(sample_briefing),
    ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_stage1_happy_path_transitions_to_briefing_ready(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    briefing = asyncio.run(orch.run_stage1(job_id=seeded_job))

    assert isinstance(briefing, Briefing)
    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.BRIEFING_READY.value
    assert job.trap_triggers in (None, "")


def test_stage1_state_json_has_two_transitions(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    """Stage 1 still produces exactly two transitions on the success
    path — the four agents run inside the single ``researching``
    state and the terminal hop lands on ``briefing_ready``."""
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.BRIEFING_READY.value
    edges = [(t["from"], t["to"]) for t in payload["transitions"]]
    assert edges == [
        ("created", "researching"),
        ("researching", "briefing_ready"),
    ]
    assert payload["last_error"] is None


def test_stage1_persists_all_four_artefacts(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    folder = isolated_jobs_root / seeded_job
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == [
        "briefing.json",
        "contacts.json",
        "needs_assessment.json",
        "research_dossier.json",
        "state.json",
    ]
    # Each artefact must round-trip as the expected schema; this
    # implicitly proves write_X used the right helper and that the
    # JSON shape is the one the Step 9a reader accepts.
    from backend.jobs.storage import (
        read_briefing,
        read_contacts,
        read_dossier,
        read_needs_assessment,
    )

    assert isinstance(read_dossier(seeded_job), ResearchDossier)
    assert isinstance(read_contacts(seeded_job), ContactExtractionResult)
    assert isinstance(
        read_needs_assessment(seeded_job), NeedsAssessment
    )
    assert isinstance(read_briefing(seeded_job), Briefing)


def test_stage1_invokes_four_agents_in_order(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    """The four agents fire in the order Research → Contacts → Needs →
    Briefing. We identify each call by a unique fragment of its
    system prompt (which BaseAgent concatenates into the user-message
    content for the single-turn call shape)."""
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    assert len(fake_cloud_client.calls) == 4
    bodies = [
        call["messages"][0]["content"] for call in fake_cloud_client.calls
    ]
    # Each agent's system prompt is unique. The Research agent's
    # prompt is about research; ContactExtractor mentions "contact
    # extraction analyst"; NeedsAgent mentions
    # "infrastructure and network sales" with maturity language;
    # BriefingCompiler mentions "MERGE three upstream artefacts".
    assert "contact extraction analyst" in bodies[1]
    assert "MERGE three upstream artefacts" in bodies[3]
    # Sanity-check that the first two calls are NOT the briefing
    # compiler and that the third is not the briefing compiler.
    assert "MERGE three upstream artefacts" not in bodies[0]
    assert "MERGE three upstream artefacts" not in bodies[1]
    assert "MERGE three upstream artefacts" not in bodies[2]
    # And that the contact extractor's call comes before the briefing
    # compiler's call.
    assert "contact extraction analyst" not in bodies[0]
    assert "contact extraction analyst" not in bodies[2]


def test_stage1_passes_dossier_json_to_contact_extractor(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    # The contact extractor is call #2 (index 1). Its body must
    # contain the dossier's company name embedded in the dossier
    # JSON. We pick a stable substring: the dossier's source URL.
    contact_body = fake_cloud_client.calls[1]["messages"][0]["content"]
    assert "https://acme.example.com/news/cio" in contact_body


def test_stage1_passes_dossier_only_to_needs_agent_not_contacts(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    """NeedsAgent must receive ``dossier_json`` and NOT ``contacts_json`` —
    see the design note in backend/agents/needs.py. We assert the
    body does NOT contain the contact's unique email."""
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    needs_body = fake_cloud_client.calls[2]["messages"][0]["content"]
    # Dossier evidence is present.
    assert "https://acme.example.com/news/cio" in needs_body
    # The contact's unique email must NOT appear — it would only be
    # there if the orchestrator wrongly threaded contacts into the
    # needs agent.
    assert "alice@acme.example.com" not in needs_body


def test_stage1_passes_all_three_upstream_artefacts_to_briefing_compiler(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    body = fake_cloud_client.calls[3]["messages"][0]["content"]
    # Dossier signal.
    assert "https://acme.example.com/news/cio" in body
    # Contacts signal.
    assert "alice@acme.example.com" in body
    # Needs signal — the SD-WAN job-posting URL is unique to the
    # needs evidence pointer.
    assert "https://acme.example.com/jobs/sdwan" in body


def test_stage1_user_context_forwarded_to_every_agent(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_stage1(
            job_id=seeded_job,
            user_context="focus on healthcare divisions",
        )
    )

    for call in fake_cloud_client.calls:
        assert "focus on healthcare divisions" in (
            call["messages"][0]["content"]
        )


# ---------------------------------------------------------------------------
# Failure: trap fired by each agent in turn
# ---------------------------------------------------------------------------

def test_stage1_trap_in_research_marks_failed(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """First agent fires a trap → failed, trap recorded, no
    artefacts on disk, downstream agents never called."""
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
        asyncio.run(orch.run_stage1(job_id=seeded_job))
    assert exc_info.value is trap

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    assert json.loads(job.trap_triggers) == ["JobBudgetExceeded"]

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"]["category"] == "runaway_trap"
    assert (
        payload["last_error"]["details"] == "would exceed per-job budget"
    )

    folder = isolated_jobs_root / seeded_job
    assert not (folder / "research_dossier.json").exists()
    assert not (folder / "contacts.json").exists()
    assert not (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()
    # Only the research agent was called.
    assert len(fake_cloud_client.calls) == 1


def test_stage1_trap_in_contact_extractor_marks_failed_keeps_dossier(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    """Second agent fires a trap → failed; dossier is already on disk
    and is left there; later artefacts are absent."""
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="contact_extractor",
    )
    fake_cloud_client.script([_resp(sample_dossier), trap])
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired):
        asyncio.run(orch.run_stage1(job_id=seeded_job))

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    assert json.loads(job.trap_triggers) == ["JobBudgetExceeded"]

    folder = isolated_jobs_root / seeded_job
    assert (folder / "research_dossier.json").exists()
    assert not (folder / "contacts.json").exists()
    assert not (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()


def test_stage1_trap_in_needs_marks_failed_keeps_dossier_and_contacts(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
) -> None:
    trap = JobBudgetExceeded(
        "would exceed per-job budget", job_id=seeded_job, agent="needs"
    )
    fake_cloud_client.script(
        [_resp(sample_dossier), _resp(sample_contacts), trap]
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired):
        asyncio.run(orch.run_stage1(job_id=seeded_job))

    folder = isolated_jobs_root / seeded_job
    assert (folder / "research_dossier.json").exists()
    assert (folder / "contacts.json").exists()
    assert not (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value


def test_stage1_trap_in_briefing_compiler_marks_failed_keeps_first_three(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
) -> None:
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="briefing_compiler",
    )
    fake_cloud_client.script(
        [
            _resp(sample_dossier),
            _resp(sample_contacts),
            _resp(sample_needs_assessment),
            trap,
        ]
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired):
        asyncio.run(orch.run_stage1(job_id=seeded_job))

    folder = isolated_jobs_root / seeded_job
    assert (folder / "research_dossier.json").exists()
    assert (folder / "contacts.json").exists()
    assert (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value


# ---------------------------------------------------------------------------
# Failure: AgentOutputInvalid in the briefing compiler
# ---------------------------------------------------------------------------

def test_stage1_output_invalid_in_briefing_marks_failed_without_trap(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
) -> None:
    """Two unparseable briefing responses → AgentOutputInvalid → failed.
    ``trap_triggers`` must NOT be touched (output failures are not
    runaway events).
    """
    bad = make_response(text="not json at all")
    fake_cloud_client.script(
        [
            _resp(sample_dossier),
            _resp(sample_contacts),
            _resp(sample_needs_assessment),
            bad,
            bad,
        ]
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(orch.run_stage1(job_id=seeded_job))

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.status == JobState.FAILED.value
    assert job.trap_triggers in (None, "")

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"]["category"] == "output_invalid"

    folder = isolated_jobs_root / seeded_job
    assert (folder / "research_dossier.json").exists()
    assert (folder / "contacts.json").exists()
    assert (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()


# ---------------------------------------------------------------------------
# Failure: unexpected exception
# ---------------------------------------------------------------------------

def test_stage1_unexpected_exception_marks_failed_crashed(
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
        asyncio.run(orch.run_stage1(job_id=seeded_job))

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
# Pre-condition: refuse runs when the job is not at CREATED
# ---------------------------------------------------------------------------

def test_stage1_rejects_job_not_in_created(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """The Stage 1 entry point must refuse a job whose on-disk state
    is not ``created``. No agent should be invoked, no transition
    written."""
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
        asyncio.run(orch.run_stage1(job_id=seeded_job))
    assert exc_info.value.actual is JobState.RESEARCHING
    assert fake_cloud_client.calls == []


# ---------------------------------------------------------------------------
# Defence-in-depth: no DB writes outside Job.status / Job.trap_triggers
# ---------------------------------------------------------------------------

def test_stage1_does_not_populate_contacts_or_other_tables(
    seeded_job: str,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    """Stage 1 persists artefacts to disk only; the relational
    ``contacts`` / ``needs`` tables must remain untouched in 9b
    (DB-level persistence is a later step)."""
    from sqlalchemy import func

    from backend.db.models import Contact

    fake_cloud_client.script(
        _four_responses(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        )
    )
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(orch.run_stage1(job_id=seeded_job))

    contacts_count = db_session.execute(
        select(func.count()).select_from(Contact)
    ).scalar_one()
    assert contacts_count == 0
