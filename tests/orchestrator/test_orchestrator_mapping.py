"""Tests for :meth:`backend.orchestrator.Orchestrator.run_mapping`.

Step 14 ships the fake-only Stage 2 product-mapping orchestration. The
orchestrator runs the :class:`ProductMappingAgent` *in place* while
the job sits at :attr:`JobState.APPROVED`: success writes
``product_mapping.json`` and leaves the state alone; failure stamps
``last_error`` (and bumps ``trap_triggers`` for runaway events) but
still leaves ``current_state`` at ``approved`` so the operator can
retry without re-running Stage 1. No new state-machine edges are
introduced — ``approved → generating_documents`` stays closed until
the writer agents land.

Everything runs through :class:`tests.orchestrator.conftest.FakeCloudClient`
— the orchestrator never touches the real Anthropic SDK or the
Keychain.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.briefing_models import Briefing
from backend.agents.mapping_models import (
    Confidence,
    KnowledgeExcerptRef,
    KnowledgeSource,
    NeedProductMatch,
    ProductLine,
    ProductMapping,
    ProductReference,
    UnmatchedNeed,
)
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.config_loader import Models
from backend.cost_control.exceptions import (
    JobBudgetExceeded,
    RunawayTrapFired,
)
from backend.db.models import Job
from backend.jobs.state import JobState, TransitionRecord
from backend.jobs.storage import (
    JobNotFound,
    append_transition,
    read_product_mapping,
    write_briefing,
)
from backend.orchestrator import (
    JobNotInExpectedState,
    Orchestrator,
)
from tests.orchestrator.conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(model_instance) -> object:
    """Wrap a Pydantic instance in a text-block CloudCallResult."""
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


def _seed_to_approved(job_id: str) -> None:
    """Walk ``state.json`` from CREATED to APPROVED.

    Uses :func:`append_transition` directly (which intentionally
    does not re-validate edges) so a test can stage a job at
    ``approved`` without the full Stage 1 chain having run. Two
    hops — created→approved (one synthetic transition) suffices
    because the orchestrator's precondition only inspects
    ``current_state``, not the audit-trail shape.
    """
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.APPROVED,
            at=datetime(2026, 5, 27, 12, 5, 0, tzinfo=timezone.utc),
            reason="seed_to_approved",
        ),
    )


def _seed_mapping_inputs(
    job_id: str,
    sample_briefing: Briefing,
) -> None:
    """Common setup for ``run_mapping`` tests: write the approved
    briefing artefact and move state.json to APPROVED."""
    write_briefing(job_id, sample_briefing)
    _seed_to_approved(job_id)


def _sample_product_mapping() -> ProductMapping:
    """Populated :class:`ProductMapping` for the agent's scripted
    return value. Two excerpts so the round-trip exercises both
    enum members; one match, one unmatched."""
    return ProductMapping(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        mapped_at=date(2026, 5, 27),
        matches=[
            NeedProductMatch(
                need_priority=1,
                need_summary="Modernise SD-WAN",
                products=[
                    ProductReference(
                        product_line=ProductLine.EMULATORS,
                        product_name="Netropy 100G",
                        knowledge_excerpt_refs=[0],
                    )
                ],
                use_case_framing=(
                    "Lab-emulate the planned SD-WAN topology before "
                    "cutover."
                ),
                why_this_fits=(
                    "Briefing flags an active SD-WAN programme."
                ),
                confidence=Confidence.HIGH,
            )
        ],
        unmatched_needs=[
            UnmatchedNeed(
                need_priority=2,
                need_summary="Refresh SIEM tooling",
                reason="Outside ANS lab/network testing portfolio.",
            )
        ],
        knowledge_excerpts=[
            KnowledgeExcerptRef(
                source_file=KnowledgeSource.PRODUCTS_EMULATORS,
                heading="Netropy Network Emulators",
                rationale="Bandwidth and impairment range for SD-WAN.",
            ),
        ],
        gaps=[],
    )


_KNOWLEDGE_BUNDLE = (
    "## products/emulators.md\n\n"
    "Netropy 100G — high-bandwidth network emulator. Marker: "
    "UNIQUE_EMULATOR_MARKER_xyz."
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_run_mapping_happy_path_returns_mapping_and_persists_artefact(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    _seed_mapping_inputs(seeded_job, sample_briefing)
    expected = _sample_product_mapping()
    fake_cloud_client.script([_resp(expected)])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    result = asyncio.run(
        orch.run_mapping(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
        )
    )

    assert isinstance(result, ProductMapping)
    # The agent ran exactly once.
    assert len(fake_cloud_client.calls) == 1

    # product_mapping.json is on disk and round-trips.
    rebuilt = read_product_mapping(seeded_job)
    assert isinstance(rebuilt, ProductMapping)
    assert rebuilt == expected


def test_run_mapping_happy_path_leaves_state_at_approved(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """In-place model: ``current_state`` stays ``approved``, no
    new transition is appended on success, ``last_error`` stays
    ``None``."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(_sample_product_mapping())])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    payload_before = _state_payload(isolated_jobs_root, seeded_job)
    transitions_before = list(payload_before["transitions"])

    asyncio.run(
        orch.run_mapping(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
        )
    )

    payload_after = _state_payload(isolated_jobs_root, seeded_job)
    assert payload_after["current_state"] == JobState.APPROVED.value
    assert payload_after["transitions"] == transitions_before
    assert payload_after["last_error"] is None


def test_run_mapping_happy_path_does_not_touch_job_status(
    seeded_job: str,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """``Job.status`` reflects the row's last orchestrator-driven
    state. ``run_mapping`` does not transition the job, so it must
    not write to ``Job.status`` either. ``trap_triggers`` must also
    stay clean on the happy path."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(_sample_product_mapping())])

    db_session.expire_all()
    job_before = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    status_before = job_before.status

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_mapping(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
        )
    )

    db_session.expire_all()
    job_after = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job_after.status == status_before
    assert job_after.trap_triggers in (None, "")


def test_run_mapping_forwards_briefing_and_bundle_and_user_context(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """The single agent call must carry, in its prompt body, the
    knowledge bundle, the briefing JSON, and the operator
    user_context. We identify each via a unique substring."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(_sample_product_mapping())])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_mapping(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
            user_context="lean on the SD-WAN angle",
        )
    )

    body = fake_cloud_client.calls[0]["messages"][0]["content"]
    # Knowledge bundle's unique marker.
    assert "UNIQUE_EMULATOR_MARKER_xyz" in body
    # Briefing JSON signal — the briefing's source URL is unique.
    assert "https://acme.example.com/news/cloud" in body
    # User context surfaced verbatim.
    assert "lean on the SD-WAN angle" in body


def test_run_mapping_uses_research_model_role(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Per ``backend/agents/mapping.py``, the agent's ``role`` is
    ``research_model``. Pin that the orchestrator threads the model
    id through correctly by reading it off the captured call kwargs."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(_sample_product_mapping())])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_mapping(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
        )
    )

    assert fake_cloud_client.calls[0]["model"] == "test-research-model"


# ---------------------------------------------------------------------------
# Failure paths — agent-side
# ---------------------------------------------------------------------------

def test_run_mapping_trap_records_failure_in_place(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A trap raised by the agent must:

    * re-raise the same exception unchanged;
    * stamp ``last_error`` with ``runaway_trap`` + the trap's reason;
    * bump ``Job.trap_triggers`` with the trap class name;
    * leave ``current_state`` at ``approved`` and add no new
      transitions;
    * leave no ``product_mapping.json`` on disk.
    """
    _seed_mapping_inputs(seeded_job, sample_briefing)
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="mapping",
    )
    fake_cloud_client.script([trap])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired) as exc_info:
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )
    assert exc_info.value is trap

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    # No transition appended on mapping failure.
    edges = [(t["from"], t["to"]) for t in payload["transitions"]]
    assert (JobState.APPROVED.value, JobState.FAILED.value) not in edges
    assert payload["last_error"]["category"] == "runaway_trap"
    assert (
        payload["last_error"]["details"] == "would exceed per-job budget"
    )

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert json.loads(job.trap_triggers) == ["JobBudgetExceeded"]

    folder = isolated_jobs_root / seeded_job
    assert not (folder / "product_mapping.json").exists()


def test_run_mapping_output_invalid_records_failure_no_trap_bump(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Two unparseable responses → AgentOutputInvalid → in-place
    failure record. ``trap_triggers`` must NOT be touched (output
    failures are not runaway events)."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    bad = make_response(text="not json at all")
    fake_cloud_client.script([bad, bad])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"]["category"] == "output_invalid"

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.trap_triggers in (None, "")

    folder = isolated_jobs_root / seeded_job
    assert not (folder / "product_mapping.json").exists()


def test_run_mapping_unexpected_exception_records_crashed(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A non-trap, non-output exception → category ``crashed``.
    State stays at ``approved``; no trap bump."""
    _seed_mapping_inputs(seeded_job, sample_briefing)
    boom = RuntimeError("network exploded")
    fake_cloud_client.script([boom])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"]["category"] == "crashed"
    assert "network exploded" in payload["last_error"]["details"]

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.trap_triggers in (None, "")


# ---------------------------------------------------------------------------
# Failure paths — caller-side (no last_error written)
# ---------------------------------------------------------------------------

def test_run_mapping_rejects_job_not_at_approved(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """A job whose ``current_state`` is not ``approved`` must be
    rejected with :class:`JobNotInExpectedState` *before* any agent
    call. ``last_error`` must not be written — this is a caller
    mistake, not an agent failure."""
    # seeded_job is at CREATED; do not transition or seed a briefing.
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotInExpectedState) as exc_info:
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )
    assert exc_info.value.actual is JobState.CREATED
    assert exc_info.value.expected is JobState.APPROVED

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


def test_run_mapping_raises_when_briefing_missing(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """If ``briefing.json`` is absent, the underlying
    :class:`JobNotFound` propagates unchanged. ``last_error`` must
    not be written — the operator's mistake (running mapping before
    the briefing exists) is not an agent failure."""
    # Move to APPROVED but do NOT write briefing.json.
    _seed_to_approved(seeded_job)

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotFound):
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


def test_run_mapping_raises_when_briefing_corrupt(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """A schema-invalid briefing on disk surfaces as
    :class:`pydantic.ValidationError`; the orchestrator does not
    catch it and does not stamp ``last_error``."""
    _seed_to_approved(seeded_job)
    folder = isolated_jobs_root / seeded_job
    (folder / "briefing.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(ValidationError):
        asyncio.run(
            orch.run_mapping(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None
