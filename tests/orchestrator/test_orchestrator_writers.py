"""Tests for the Stage 2 writer orchestration methods (Step 18).

The orchestrator gains four public methods:

* :meth:`Orchestrator.run_benefits`
* :meth:`Orchestrator.run_faq`
* :meth:`Orchestrator.run_objections`
* :meth:`Orchestrator.run_stage2_writers` — sequential composition

All four run the writer agent *in place* at :attr:`JobState.APPROVED`
(mirrors :meth:`Orchestrator.run_mapping`): success persists the
matching artefact (``benefits.json`` / ``faq.json`` /
``objections.json``) and leaves ``current_state`` untouched; failure
stamps ``last_error`` (and bumps ``Job.trap_triggers`` for runaway
events) but the job stays at ``approved`` so the operator can retry
without re-running Stage 1. No new state-machine edge is opened —
``approved → generating_documents`` stays closed until real document
generation lands.

Everything runs through :class:`tests.orchestrator.conftest.FakeCloudClient`
— the orchestrator never touches the real Anthropic SDK or the
Keychain. The static fence in
``tests/orchestrator/test_no_production_client_or_keychain.py``
auto-covers any new imports in :mod:`backend.orchestrator`.

Why fake-only
-------------
Step 18 lands the orchestration plumbing. The real Claude API calls
that exercise the writers belong in a later step (the Stage-2 E2E
integration tests). Forcing a real LLM call here would couple a
plumbing test to model availability and cost.
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

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
    Confidence,
)
from backend.agents.briefing_models import Briefing
from backend.agents.faq_models import (
    FAQCategory,
    FAQDocument,
    FAQEntry,
)
from backend.agents.mapping_models import (
    KnowledgeExcerptRef,
    KnowledgeSource,
    NeedProductMatch,
    ProductLine,
    ProductMapping,
    ProductReference,
    UnmatchedNeed,
)
from backend.agents.objections_models import (
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
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
    read_benefits,
    read_faq,
    read_objections,
    write_briefing,
    write_product_mapping,
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
    """Walk ``state.json`` from CREATED to APPROVED via one synthetic
    transition. Same shape as the equivalent helper in
    ``test_orchestrator_mapping.py`` — the orchestrator inspects only
    ``current_state``, not the audit-trail edges."""
    append_transition(
        job_id,
        TransitionRecord(
            from_state=JobState.CREATED,
            to_state=JobState.APPROVED,
            at=datetime(2026, 5, 27, 12, 5, 0, tzinfo=timezone.utc),
            reason="seed_to_approved",
        ),
    )


def _sample_product_mapping() -> ProductMapping:
    """Populated mapping for the upstream artefact each writer reads.

    Carries a UNIQUE marker in the rationale so we can prove the
    orchestrator threaded the mapping JSON into the prompt body.
    """
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
                    "Lab-emulate the planned SD-WAN topology before cutover."
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
                rationale=(
                    "Bandwidth and impairment range for SD-WAN. "
                    "Marker: UNIQUE_MAPPING_MARKER_xyz."
                ),
            ),
        ],
        gaps=[],
    )


def _seed_writer_inputs(
    job_id: str,
    sample_briefing: Briefing,
) -> None:
    """Common setup for writer-orchestration tests: write the approved
    briefing + the upstream product mapping artefacts and move
    ``state.json`` to APPROVED."""
    write_briefing(job_id, sample_briefing)
    write_product_mapping(job_id, _sample_product_mapping())
    _seed_to_approved(job_id)


# ---------------------------------------------------------------------------
# Sample writer outputs
# ---------------------------------------------------------------------------

def _claim() -> BenefitClaim:
    return BenefitClaim(
        claim="Netropy emulates realistic SD-WAN topologies.",
        detail=(
            "Line-rate impairment up to 100G covers the rollout's "
            "aggregate bandwidth without truncation."
        ),
        why_it_matters=(
            "Surfaces SLA-breaching jitter before cutover instead of "
            "in production."
        ),
        knowledge_excerpt_refs=[0],
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )


def _sample_benefits_brief() -> BenefitsBrief:
    return BenefitsBrief(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        executive_summary=BenefitsSection(
            heading="Executive summary",
            audience=Audience.CTO_CIO,
            summary="ANS lab-validates the SD-WAN rollout end-to-end.",
            body=[_claim()],
        ),
        technical_fit=BenefitsSection(
            heading="Technical fit",
            audience=Audience.ENGINEERS,
            summary="Netropy lines up with the planned topology.",
            body=[_claim()],
        ),
        business_case=BenefitsSection(
            heading="Business case",
            audience=Audience.IT_DIRECTOR,
            summary="Catches cutover risk before it hits revenue.",
            body=[_claim()],
        ),
        gaps=[],
    )


def _sample_faq_document() -> FAQDocument:
    categories = [
        FAQCategory.ABOUT_ANS,
        FAQCategory.PRODUCTS,
        FAQCategory.IMPLEMENTATION,
        FAQCategory.COMMERCIAL,
        FAQCategory.SUPPORT,
    ]
    entries = [
        FAQEntry(
            question=f"Question #{i}?",
            answer=(
                "ANS-side answer grounded in the briefing and the "
                "knowledge bundle."
            ),
            category=categories[i % len(categories)],
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            confidence=Confidence.HIGH,
        )
        for i in range(12)
    ]
    return FAQDocument(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        entries=entries,
        gaps=[],
    )


def _sample_objections_register() -> ObjectionsRegister:
    rows = [
        ObjectionRow(
            category=ObjectionCategory.PRICE,
            objection=f"Stated objection #{i}.",
            underlying_concern=(
                "Worried that procurement won't approve the spend."
            ),
            response=(
                "ANS Netropy emulators are line-rate and "
                "impairment-accurate."
            ),
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            escalation_path="Account owner brings in solutions architect.",
            confidence=Confidence.HIGH,
        )
        for i in range(15)
    ]
    return ObjectionsRegister(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        rows=rows,
        gaps=[],
    )


_KNOWLEDGE_BUNDLE = (
    "## products/emulators.md\n\n"
    "Netropy 100G — high-bandwidth network emulator. Marker: "
    "UNIQUE_BUNDLE_MARKER_xyz."
)


# ---------------------------------------------------------------------------
# Per-writer happy paths
# ---------------------------------------------------------------------------

def test_run_benefits_happy_path_persists_artefact_and_keeps_state(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Happy path: agent called once with writer-model id; benefits.json
    round-trips; state stays at APPROVED; no transition appended;
    last_error stays None; Job.status and trap_triggers untouched."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    expected = _sample_benefits_brief()
    fake_cloud_client.script([_resp(expected)])

    payload_before = _state_payload(isolated_jobs_root, seeded_job)
    transitions_before = list(payload_before["transitions"])

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
    result = asyncio.run(
        orch.run_benefits(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert isinstance(result, BenefitsBrief)
    assert len(fake_cloud_client.calls) == 1
    assert fake_cloud_client.calls[0]["model"] == "test-writer-model"

    rebuilt = read_benefits(seeded_job)
    assert rebuilt == expected

    payload_after = _state_payload(isolated_jobs_root, seeded_job)
    assert payload_after["current_state"] == JobState.APPROVED.value
    assert payload_after["transitions"] == transitions_before
    assert payload_after["last_error"] is None

    db_session.expire_all()
    job_after = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job_after.status == status_before
    assert job_after.trap_triggers in (None, "")


def test_run_faq_happy_path_persists_artefact_and_keeps_state(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    _seed_writer_inputs(seeded_job, sample_briefing)
    expected = _sample_faq_document()
    fake_cloud_client.script([_resp(expected)])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    result = asyncio.run(
        orch.run_faq(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert isinstance(result, FAQDocument)
    assert len(fake_cloud_client.calls) == 1
    assert fake_cloud_client.calls[0]["model"] == "test-writer-model"
    assert read_faq(seeded_job) == expected

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"] is None


def test_run_objections_happy_path_persists_artefact_and_keeps_state(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    _seed_writer_inputs(seeded_job, sample_briefing)
    expected = _sample_objections_register()
    fake_cloud_client.script([_resp(expected)])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    result = asyncio.run(
        orch.run_objections(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert isinstance(result, ObjectionsRegister)
    assert len(fake_cloud_client.calls) == 1
    assert fake_cloud_client.calls[0]["model"] == "test-writer-model"
    assert read_objections(seeded_job) == expected

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"] is None


# ---------------------------------------------------------------------------
# Prompt-forwarding — every writer threads briefing + mapping + bundle +
# user_context into the agent call.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method_name,scripted_output",
    [
        ("run_benefits", _sample_benefits_brief),
        ("run_faq", _sample_faq_document),
        ("run_objections", _sample_objections_register),
    ],
)
def test_writer_forwards_briefing_mapping_bundle_and_user_context(
    method_name: str,
    scripted_output,
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """All three writers must thread the same four inputs into the
    prompt body. Unique markers prove identity for each one."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(scripted_output())])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        getattr(orch, method_name)(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
            user_context="lean on the SD-WAN angle",
        )
    )

    body = fake_cloud_client.calls[0]["messages"][0]["content"]
    # Knowledge bundle marker.
    assert "UNIQUE_BUNDLE_MARKER_xyz" in body
    # Product mapping marker — proves the mapping JSON landed in the prompt.
    assert "UNIQUE_MAPPING_MARKER_xyz" in body
    # Briefing JSON signal — the briefing's source URL is unique.
    assert "https://acme.example.com/news/cloud" in body
    # User context surfaced verbatim.
    assert "lean on the SD-WAN angle" in body


# ---------------------------------------------------------------------------
# Per-writer failure paths (trap / output_invalid / crashed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method_name,artefact_filename",
    [
        ("run_benefits", "benefits.json"),
        ("run_faq", "faq.json"),
        ("run_objections", "objections.json"),
    ],
)
def test_writer_trap_records_failure_in_place(
    method_name: str,
    artefact_filename: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A trap raised inside the writer agent must:

    * re-raise unchanged;
    * stamp ``last_error`` with ``runaway_trap`` + the trap's reason;
    * bump ``Job.trap_triggers`` with the trap class name;
    * leave ``current_state`` at APPROVED;
    * leave no artefact on disk.
    """
    _seed_writer_inputs(seeded_job, sample_briefing)
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent=method_name,
    )
    fake_cloud_client.script([trap])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired) as exc_info:
        asyncio.run(
            getattr(orch, method_name)(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )
    assert exc_info.value is trap

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
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
    assert not (folder / artefact_filename).exists()


@pytest.mark.parametrize(
    "method_name,artefact_filename",
    [
        ("run_benefits", "benefits.json"),
        ("run_faq", "faq.json"),
        ("run_objections", "objections.json"),
    ],
)
def test_writer_output_invalid_records_failure_no_trap_bump(
    method_name: str,
    artefact_filename: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Two unparseable responses → AgentOutputInvalid → in-place
    failure record. ``trap_triggers`` is NOT touched (output failures
    are not runaway events)."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    bad = make_response(text="not json at all")
    fake_cloud_client.script([bad, bad])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(
            getattr(orch, method_name)(
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
    assert not (folder / artefact_filename).exists()


@pytest.mark.parametrize(
    "method_name",
    ["run_benefits", "run_faq", "run_objections"],
)
def test_writer_unexpected_exception_records_crashed(
    method_name: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A non-trap, non-output exception → category ``crashed``. State
    stays at APPROVED; no trap bump."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    boom = RuntimeError("network exploded")
    fake_cloud_client.script([boom])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            getattr(orch, method_name)(
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
# Per-writer caller-side rejection (no last_error written)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method_name",
    ["run_benefits", "run_faq", "run_objections"],
)
def test_writer_rejects_job_not_at_approved(
    method_name: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """A job whose ``current_state`` is not APPROVED must be rejected
    with :class:`JobNotInExpectedState` before any agent call.
    ``last_error`` must not be written — caller mistake."""
    # seeded_job is at CREATED; do not seed inputs.
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotInExpectedState) as exc_info:
        asyncio.run(
            getattr(orch, method_name)(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )
    assert exc_info.value.actual is JobState.CREATED
    assert exc_info.value.expected is JobState.APPROVED

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


@pytest.mark.parametrize(
    "method_name",
    ["run_benefits", "run_faq", "run_objections"],
)
def test_writer_raises_when_briefing_missing(
    method_name: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """A missing briefing surfaces :class:`JobNotFound` unchanged.
    ``last_error`` is not stamped — caller mistake."""
    # APPROVED but no briefing.json or product_mapping.json.
    _seed_to_approved(seeded_job)

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotFound):
        asyncio.run(
            getattr(orch, method_name)(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


@pytest.mark.parametrize(
    "method_name",
    ["run_benefits", "run_faq", "run_objections"],
)
def test_writer_raises_when_product_mapping_missing(
    method_name: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Briefing is present but the upstream product mapping is not.
    Surfaces :class:`JobNotFound`; ``last_error`` stays None."""
    write_briefing(seeded_job, sample_briefing)
    _seed_to_approved(seeded_job)
    # Do NOT write product_mapping.json.

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotFound):
        asyncio.run(
            getattr(orch, method_name)(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


@pytest.mark.parametrize(
    "method_name",
    ["run_benefits", "run_faq", "run_objections"],
)
def test_writer_raises_when_briefing_corrupt(
    method_name: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """A schema-invalid briefing surfaces :class:`ValidationError`;
    the orchestrator does not catch it and does not stamp
    ``last_error``."""
    _seed_to_approved(seeded_job)
    folder = isolated_jobs_root / seeded_job
    folder.mkdir(parents=True, exist_ok=True)
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
            getattr(orch, method_name)(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


# ---------------------------------------------------------------------------
# Composition: run_stage2_writers
# ---------------------------------------------------------------------------

def test_run_stage2_writers_happy_path_runs_three_writers_in_order(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Sequential composition: benefits → FAQ → objections. Three
    agent calls total; all three artefacts on disk; state stays at
    APPROVED; no transition appended; last_error stays None.

    The order is locked because the operator deliverable contract
    treats benefits as the headline document; a downstream failure
    in objections should still leave benefits+faq inspectable on
    disk.
    """
    _seed_writer_inputs(seeded_job, sample_briefing)
    expected_benefits = _sample_benefits_brief()
    expected_faq = _sample_faq_document()
    expected_objections = _sample_objections_register()
    fake_cloud_client.script([
        _resp(expected_benefits),
        _resp(expected_faq),
        _resp(expected_objections),
    ])

    payload_before = _state_payload(isolated_jobs_root, seeded_job)
    transitions_before = list(payload_before["transitions"])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    benefits, faq, objections = asyncio.run(
        orch.run_stage2_writers(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert benefits == expected_benefits
    assert faq == expected_faq
    assert objections == expected_objections

    assert len(fake_cloud_client.calls) == 3
    # All three on disk.
    assert read_benefits(seeded_job) == expected_benefits
    assert read_faq(seeded_job) == expected_faq
    assert read_objections(seeded_job) == expected_objections

    payload_after = _state_payload(isolated_jobs_root, seeded_job)
    assert payload_after["current_state"] == JobState.APPROVED.value
    assert payload_after["transitions"] == transitions_before
    assert payload_after["last_error"] is None


def test_run_stage2_writers_stops_on_first_failure_and_leaves_upstream_artefacts(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """If the FAQ writer fails, benefits.json is on disk but
    faq.json and objections.json are not. The objections writer
    must not have been called. last_error reflects the FAQ failure;
    state stays at APPROVED."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    expected_benefits = _sample_benefits_brief()
    bad = make_response(text="not json at all")
    fake_cloud_client.script([
        _resp(expected_benefits),
        # FAQ writer: two unparseable responses → AgentOutputInvalid.
        bad,
        bad,
    ])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(
            orch.run_stage2_writers(
                job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
            )
        )

    # Benefits succeeded, FAQ failed (2 attempts), objections never ran.
    assert len(fake_cloud_client.calls) == 3
    folder = isolated_jobs_root / seeded_job
    assert (folder / "benefits.json").exists()
    assert not (folder / "faq.json").exists()
    assert not (folder / "objections.json").exists()

    # Upstream artefact still round-trips.
    assert read_benefits(seeded_job) == expected_benefits

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"]["category"] == "output_invalid"

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.trap_triggers in (None, "")


def test_run_stage2_writers_first_writer_trap_short_circuits(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A trap on benefits short-circuits the chain: only one agent
    call, no artefacts on disk, last_error stamped, trap_triggers
    bumped."""
    _seed_writer_inputs(seeded_job, sample_briefing)
    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="benefits_writer",
    )
    fake_cloud_client.script([trap])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired):
        asyncio.run(
            orch.run_stage2_writers(
                job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
            )
        )

    assert len(fake_cloud_client.calls) == 1
    folder = isolated_jobs_root / seeded_job
    assert not (folder / "benefits.json").exists()
    assert not (folder / "faq.json").exists()
    assert not (folder / "objections.json").exists()

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"]["category"] == "runaway_trap"

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert json.loads(job.trap_triggers) == ["JobBudgetExceeded"]


def test_run_stage2_writers_rejects_job_not_at_approved(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
) -> None:
    """Composition method shares the same pre-flight gate as the
    individual writers — caller mistake, no ``last_error`` written."""
    # seeded_job is at CREATED.
    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotInExpectedState):
        asyncio.run(
            orch.run_stage2_writers(
                job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None
