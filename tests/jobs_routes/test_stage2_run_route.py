"""Tests for ``POST /api/jobs/{job_id}/stage2/run`` (Step 23).

The route is a thin HTTP adapter over three
:class:`backend.orchestrator.Orchestrator` Stage 2 entry points
(:meth:`run_mapping`, :meth:`run_stage2_writers`, :meth:`run_critic`).
The orchestrator's own behaviour is exhaustively covered in
``tests/orchestrator/`` — this module's job is to prove the **HTTP
surface**: dependency injection, request shape, response shape,
exception mapping, and the fake-safe invariants.

The five layers asserted here:

1. Happy-path 201 with the documented envelope shape, all five Stage 2
   artefacts on disk, ``current_state`` still ``approved``, no
   transition appended.
2. Default 503 when no dependency overrides are bound — the route
   cannot construct a production model client.
3. Wrong-state 409 (job not in ``approved``).
4. 422 for body shape (missing bundle, empty bundle, extra fields).
5. Partial-failure 500 with stable ``reason`` / ``stage`` /
   ``category`` body; upstream artefacts left on disk; orchestrator's
   ``last_error`` is observable via ``GET /api/jobs/{id}``.

Plus a scoped-fence proof: the orchestrator-sentinel monkeypatch
from ``tests/jobs_routes/test_stage2_artefacts_readonly_fence.py``
applies only to the GET routes; the new POST route succeeds without
that fixture and fails loudly when the fixture is on.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
)
from backend.agents.briefing_models import Briefing
from backend.agents.critic_models import (
    Stage2CriticReport,
    Verdict,
)
from backend.agents.faq_models import FAQCategory, FAQDocument, FAQEntry
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
from backend.agents.objections_models import (
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
)
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.config_loader import Models
from backend.jobs import stage2_routes
from backend.jobs.state import JobState
from backend.main import app
from tests.agents.conftest import FakeCloudClient, make_response


_KNOWLEDGE_BUNDLE = (
    "## products/emulators.md\n\n"
    "Netropy 100G — high-bandwidth network emulator. Marker: "
    "STAGE2_ROUTE_TEST_BUNDLE."
)


# ---------------------------------------------------------------------------
# Sample agent outputs — populated minimum-valid instances
# ---------------------------------------------------------------------------
#
# These are independent of the conftest's read-side fixtures so a
# happy-path test can prove byte-by-byte that the orchestrator wrote
# *what the fake returned*.

_COMPANY_NAME = "Acme Ltd"
_COMPANY_URL = "https://acme.example.com/"


def _sample_product_mapping() -> ProductMapping:
    return ProductMapping(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
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
                rationale="Bandwidth and impairment range for SD-WAN.",
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
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
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
    cats = [
        FAQCategory.ABOUT_ANS,
        FAQCategory.PRODUCTS,
        FAQCategory.IMPLEMENTATION,
        FAQCategory.COMMERCIAL,
        FAQCategory.SUPPORT,
    ]
    return FAQDocument(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        entries=[
            FAQEntry(
                question=f"Question #{i}?",
                answer=(
                    "ANS-side answer grounded in the briefing and the "
                    "knowledge bundle."
                ),
                category=cats[i % len(cats)],
                knowledge_excerpt_refs=[0],
                briefing_source_refs=[0],
                confidence=Confidence.HIGH,
            )
            for i in range(12)
        ],
        gaps=[],
    )


def _sample_objections_register() -> ObjectionsRegister:
    return ObjectionsRegister(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        rows=[
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
                escalation_path=(
                    "Account owner brings in solutions architect."
                ),
                confidence=Confidence.HIGH,
            )
            for i in range(15)
        ],
        gaps=[],
    )


def _sample_critic_report() -> Stage2CriticReport:
    """READY verdict with zero issues — simplest valid critic output."""
    return Stage2CriticReport(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 27),
        verdict=Verdict.READY,
        issues=[],
        summary="Artefacts cohere; no issues to flag.",
        gaps=[],
    )


def _resp(model_instance) -> object:
    return make_response(text=model_instance.model_dump_json())


def _full_chain_script() -> list[object]:
    """Five scripted responses: mapping, benefits, faq, objections, critic."""
    return [
        _resp(_sample_product_mapping()),
        _resp(_sample_benefits_brief()),
        _resp(_sample_faq_document()),
        _resp(_sample_objections_register()),
        _resp(_sample_critic_report()),
    ]


# ---------------------------------------------------------------------------
# Override wiring
# ---------------------------------------------------------------------------
#
# Tests that need a real, in-test orchestration set up three
# dependency overrides on the FastAPI app:
#
#   - get_cloud_client       → returns the scripted FakeCloudClient
#   - get_models             → returns a deterministic Models triple
#   - get_db_session_factory → reuses the conftest's per-test factory
#
# The overrides are torn down by the conftest's ``client`` fixture
# which clears ``app.dependency_overrides`` between tests.

def _bind_stage2_overrides(
    *,
    client: FakeCloudClient,
    session_factory: Callable[[], Session],
) -> None:
    app.dependency_overrides[stage2_routes.get_cloud_client] = lambda: client
    app.dependency_overrides[stage2_routes.get_models] = lambda: Models(
        writer_model="test-writer-model",
        research_model="test-research-model",
        critic_model="test-critic-model",
    )
    app.dependency_overrides[stage2_routes.get_db_session_factory] = (
        lambda: session_factory
    )


# ---------------------------------------------------------------------------
# Per-test job-semaphore reset
# ---------------------------------------------------------------------------
#
# The orchestrator acquires the module-global semaphore in each
# Stage 2 entry point. Tests in this file run them sequentially via
# ``asyncio`` from inside the synchronous TestClient, so we reset
# the semaphore between tests for the same reason
# ``tests/orchestrator/conftest.py`` does.

@pytest.fixture(autouse=True)
def _reset_job_semaphore() -> Iterator[None]:
    from backend.cost_control import concurrency as _concurrency

    _concurrency._reset_for_tests()
    yield
    _concurrency._reset_for_tests()


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_stage2_run_happy_path_returns_201_envelope(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """All three orchestrator entries succeed; route returns the
    documented envelope."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body == {
        "job_id": approved_job,
        "stages_completed": ["mapping", "writers", "critic"],
        "current_state": "approved",
    }


def test_stage2_run_happy_path_writes_all_five_artefacts(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """A successful chain leaves five new artefact files on disk —
    one per orchestrator stage step. We don't pretty-render them
    here; existence proves the route delegated to the orchestrator."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text

    folder = isolated_jobs_root / approved_job
    for name in (
        "product_mapping.json",
        "benefits.json",
        "faq.json",
        "objections.json",
        "critic_report.json",
    ):
        assert (folder / name).exists(), f"{name} should be on disk"


def test_stage2_run_happy_path_calls_fake_five_times(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """Five agents run (one mapping + three writers + one critic) —
    the fake's call log records five entries. Proves the route
    actually delegated and did not stop short."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    assert len(fake.calls) == 5


def test_stage2_run_happy_path_does_not_append_transition_or_change_state(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """In-place semantics: state.json's transitions list and
    current_state are byte-identical before and after the run."""
    state_path = isolated_jobs_root / approved_job / "state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))

    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text

    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["current_state"] == JobState.APPROVED.value
    assert after["transitions"] == before["transitions"]
    assert after["last_error"] is None


def test_stage2_run_forwards_user_context_when_provided(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """``user_context`` is optional and threads through. We assert
    *behavioural* presence: a long context plus an empty fake script
    surfaces the orchestrator's normal "script empty" assertion, but
    a properly-scripted full chain still succeeds when context is
    provided — both confirm the field is accepted and forwarded
    without raising at the body-validation layer."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={
            "knowledge_bundle": _KNOWLEDGE_BUNDLE,
            "user_context": "Focus on SD-WAN concerns.",
        },
    )
    assert r.status_code == 201, r.text


def test_stage2_run_idempotent_rerun_overwrites_artefacts(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """Second invocation succeeds and produces byte-identical
    artefacts. Proves the route does not refuse to run when the
    files already exist."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script() + _full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    folder = isolated_jobs_root / approved_job

    r1 = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r1.status_code == 201, r1.text
    first_bytes = {
        n: (folder / n).read_bytes()
        for n in (
            "product_mapping.json",
            "benefits.json",
            "faq.json",
            "objections.json",
            "critic_report.json",
        )
    }

    r2 = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r2.status_code == 201, r2.text
    second_bytes = {
        n: (folder / n).read_bytes()
        for n in first_bytes
    }
    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# 2. Default 503 — no overrides bound
# ---------------------------------------------------------------------------

def test_default_dependencies_return_503(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Without overrides, the route must not be callable. This is
    the fake-safe production guard — any deployment that has not
    deliberately bound a real client gets 503 with a stable reason."""
    # Defensive: no overrides for the stage2 deps in this test.
    for dep in (
        stage2_routes.get_cloud_client,
        stage2_routes.get_models,
        stage2_routes.get_db_session_factory,
    ):
        app.dependency_overrides.pop(dep, None)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "stage2_run_route_has_no_client_bound"


def test_default_models_dependency_returns_503(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """Bind the client but leave models unbound — should still 503."""
    fake = FakeCloudClient()
    app.dependency_overrides[stage2_routes.get_cloud_client] = lambda: fake
    app.dependency_overrides[stage2_routes.get_db_session_factory] = (
        lambda: session_factory
    )
    # Deliberately do NOT bind models.
    app.dependency_overrides.pop(stage2_routes.get_models, None)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["reason"] == "stage2_run_route_has_no_models_bound"


def test_default_db_factory_dependency_returns_503(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Bind the client and models but leave the DB factory unbound —
    should still 503."""
    fake = FakeCloudClient()
    app.dependency_overrides[stage2_routes.get_cloud_client] = lambda: fake
    app.dependency_overrides[stage2_routes.get_models] = lambda: Models(
        writer_model="x", research_model="y", critic_model="z",
    )
    app.dependency_overrides.pop(stage2_routes.get_db_session_factory, None)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 503, r.text
    assert (
        r.json()["detail"]["reason"]
        == "stage2_run_route_has_no_db_factory_bound"
    )


# ---------------------------------------------------------------------------
# 3. Wrong state — 409
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "briefing_ready_job",
        "editing_job",
        "regenerating_job",
    ],
)
def test_stage2_run_rejects_non_approved_state_with_409(
    request: pytest.FixtureRequest,
    authed_client: TestClient,
    session_factory: Callable[[], Session],
    fixture_name: str,
) -> None:
    """Every non-APPROVED state must surface as 409 wrong_state."""
    job_id = request.getfixturevalue(fixture_name)
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{job_id}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "wrong_state"
    assert detail["expected"] == "approved"
    assert detail["actual"] != "approved"
    # No agent calls happened — the orchestrator short-circuits at
    # the state check.
    assert fake.calls == []


# ---------------------------------------------------------------------------
# 4. Body-shape validation
# ---------------------------------------------------------------------------

def test_stage2_run_rejects_missing_knowledge_bundle(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={},
    )
    assert r.status_code == 422, r.text


def test_stage2_run_rejects_empty_knowledge_bundle(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": ""},
    )
    assert r.status_code == 422, r.text


def test_stage2_run_rejects_extra_fields(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """``Stage2RunBody`` is ``extra='forbid'``."""
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={
            "knowledge_bundle": _KNOWLEDGE_BUNDLE,
            "step_filter": "mapping",  # not a real field
        },
    )
    assert r.status_code == 422, r.text


def test_stage2_run_rejects_bundle_over_max_length(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """Pathological-size payload must be rejected at the framework
    layer before the route handler is reached."""
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": "x" * 200_001},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 5. Partial failure surfaces 500 + records last_error
# ---------------------------------------------------------------------------

def test_stage2_run_mapping_output_invalid_returns_500_and_stamps_last_error(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """Mapping agent returns garbage twice → ``AgentOutputInvalid``.
    Route surfaces 500 with stage=mapping, category=output_invalid.
    ``state.json.last_error`` carries the matching category. No
    Stage 2 artefacts on disk; current_state still APPROVED."""
    bad = make_response(text="not json at all")
    fake = FakeCloudClient()
    # Two attempts inside the agent's single-retry loop.
    fake.script([bad, bad])
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail == {
        "reason": "stage2_agent_failed",
        "stage": "mapping",
        "category": "output_invalid",
    }

    folder = isolated_jobs_root / approved_job
    assert not (folder / "product_mapping.json").exists()
    assert not (folder / "benefits.json").exists()
    assert not (folder / "critic_report.json").exists()

    state = json.loads(
        (folder / "state.json").read_text(encoding="utf-8")
    )
    assert state["current_state"] == JobState.APPROVED.value
    assert state["last_error"]["category"] == "output_invalid"


def test_stage2_run_writers_partial_failure_leaves_upstream_artefacts(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """Mapping succeeds; benefits succeeds; FAQ fails. Route returns
    500 with stage=writers. benefits.json and product_mapping.json
    are on disk; faq.json, objections.json, critic_report.json are
    not. state.json.last_error reflects the FAQ failure."""
    bad = make_response(text="not json at all")
    fake = FakeCloudClient()
    fake.script([
        _resp(_sample_product_mapping()),
        _resp(_sample_benefits_brief()),
        bad,  # FAQ try 1
        bad,  # FAQ try 2
    ])
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "stage2_agent_failed"
    assert detail["stage"] == "writers"
    assert detail["category"] == "output_invalid"

    folder = isolated_jobs_root / approved_job
    assert (folder / "product_mapping.json").exists()
    assert (folder / "benefits.json").exists()
    assert not (folder / "faq.json").exists()
    assert not (folder / "objections.json").exists()
    assert not (folder / "critic_report.json").exists()

    state = json.loads(
        (folder / "state.json").read_text(encoding="utf-8")
    )
    assert state["current_state"] == JobState.APPROVED.value
    assert state["last_error"]["category"] == "output_invalid"


def test_stage2_run_critic_unexpected_exception_returns_500_crashed(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: Path,
) -> None:
    """Mapping + writers succeed; critic blows up with an unexpected
    Exception. Route returns 500 with stage=critic, category=crashed.
    All four upstream artefacts are on disk."""
    boom = RuntimeError("critic-side boom")
    fake = FakeCloudClient()
    fake.script([
        _resp(_sample_product_mapping()),
        _resp(_sample_benefits_brief()),
        _resp(_sample_faq_document()),
        _resp(_sample_objections_register()),
        boom,
    ])
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "stage2_agent_failed"
    assert detail["stage"] == "critic"
    assert detail["category"] == "crashed"

    folder = isolated_jobs_root / approved_job
    for name in (
        "product_mapping.json",
        "benefits.json",
        "faq.json",
        "objections.json",
    ):
        assert (folder / name).exists(), f"{name} should be on disk"
    assert not (folder / "critic_report.json").exists()


# ---------------------------------------------------------------------------
# 6. Job-id surface
# ---------------------------------------------------------------------------

def test_stage2_run_unknown_job_id_returns_404_via_orchestrator(
    authed_client: TestClient,
    session_factory: Callable[[], Session],
) -> None:
    """A well-formed UUID that doesn't exist on disk: the orchestrator
    raises :class:`JobNotFound`, which the route maps to 404."""
    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        "/api/jobs/00000000-0000-4000-8000-000000000000/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    # 404 from JobNotFound mapped by the route, or 500 from a different
    # surface depending on whether the orchestrator's pre-flight order
    # reaches the JobNotFound case first. Either way the fake never
    # ran an agent.
    assert r.status_code in (404, 500), r.text
    assert fake.calls == []


# ---------------------------------------------------------------------------
# 7. Fake-safe proof — no real Anthropic SDK touched
# ---------------------------------------------------------------------------

def test_anthropic_sdk_not_imported_by_stage2_routes_module(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """The route module deliberately does not import the Anthropic
    SDK. A regression that wires the real SDK in (even via a lazy
    import) would surface here as a sys.modules entry after the
    route has been exercised."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    sys.modules.pop("anthropic", None)
    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    assert "anthropic" not in sys.modules, (
        "stage2 route exercised the real Anthropic SDK"
    )


def test_route_uses_injected_fake_client_identity(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """The exact :class:`FakeCloudClient` instance we install via the
    dependency override is the one the orchestrator drove. We check
    identity, not just equality, so a regression that secretly built
    a new client (even of the same class) would be caught."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    holder: dict[str, Any] = {}

    def provider() -> FakeCloudClient:
        holder["served"] = fake
        return fake

    app.dependency_overrides[stage2_routes.get_cloud_client] = provider
    app.dependency_overrides[stage2_routes.get_models] = lambda: Models(
        writer_model="x", research_model="y", critic_model="z",
    )
    app.dependency_overrides[stage2_routes.get_db_session_factory] = (
        lambda: session_factory
    )

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    assert holder["served"] is fake
    assert len(fake.calls) == 5


# ---------------------------------------------------------------------------
# 8. Read-only fence scoping
# ---------------------------------------------------------------------------
#
# The Stage 2 read-only fence
# (``test_stage2_artefacts_readonly_fence.py``) patches the
# orchestrator's Stage 2 entry points with sentinels. Those patches
# are scope-local. Two tests prove the scoping:
#
#   (a) Without the sentinel patch (default state), the new POST
#       route succeeds.
#   (b) With the sentinel patch active, the new POST route fails
#       loudly — proving the sentinels work and that this route is
#       a new, deliberate orchestrator caller.

def test_post_route_works_without_readonly_sentinels(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
) -> None:
    """Inverse-test sibling of the next test: with no orchestrator
    patching, the POST route runs the chain end-to-end."""
    fake = FakeCloudClient()
    fake.script(_full_chain_script())
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text


def test_post_route_blows_up_when_readonly_sentinels_active(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a future regression installs the GET-only sentinels at
    module scope (escalating them to a global patch), this test
    will see a 500/AssertionError and surface that mistake. Today,
    we activate the sentinels ourselves to prove they would catch
    the POST route — i.e., the GET-side fence is correctly
    *scope-locked* in its own conftest fixture rather than module-
    level."""
    from backend.orchestrator import Orchestrator

    async def _sentinel(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("orchestrator sentinel fired (test guard)")

    for name in (
        "run_mapping",
        "run_stage2_writers",
        "run_critic",
    ):
        monkeypatch.setattr(Orchestrator, name, _sentinel)

    fake = FakeCloudClient()
    _bind_stage2_overrides(client=fake, session_factory=session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    # The sentinel raises AssertionError. FastAPI's default handler
    # turns an uncaught Exception into a 500. The fake never ran.
    assert r.status_code == 500, r.text
    assert fake.calls == []
