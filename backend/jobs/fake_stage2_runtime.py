"""Opt-in dev-only fake runtime for the Stage 2 run route (Step 25).

Scope (step 25)
---------------
Step 23 shipped ``POST /api/jobs/{job_id}/stage2/run`` fake-safe: the
three FastAPI ``Depends`` providers
(:func:`backend.jobs.stage2_routes.get_cloud_client`,
:func:`~backend.jobs.stage2_routes.get_models`,
:func:`~backend.jobs.stage2_routes.get_db_session_factory`) raise
HTTP 503 by default so production deployments that have not
deliberately bound a real client cannot reach the orchestrator.

This module ships an **explicitly opt-in, dev-only** runtime that
binds the three deps to schema-valid fakes when — and only when —
the environment variable ``ANS_ENABLE_FAKE_STAGE2_RUNTIME`` is set
to the exact string ``"1"``. Any other value (unset, empty,
``"0"``, ``"true"``, ``"yes"``, …) leaves the runtime disabled and
the route continues to return 503.

Single source of truth: each canned response is constructed from
the canonical Pydantic schema model and dumped to JSON with
``.model_dump_json()``. Schema drift will fail at import time
(``ProductMapping(...)`` etc. will raise ``ValidationError``),
not at request time — a loud, immediate signal.

This module deliberately:

* does **not** import the Anthropic SDK,
* does **not** import :mod:`keyring` or :mod:`backend.credentials`,
* does **not** import :mod:`backend.tools.cloud_client`,
* does **not** import :mod:`backend.delivery` or
  :mod:`backend.assembly`,
* does **not** import any module from ``tests/``,
* does **not** add document generation or M365 delivery,
* does **not** add new job states or change state transitions.

A separate static-fence test
(:mod:`tests.jobs_routes.test_fake_stage2_runtime`) enforces this
with AST analysis plus a substring scan.

NOT FOR PRODUCTION USE. Enabling the env var will log a startup
warning identifying the install hook so accidental production
exposure is immediately visible.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from fastapi import FastAPI
from sqlalchemy.orm import Session as DbSession

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
)
from backend.agents.critic_models import Stage2CriticReport, Verdict
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
from backend.cost_control.config_loader import Models


# ---------------------------------------------------------------------------
# Locally-defined CloudCallResult-shaped return value
# ---------------------------------------------------------------------------
#
# The jobs-layer static fence
# (:mod:`tests.jobs.test_no_keyring_or_credentials_imports`) bans any
# import from :mod:`backend.cost_control.cloud_client` in any file
# under ``backend/jobs/``. The orchestrator only ever reads four
# attributes off the value returned by
# :meth:`CloudClientProtocol.messages_create` — ``response``,
# ``input_tokens``, ``output_tokens``, ``cost_usd`` — so a
# structurally-compatible local dataclass is enough. Schema drift on
# :class:`backend.cost_control.cloud_client.CloudCallResult` would
# surface in agent-layer tests, not here.

@dataclass(frozen=True)
class _FakeCallResult:
    response: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env-var predicate
# ---------------------------------------------------------------------------

#: The exact environment-variable name. Verbose on purpose so a typo
#: defaults to "off" rather than to "on".
_ENV_VAR = "ANS_ENABLE_FAKE_STAGE2_RUNTIME"


def fake_stage2_runtime_enabled() -> bool:
    """Return ``True`` iff ``ANS_ENABLE_FAKE_STAGE2_RUNTIME`` == ``"1"``.

    Strict string equality — no truthy-string parsing. Any other value
    (unset, empty, ``"0"``, ``"true"``, ``"yes"``, …) returns
    ``False``. This is a deliberate foot-gun guard: a typo or a
    legacy ``"true"`` from another tool's convention silently
    disables the fake, which is the safe direction.
    """
    return os.environ.get(_ENV_VAR) == "1"


# ---------------------------------------------------------------------------
# Canned Pydantic models — schema-valid by construction
# ---------------------------------------------------------------------------
#
# Every canned response is built by instantiating the canonical schema
# model and serialising it with ``.model_dump_json()``. If a future
# step changes any schema in a way that invalidates these literals,
# this module will fail to import — and the static-fence test plus
# the env-on happy-path test will both fail immediately. That is the
# intended fail-loud behaviour for schema drift.

_COMPANY_NAME = "Fake Stage 2 Co"
_COMPANY_URL = "https://fake.example.com/"
_TODAY = date(2026, 1, 1)


def _canned_product_mapping() -> ProductMapping:
    return ProductMapping(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        mapped_at=_TODAY,
        matches=[
            NeedProductMatch(
                need_priority=1,
                need_summary="Validate planned network refresh",
                products=[
                    ProductReference(
                        product_line=ProductLine.EMULATORS,
                        product_name="Netropy 100G",
                        knowledge_excerpt_refs=[0],
                    )
                ],
                use_case_framing=(
                    "Emulate the topology under realistic impairment "
                    "before cutover."
                ),
                why_this_fits=(
                    "Briefing flags an in-flight network programme."
                ),
                confidence=Confidence.HIGH,
            )
        ],
        unmatched_needs=[
            UnmatchedNeed(
                need_priority=2,
                need_summary="Refresh SIEM tooling",
                reason="Outside the ANS lab/network testing portfolio.",
            )
        ],
        knowledge_excerpts=[
            KnowledgeExcerptRef(
                source_file=KnowledgeSource.PRODUCTS_EMULATORS,
                heading="Netropy Network Emulators",
                rationale="Bandwidth and impairment range.",
            ),
        ],
        gaps=[],
    )


def _benefit_claim() -> BenefitClaim:
    return BenefitClaim(
        claim="Netropy reproduces realistic network conditions.",
        detail=(
            "Line-rate impairment up to 100G matches the rollout's "
            "aggregate bandwidth without truncation."
        ),
        why_it_matters=(
            "Surfaces SLA-breaching jitter before cutover rather "
            "than after."
        ),
        knowledge_excerpt_refs=[0],
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )


def _canned_benefits_brief() -> BenefitsBrief:
    return BenefitsBrief(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=_TODAY,
        executive_summary=BenefitsSection(
            heading="Executive summary",
            audience=Audience.CTO_CIO,
            summary="ANS lab-validates the rollout end-to-end.",
            body=[_benefit_claim()],
        ),
        technical_fit=BenefitsSection(
            heading="Technical fit",
            audience=Audience.ENGINEERS,
            summary="Netropy lines up with the planned topology.",
            body=[_benefit_claim()],
        ),
        business_case=BenefitsSection(
            heading="Business case",
            audience=Audience.IT_DIRECTOR,
            summary="Catches cutover risk before it hits revenue.",
            body=[_benefit_claim()],
        ),
        gaps=[],
    )


def _canned_faq_document() -> FAQDocument:
    categories = [
        FAQCategory.ABOUT_ANS,
        FAQCategory.PRODUCTS,
        FAQCategory.IMPLEMENTATION,
        FAQCategory.COMMERCIAL,
        FAQCategory.SUPPORT,
    ]
    return FAQDocument(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=_TODAY,
        entries=[
            FAQEntry(
                question=f"Fake question #{i}?",
                answer=(
                    "Fake answer grounded in the briefing and the "
                    "knowledge bundle."
                ),
                category=categories[i % len(categories)],
                knowledge_excerpt_refs=[0],
                briefing_source_refs=[0],
                confidence=Confidence.HIGH,
            )
            for i in range(12)
        ],
        gaps=[],
    )


def _canned_objections_register() -> ObjectionsRegister:
    return ObjectionsRegister(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=_TODAY,
        rows=[
            ObjectionRow(
                category=ObjectionCategory.PRICE,
                objection=f"Fake objection #{i}.",
                underlying_concern=(
                    "Procurement may push back on the spend."
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


def _canned_critic_report() -> Stage2CriticReport:
    """READY verdict with zero issues — simplest valid critic output.

    A READY verdict with an empty ``issues`` list passes the cross-
    field validator on :class:`Stage2CriticReport`.
    """
    return Stage2CriticReport(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        reviewed_at=_TODAY,
        verdict=Verdict.READY,
        issues=[],
        summary="Fake artefacts cohere; no issues to flag.",
        gaps=[],
    )


#: Map from the ``agent`` kwarg passed by
#: :class:`backend.agents.base.BaseAgent` into
#: ``CloudClientProtocol.messages_create`` to the canned-model builder
#: for that agent. Dispatch by stable agent name — not a call counter —
#: so a future reordering of the orchestrator's stages does not break
#: the fake.
_AGENT_DISPATCH: dict[str, Callable[[], Any]] = {
    "mapping": _canned_product_mapping,
    "benefits_writer": _canned_benefits_brief,
    "faq_writer": _canned_faq_document,
    "objections_writer": _canned_objections_register,
    "critic": _canned_critic_report,
}


# ---------------------------------------------------------------------------
# FakeStage2CloudClient
# ---------------------------------------------------------------------------

class FakeStage2CloudClient:
    """Structural match for :class:`backend.agents._protocols.CloudClientProtocol`.

    Implements the single :meth:`messages_create` method the base
    agent ever calls. Dispatches on the stable ``agent`` kwarg
    (``mapping`` / ``benefits_writer`` / ``faq_writer`` /
    ``objections_writer`` / ``critic``) and returns a
    :class:`CloudCallResult` whose response carries one ``text`` block
    containing the canonical schema model serialised to JSON.

    Re-entrancy: the fake holds no state between calls, so a single
    instance per request — or a single instance per process — is
    equally safe.
    """

    def messages_create(
        self,
        *,
        agent: str,
        model: str,
        job_id: str | None,
        messages: list[dict[str, Any]],
        max_tokens: int,
        input_tokens_estimate: int,
        tools: list[dict[str, Any]] | None = None,
        tool_use_counts: dict[str, int] | None = None,
        approved_by: str = "user",
        extra_sdk_kwargs: dict[str, Any] | None = None,
    ) -> _FakeCallResult:
        builder = _AGENT_DISPATCH.get(agent)
        if builder is None:
            raise RuntimeError(
                f"FakeStage2CloudClient has no canned response for "
                f"agent {agent!r}. Step 25 covers only the five "
                f"Stage 2 agents (mapping, benefits_writer, "
                f"faq_writer, objections_writer, critic)."
            )
        text = builder().model_dump_json()
        response = {
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        }
        return _FakeCallResult(
            response=response,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0,
        )


# ---------------------------------------------------------------------------
# Install hook
# ---------------------------------------------------------------------------

#: Stable warning string. Tested verbatim by
#: :func:`tests.jobs_routes.test_fake_stage2_runtime` so an accidental
#: production exposure is loudly visible in startup logs.
STARTUP_WARNING = (
    "ANS_ENABLE_FAKE_STAGE2_RUNTIME=1 — fake Stage 2 runtime enabled; "
    "do not use in production."
)


def install_fake_stage2_runtime(
    app: FastAPI,
    db_session_factory: Callable[[], DbSession],
) -> None:
    """Wire fake Stage 2 deps into ``app.dependency_overrides``.

    Overrides three entry points exposed by
    :mod:`backend.jobs.stage2_routes`:

    * ``get_cloud_client``       → returns a fresh ``FakeStage2CloudClient``,
    * ``get_models``             → returns a stable :class:`Models` triple
                                   (the values are never consulted by the
                                   fake but the orchestrator passes them
                                   through to the agents),
    * ``get_db_session_factory`` → returns the caller-supplied factory.

    Idempotent: re-installing replaces the existing overrides in
    place. FastAPI's ``dependency_overrides`` is a dict, so writing
    the same three keys twice is a no-op in effect.

    Logs :data:`STARTUP_WARNING` so accidental production exposure
    is immediately visible.
    """
    # Local import to keep :mod:`backend.jobs.stage2_routes` out of
    # this module's top-level surface. Tests for the static fence
    # then need only forbid the cloud-client / keychain / SDK
    # prefixes, not the route module itself.
    from backend.jobs.stage2_routes import (
        get_cloud_client,
        get_db_session_factory,
        get_models,
    )

    def _models() -> Models:
        return Models(
            writer_model="fake-writer",
            research_model="fake-research",
            critic_model="fake-critic",
        )

    app.dependency_overrides[get_cloud_client] = FakeStage2CloudClient
    app.dependency_overrides[get_models] = _models
    app.dependency_overrides[get_db_session_factory] = (
        lambda: db_session_factory
    )

    log.warning(STARTUP_WARNING)
