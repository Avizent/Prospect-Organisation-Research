"""Tests for the Stage 2 critic orchestration method (Step 20).

The orchestrator gains one public method:

* :meth:`Orchestrator.run_critic`

It runs the critic agent *in place* at :attr:`JobState.APPROVED`
(mirrors :meth:`Orchestrator.run_mapping` and the three writer
methods): success persists ``critic_report.json`` and leaves
``current_state`` untouched; failure stamps ``last_error`` (and
bumps ``Job.trap_triggers`` for runaway events) but the job stays at
``approved`` so the operator can retry without re-running the
writers. No new state-machine edge is opened — ``approved →
generating_documents`` stays closed until real document generation
lands.

The critic's verdict is **advisory** in Step 20: the orchestrator
persists the report regardless of verdict and does not branch on it.
Revision-loop wiring belongs in a later step that touches the state
machine and the retry budget.

Everything runs through :class:`tests.orchestrator.conftest.FakeCloudClient`
— the orchestrator never touches the real Anthropic SDK or the
Keychain. The static fence in
``tests/orchestrator/test_no_production_client_or_keychain.py``
auto-covers any new imports in :mod:`backend.orchestrator`.

Why fake-only
-------------
Step 20 lands the orchestration plumbing. The real Claude API calls
that exercise the critic belong in a later step (the Stage-2 E2E
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
from backend.agents.critic_models import (
    CitedArtefact,
    CriticIssue,
    IssueCategory,
    Severity,
    Stage2CriticReport,
    Verdict,
)
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
    read_critic_report,
    write_benefits,
    write_briefing,
    write_faq,
    write_objections,
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
    transition. Same shape as the helper in the writer/mapping
    orchestrator tests — the orchestrator inspects only
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


# ---------------------------------------------------------------------------
# Sample upstream artefacts — each carries a UNIQUE marker so we can
# prove the orchestrator threaded that artefact's JSON into the prompt.
# ---------------------------------------------------------------------------

def _sample_product_mapping() -> ProductMapping:
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
            summary=(
                "ANS lab-validates the SD-WAN rollout end-to-end. "
                "Marker: UNIQUE_BENEFITS_MARKER_xyz."
            ),
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
                "knowledge bundle. "
                + (
                    "Marker: UNIQUE_FAQ_MARKER_xyz."
                    if i == 0
                    else "(filler)"
                )
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
                "Worried that procurement won't approve the spend. "
                + (
                    "Marker: UNIQUE_OBJECTIONS_MARKER_xyz."
                    if i == 0
                    else "(filler)"
                )
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


# ---------------------------------------------------------------------------
# Sample critic outputs — one for each verdict so the schema's
# verdict/severity cross-field validator is exercised end-to-end.
# ---------------------------------------------------------------------------

def _sample_report_ready() -> Stage2CriticReport:
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 27),
        verdict=Verdict.READY,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.BENEFITS,
                locator="executive_summary.summary",
                severity=Severity.INFO,
                category=IssueCategory.OTHER,
                description="Minor noise-floor observation; no impact.",
            ),
        ],
        summary="All three artefacts hang together; ready to ship.",
        gaps=[],
    )


def _sample_report_warnings() -> Stage2CriticReport:
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 27),
        verdict=Verdict.READY_WITH_WARNINGS,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.FAQ,
                locator="entries[3].answer",
                severity=Severity.WARNING,
                category=IssueCategory.OFF_BRAND_TONE,
                description="Marketing-speak; tighten to pre-sales register.",
                suggested_fix="Replace 'world-class' with a concrete claim.",
            ),
        ],
        summary=(
            "Artefacts hang together; one tone slip in the FAQ to clean up."
        ),
        gaps=[],
    )


def _sample_report_needs_revision() -> Stage2CriticReport:
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 27),
        verdict=Verdict.NEEDS_REVISION,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.OBJECTIONS,
                locator="rows[7].response",
                severity=Severity.BLOCKING,
                category=IssueCategory.UNSUPPORTED_PRODUCT_CLAIM,
                description=(
                    "Response claims a 100G jitter floor the knowledge "
                    "bundle does not support."
                ),
                suggested_fix=(
                    "Drop the unsupported claim or replace with the "
                    "datasheet-supported figure."
                ),
            ),
        ],
        summary=(
            "One blocker on objections — fabricated product capability — "
            "must be fixed before shipping."
        ),
        gaps=[],
    )


_KNOWLEDGE_BUNDLE = (
    "## products/emulators.md\n\n"
    "Netropy 100G — high-bandwidth network emulator. Marker: "
    "UNIQUE_BUNDLE_MARKER_xyz."
)


def _seed_critic_inputs(
    job_id: str,
    sample_briefing: Briefing,
) -> None:
    """Common setup for critic-orchestration tests: write all five
    upstream artefacts (briefing, mapping, benefits, FAQ, objections)
    and move ``state.json`` to APPROVED."""
    write_briefing(job_id, sample_briefing)
    write_product_mapping(job_id, _sample_product_mapping())
    write_benefits(job_id, _sample_benefits_brief())
    write_faq(job_id, _sample_faq_document())
    write_objections(job_id, _sample_objections_register())
    _seed_to_approved(job_id)


# ---------------------------------------------------------------------------
# Happy paths — one per verdict
# ---------------------------------------------------------------------------

def test_run_critic_happy_path_ready_persists_artefact_and_keeps_state(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Happy path with verdict=READY: agent called once with the
    critic-model id; ``critic_report.json`` round-trips; state stays
    at APPROVED; no transition appended; last_error stays None;
    Job.status and trap_triggers untouched."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    expected = _sample_report_ready()
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
        orch.run_critic(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert isinstance(result, Stage2CriticReport)
    assert result.verdict is Verdict.READY
    assert len(fake_cloud_client.calls) == 1
    # Critic uses the critic_model id, NOT the writer/research id.
    assert fake_cloud_client.calls[0]["model"] == "test-critic-model"

    rebuilt = read_critic_report(seeded_job)
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


def test_run_critic_happy_path_ready_with_warnings_persists_artefact(
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Happy path with verdict=READY_WITH_WARNINGS: artefact still
    persisted; state still APPROVED; last_error still None. The
    verdict is advisory in Step 20 — the orchestrator does not branch."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    expected = _sample_report_warnings()
    fake_cloud_client.script([_resp(expected)])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    result = asyncio.run(
        orch.run_critic(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert result.verdict is Verdict.READY_WITH_WARNINGS
    assert read_critic_report(seeded_job) == expected

    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["current_state"] == JobState.APPROVED.value
    assert payload["last_error"] is None


def test_run_critic_happy_path_needs_revision_persists_artefact_no_branch(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Happy path with verdict=NEEDS_REVISION: the artefact is still
    persisted and the orchestrator does NOT branch — no state edit,
    no transition, no last_error stamp. Step 20 keeps the verdict
    advisory; revision-loop wiring belongs in a later step."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    expected = _sample_report_needs_revision()
    fake_cloud_client.script([_resp(expected)])

    payload_before = _state_payload(isolated_jobs_root, seeded_job)
    transitions_before = list(payload_before["transitions"])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    result = asyncio.run(
        orch.run_critic(
            job_id=seeded_job, knowledge_bundle=_KNOWLEDGE_BUNDLE
        )
    )

    assert result.verdict is Verdict.NEEDS_REVISION
    assert read_critic_report(seeded_job) == expected

    payload_after = _state_payload(isolated_jobs_root, seeded_job)
    assert payload_after["current_state"] == JobState.APPROVED.value
    assert payload_after["transitions"] == transitions_before
    # Critical: a NEEDS_REVISION verdict must NOT stamp last_error —
    # the agent succeeded, even though its judgement was negative.
    assert payload_after["last_error"] is None

    db_session.expire_all()
    job = db_session.execute(
        select(Job).where(Job.id == seeded_job)
    ).scalar_one()
    assert job.trap_triggers in (None, "")


# ---------------------------------------------------------------------------
# Prompt-forwarding — every upstream artefact lands verbatim in the
# prompt body.
# ---------------------------------------------------------------------------

def test_run_critic_forwards_all_five_artefacts_bundle_and_user_context(
    seeded_job: str,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """The critic must thread all five upstream artefacts, the
    knowledge bundle, and the user_context into the prompt body. We
    prove identity for each one via a unique marker."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    fake_cloud_client.script([_resp(_sample_report_ready())])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )
    asyncio.run(
        orch.run_critic(
            job_id=seeded_job,
            knowledge_bundle=_KNOWLEDGE_BUNDLE,
            user_context="lean on the SD-WAN angle",
        )
    )

    body = fake_cloud_client.calls[0]["messages"][0]["content"]
    # Briefing JSON signal — its source URL is unique.
    assert "https://acme.example.com/news/cloud" in body
    # Product mapping marker.
    assert "UNIQUE_MAPPING_MARKER_xyz" in body
    # Benefits marker.
    assert "UNIQUE_BENEFITS_MARKER_xyz" in body
    # FAQ marker.
    assert "UNIQUE_FAQ_MARKER_xyz" in body
    # Objections marker.
    assert "UNIQUE_OBJECTIONS_MARKER_xyz" in body
    # Knowledge bundle marker.
    assert "UNIQUE_BUNDLE_MARKER_xyz" in body
    # User context surfaced verbatim.
    assert "lean on the SD-WAN angle" in body


# ---------------------------------------------------------------------------
# Failure paths — trap / output_invalid / crashed
# ---------------------------------------------------------------------------

def test_run_critic_trap_records_failure_in_place(
    seeded_job: str,
    isolated_jobs_root: Path,
    db_session: Session,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A trap raised inside the critic agent must:

    * re-raise unchanged;
    * stamp ``last_error`` with ``runaway_trap`` + the trap's reason;
    * bump ``Job.trap_triggers`` with the trap class name;
    * leave ``current_state`` at APPROVED;
    * leave no ``critic_report.json`` on disk;
    * leave the five upstream artefacts byte-identical on disk.
    """
    _seed_critic_inputs(seeded_job, sample_briefing)
    folder = isolated_jobs_root / seeded_job
    upstream_before = {
        name: (folder / name).read_text(encoding="utf-8")
        for name in (
            "briefing.json",
            "product_mapping.json",
            "benefits.json",
            "faq.json",
            "objections.json",
        )
    }

    trap = JobBudgetExceeded(
        "would exceed per-job budget",
        job_id=seeded_job,
        agent="critic",
    )
    fake_cloud_client.script([trap])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RunawayTrapFired) as exc_info:
        asyncio.run(
            orch.run_critic(
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

    # No critic report on disk.
    assert not (folder / "critic_report.json").exists()

    # Upstream artefacts byte-identical — a failed critic must not
    # tamper with the writer outputs the operator may yet ship.
    for name, before in upstream_before.items():
        assert (folder / name).read_text(encoding="utf-8") == before


def test_run_critic_output_invalid_records_failure_no_trap_bump(
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
    _seed_critic_inputs(seeded_job, sample_briefing)
    bad = make_response(text="not json at all")
    fake_cloud_client.script([bad, bad])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(AgentOutputInvalid):
        asyncio.run(
            orch.run_critic(
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
    assert not (folder / "critic_report.json").exists()


def test_run_critic_unexpected_exception_records_crashed(
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
    _seed_critic_inputs(seeded_job, sample_briefing)
    boom = RuntimeError("network exploded")
    fake_cloud_client.script([boom])

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            orch.run_critic(
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
# Pre-flight rejection (no last_error written)
# ---------------------------------------------------------------------------

def test_run_critic_rejects_job_not_at_approved(
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
            orch.run_critic(
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
    "missing_filename",
    [
        "briefing.json",
        "product_mapping.json",
        "benefits.json",
        "faq.json",
        "objections.json",
    ],
)
def test_run_critic_raises_job_not_found_when_any_upstream_missing(
    missing_filename: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """Each of the five upstream artefacts is load-bearing. If any one
    is missing, the critic must surface :class:`JobNotFound`
    unchanged and not stamp ``last_error`` — caller mistake."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    # Delete the one upstream artefact under test.
    target = isolated_jobs_root / seeded_job / missing_filename
    target.unlink()

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(JobNotFound):
        asyncio.run(
            orch.run_critic(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None


@pytest.mark.parametrize(
    "corrupt_filename",
    [
        "briefing.json",
        "product_mapping.json",
        "benefits.json",
        "faq.json",
        "objections.json",
    ],
)
def test_run_critic_raises_validation_error_on_corrupt_upstream(
    corrupt_filename: str,
    seeded_job: str,
    isolated_jobs_root: Path,
    session_factory,
    fake_cloud_client: FakeCloudClient,
    test_models: Models,
    sample_briefing: Briefing,
) -> None:
    """A schema-invalid upstream artefact surfaces
    :class:`ValidationError`; the orchestrator does not catch it and
    does not stamp ``last_error``."""
    _seed_critic_inputs(seeded_job, sample_briefing)
    target = isolated_jobs_root / seeded_job / corrupt_filename
    target.write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )

    orch = _make_orchestrator(
        client=fake_cloud_client,
        models=test_models,
        session_factory=session_factory,
    )

    with pytest.raises(ValidationError):
        asyncio.run(
            orch.run_critic(
                job_id=seeded_job,
                knowledge_bundle=_KNOWLEDGE_BUNDLE,
            )
        )

    assert fake_cloud_client.calls == []
    payload = _state_payload(isolated_jobs_root, seeded_job)
    assert payload["last_error"] is None
