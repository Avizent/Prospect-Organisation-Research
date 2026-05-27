"""Tests for the Stage 2 booleans on ``GET /api/jobs/{job_id}``.

Step 21 extends the :class:`AvailableArtefacts` response model with
five new booleans — ``product_mapping``, ``benefits``, ``faq``,
``objections``, ``critic_report`` — alongside the four existing
Stage 1 booleans. Each new flag is populated by a cheap
``Path.exists()`` check against the matching on-disk filename.

These tests pin three contracts:

1. The shape — all nine booleans appear in the response, never four.
2. The default — every new boolean is ``False`` for a job that has
   not run Stage 2 yet.
3. The flip — writing the on-disk file flips its matching boolean
   from ``False`` to ``True``, without affecting any other field.

Why pin the flip per-artefact
-----------------------------
The frontend inspector
(``frontend/js/screens/job_status.js``) iterates the booleans dict
and renders one "open JSON" link per ``True`` key. A bug that hung
the wrong filename off the wrong boolean (``benefits`` flipping when
``faq.json`` is written) would render dead links and confuse
operators — the bug class this test pre-empts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agents.benefits_models import BenefitsBrief
from backend.agents.critic_models import Stage2CriticReport
from backend.agents.faq_models import FAQDocument
from backend.agents.mapping_models import ProductMapping
from backend.agents.objections_models import ObjectionsRegister
from backend.jobs.storage import (
    write_benefits,
    write_critic_report,
    write_faq,
    write_objections,
    write_product_mapping,
)


# Every boolean the response is expected to expose, in alphabetical
# order for stable comparisons.
_EXPECTED_BOOLEAN_KEYS = {
    "benefits",
    "briefing",
    "contacts",
    "critic_report",
    "faq",
    "needs_assessment",
    "objections",
    "product_mapping",
    "research_dossier",
}


def test_response_shape_lists_all_nine_booleans(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job exposes every boolean, all ``False``."""
    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert set(arts.keys()) == _EXPECTED_BOOLEAN_KEYS
    # Stage 2 fields are all False for a fresh job.
    assert arts["product_mapping"] is False
    assert arts["benefits"] is False
    assert arts["faq"] is False
    assert arts["objections"] is False
    assert arts["critic_report"] is False


def test_all_nine_true_when_every_artefact_present(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
) -> None:
    """A job with every Stage 1 + Stage 2 artefact on disk reports
    every boolean ``True`` — the maximal happy-path."""
    r = authed_client.get(f"/api/jobs/{job_with_all_stage2_artefacts}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert arts == {key: True for key in _EXPECTED_BOOLEAN_KEYS}


# Parametrise per-Stage-2-artefact: writing exactly one Stage 2 file
# must flip exactly one boolean. Each row pairs a writer-callable
# fixture name with the boolean it should flip.
_STAGE2_WRITERS = [
    ("sample_product_mapping", write_product_mapping, "product_mapping"),
    ("sample_benefits", write_benefits, "benefits"),
    ("sample_faq", write_faq, "faq"),
    ("sample_objections", write_objections, "objections"),
    ("sample_critic_report", write_critic_report, "critic_report"),
]


@pytest.mark.parametrize(
    "fixture_name, writer, boolean_key", _STAGE2_WRITERS,
)
def test_individual_stage2_artefact_flips_only_its_own_boolean(
    authed_client: TestClient,
    created_job: str,
    request: pytest.FixtureRequest,
    fixture_name: str,
    writer,
    boolean_key: str,
) -> None:
    """Writing one Stage 2 artefact flips its boolean True; every
    other Stage 2 boolean stays False.

    Catches a wiring bug where ``benefits=...`` was hung off the
    wrong filename check — the route would compile but the inspector
    would render dead links."""
    payload = request.getfixturevalue(fixture_name)
    writer(created_job, payload)

    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]

    # The target boolean flipped.
    assert arts[boolean_key] is True, (
        f"writing {fixture_name} should flip {boolean_key} to True"
    )
    # Every other Stage 2 boolean is still False.
    other_stage2 = {
        "product_mapping", "benefits", "faq", "objections", "critic_report",
    } - {boolean_key}
    for other in other_stage2:
        assert arts[other] is False, (
            f"writing {fixture_name} should not flip {other} "
            f"(found {arts[other]})"
        )


def test_extra_unexpected_field_rejected_by_response_model(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The ``AvailableArtefacts`` model uses ``extra='forbid'``; a
    future change that accidentally adds a tenth field would be
    caught at response-model validation rather than silently
    appearing on the wire. We assert the negative by checking the
    shape contains exactly the nine expected keys — no more, no less."""
    r = authed_client.get(f"/api/jobs/{created_job}")
    arts = r.json()["available_artefacts"]
    assert len(arts) == 9
    assert set(arts.keys()) == _EXPECTED_BOOLEAN_KEYS
