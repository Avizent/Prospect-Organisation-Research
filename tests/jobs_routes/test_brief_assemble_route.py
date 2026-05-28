"""Tests for ``POST /api/jobs/{job_id}/brief/assemble`` — Step 32.

The route is a thin HTTP adapter over
:func:`backend.assembly.markdown.assemble_job` (Step 29). These tests
cover the wire surface:

* the happy path returns 201 with a verbatim projection of the
  manifest's provenance fields,
* re-runs return 200 with byte-identical Markdown and an identical
  ``markdown_sha256``,
* the precondition gate fires cleanly for the four documented
  failure modes (unknown job, non-UUID, wrong state, missing
  critic_report),
* the route never mutates ``state.json`` and never mutates any of
  the six source artefacts on disk,
* missing critic_report path produces no output files at all,
* anonymous callers get 401 (separately enforced in
  :mod:`tests.jobs_routes.test_authentication_required`).

The assembler itself has its own exhaustive coverage in
``tests/assembly/test_markdown_assembly.py``; we do not re-test its
internal behaviour here. We only assert the *wire contract* and the
*state-machine invariance* the route's docstring claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.agents.briefing_models import Briefing
from backend.agents.benefits_models import BenefitsBrief
from backend.agents.critic_models import Stage2CriticReport
from backend.agents.faq_models import FAQDocument
from backend.agents.mapping_models import ProductMapping
from backend.agents.objections_models import ObjectionsRegister
from backend.jobs.state import JobState
from backend.jobs.storage import (
    job_folder,
    write_benefits,
    write_briefing,
    write_critic_report,
    write_faq,
    write_objections,
    write_product_mapping,
)


_SOURCE_ARTEFACTS = (
    "briefing.json",
    "product_mapping.json",
    "benefits.json",
    "faq.json",
    "objections.json",
    "critic_report.json",
)


def _route(job_id: str) -> str:
    return f"/api/jobs/{job_id}/brief/assemble"


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _snapshot_state_and_artefacts(job_id: str) -> dict[str, bytes]:
    folder = job_folder(job_id)
    snapshot: dict[str, bytes] = {
        "state.json": _read_bytes(folder / "state.json"),
    }
    for name in _SOURCE_ARTEFACTS:
        path = folder / name
        if path.exists():
            snapshot[name] = _read_bytes(path)
    return snapshot


# ---------------------------------------------------------------------------
# Local fixture: an approved job with every Stage 2 artefact on disk but
# WITHOUT prospect_brief.md (so the first POST is genuinely a 201).
# ---------------------------------------------------------------------------
#
# ``conftest.job_with_all_stage2_artefacts`` pre-seeds prospect_brief.md
# so the inspector route tests can flip ``available_artefacts`` true.
# For Step 32 we need the *inverse* — the brief absent — so the 201
# branch is exercised end-to-end. We strip the seed file rather than
# rebuild a parallel fixture from scratch.

@pytest.fixture()
def approved_job_ready_for_assembly(
    job_with_all_stage2_artefacts: str,
) -> Iterator[str]:
    job_id = job_with_all_stage2_artefacts
    folder = job_folder(job_id)
    seed = folder / "prospect_brief.md"
    if seed.exists():
        seed.unlink()
    manifest = folder / "document_manifest.json"
    if manifest.exists():
        manifest.unlink()
    yield job_id


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_post_assembles_brief_for_approved_job(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """First POST returns 201 and writes both outputs."""
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text

    body = r.json()
    assert set(body.keys()) == {
        "job_id",
        "markdown_filename",
        "manifest_filename",
        "markdown_sha256",
        "critic_verdict",
        "generated_at",
    }
    assert body["job_id"] == job_id
    assert body["markdown_filename"] == "prospect_brief.md"
    assert body["manifest_filename"] == "document_manifest.json"
    assert re.fullmatch(r"[0-9a-f]{64}", body["markdown_sha256"])

    folder = job_folder(job_id)
    assert (folder / "prospect_brief.md").exists()
    assert (folder / "document_manifest.json").exists()


def test_response_critic_verdict_matches_critic_report_on_disk(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
    sample_critic_report: Stage2CriticReport,
) -> None:
    """``critic_verdict`` comes from the on-disk critic report."""
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text
    assert r.json()["critic_verdict"] == sample_critic_report.verdict.value


def test_response_generated_at_is_iso_utc_z_suffix(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        r.json()["generated_at"],
    )


def test_response_sha256_matches_on_disk_markdown(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """The reported hex matches a fresh sha256 of the file on disk."""
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text
    body = r.json()

    md_bytes = (job_folder(job_id) / "prospect_brief.md").read_bytes()
    assert body["markdown_sha256"] == hashlib.sha256(md_bytes).hexdigest()


# ---------------------------------------------------------------------------
# 2. 201 vs 200 idempotency
# ---------------------------------------------------------------------------

def test_post_returns_201_on_first_call(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text


def test_post_returns_200_on_second_call(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """Second POST regenerates in place and returns 200."""
    job_id = approved_job_ready_for_assembly
    first = authed_client.post(_route(job_id))
    assert first.status_code == 201, first.text

    second = authed_client.post(_route(job_id))
    assert second.status_code == 200, second.text


def test_idempotent_markdown_bytes(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """The on-disk Markdown is byte-identical across two POSTs.

    Mirrors the Step 29 determinism contract at the HTTP layer.
    """
    job_id = approved_job_ready_for_assembly
    md_path = job_folder(job_id) / "prospect_brief.md"

    r1 = authed_client.post(_route(job_id))
    assert r1.status_code == 201, r1.text
    bytes_after_first = md_path.read_bytes()

    r2 = authed_client.post(_route(job_id))
    assert r2.status_code == 200, r2.text
    bytes_after_second = md_path.read_bytes()

    assert bytes_after_first == bytes_after_second


def test_idempotent_sha256_in_response(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    job_id = approved_job_ready_for_assembly
    r1 = authed_client.post(_route(job_id))
    r2 = authed_client.post(_route(job_id))
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["markdown_sha256"] == r2.json()["markdown_sha256"]


# ---------------------------------------------------------------------------
# 3. Precondition failures — 4xx
# ---------------------------------------------------------------------------

def test_unknown_job_id_returns_404(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        _route("00000000-0000-4000-8000-000000000000")
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_non_uuid_job_id_returns_404(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(_route("not-a-uuid"))
    assert r.status_code == 404, r.text


def test_non_approved_state_returns_409(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    """A job that hasn't been approved yet is 409 with the actual state."""
    r = authed_client.post(_route(briefing_ready_job))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "state_not_approved"
    assert detail["current_state"] == JobState.BRIEFING_READY.value
    assert detail["required_state"] == JobState.APPROVED.value


def test_missing_critic_report_returns_409(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """An approved job with no critic_report.json gets a clean 409.

    Simulates on-disk drift where the state machine reached
    ``approved`` (so the route gets past the state check) but the
    critic report is missing from disk.
    """
    job_id = approved_job_ready_for_assembly
    (job_folder(job_id) / "critic_report.json").unlink()
    r = authed_client.post(_route(job_id))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "missing_critic_report"


def test_missing_critic_report_does_not_create_outputs(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """A 409 from the missing-critic path writes nothing on disk.

    Snapshots the state.json and every source artefact before/after
    the failed POST to prove the precondition gate fires *before*
    the assembler runs.
    """
    job_id = approved_job_ready_for_assembly
    folder = job_folder(job_id)
    (folder / "critic_report.json").unlink()

    before = _snapshot_state_and_artefacts(job_id)
    md_existed = (folder / "prospect_brief.md").exists()
    manifest_existed = (folder / "document_manifest.json").exists()

    r = authed_client.post(_route(job_id))

    assert r.status_code == 409, r.text
    # No outputs created.
    assert (folder / "prospect_brief.md").exists() is md_existed
    assert (folder / "document_manifest.json").exists() is manifest_existed
    assert not (folder / "prospect_brief.md").exists()
    assert not (folder / "document_manifest.json").exists()
    # No state mutation.
    assert _snapshot_state_and_artefacts(job_id) == before


# ---------------------------------------------------------------------------
# 4. State-machine and source-artefact invariance
# ---------------------------------------------------------------------------

def test_no_state_transition_on_success(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """A successful POST does not mutate ``state.json``."""
    job_id = approved_job_ready_for_assembly
    state_path = job_folder(job_id) / "state.json"
    before = state_path.read_bytes()

    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text

    after = state_path.read_bytes()
    assert before == after

    # Defensive parse: confirm the documented fields are still the
    # values we expect (in case some future helper rewrites
    # state.json byte-equivalent but with mutated content — paranoid
    # check costs us nothing).
    parsed = json.loads(after.decode("utf-8"))
    assert parsed["current_state"] == JobState.APPROVED.value
    assert parsed["last_error"] is None


def test_no_source_artefact_mutation(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """A successful POST does not mutate any of the six source files."""
    job_id = approved_job_ready_for_assembly
    folder = job_folder(job_id)
    before = {
        name: (folder / name).read_bytes() for name in _SOURCE_ARTEFACTS
    }

    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text

    after = {
        name: (folder / name).read_bytes() for name in _SOURCE_ARTEFACTS
    }
    assert before == after


def test_no_state_transition_on_409_path(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    """A 409 from the state-precondition path does not mutate state."""
    job_id = briefing_ready_job
    folder = job_folder(job_id)
    state_before = (folder / "state.json").read_bytes()
    briefing_before = (folder / "briefing.json").read_bytes()

    r = authed_client.post(_route(job_id))
    assert r.status_code == 409, r.text

    assert (folder / "state.json").read_bytes() == state_before
    assert (folder / "briefing.json").read_bytes() == briefing_before
    # And no outputs were written.
    assert not (folder / "prospect_brief.md").exists()
    assert not (folder / "document_manifest.json").exists()


# ---------------------------------------------------------------------------
# 5. Manifest sanity
# ---------------------------------------------------------------------------

def test_manifest_file_is_valid_json_with_expected_keys(
    authed_client: TestClient,
    approved_job_ready_for_assembly: str,
) -> None:
    """The manifest written alongside the Markdown carries the Step 29
    schema (sections, artefacts, outputs, etc.)."""
    job_id = approved_job_ready_for_assembly
    r = authed_client.post(_route(job_id))
    assert r.status_code == 201, r.text

    manifest_path = job_folder(job_id) / "document_manifest.json"
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_keys = {
        "schema_version",
        "job_id",
        "company_name",
        "company_url",
        "generated_at",
        "markdown_filename",
        "markdown_sha256",
        "markdown_byte_length",
        "sections",
        "artefacts",
        "outputs",
        "critic_verdict",
        "warnings",
    }
    assert expected_keys.issubset(set(parsed.keys()))
    assert parsed["markdown_filename"] == "prospect_brief.md"
    assert parsed["markdown_sha256"] == r.json()["markdown_sha256"]


# ---------------------------------------------------------------------------
# 6. Auth gate — sanity check (the auth-required suite has the parametrized
#    enumeration; we duplicate one direct case here for locality).
# ---------------------------------------------------------------------------

def test_anonymous_request_returns_401(
    anon_client: TestClient,
) -> None:
    r = anon_client.post(
        _route("00000000-0000-4000-8000-000000000000")
    )
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Helper-test hygiene — make sure the local fixture matches reality.
# ---------------------------------------------------------------------------

def test_local_fixture_strips_seed_markdown(
    approved_job_ready_for_assembly: str,
) -> None:
    """Sanity: the local fixture really does start with no brief on disk."""
    folder = job_folder(approved_job_ready_for_assembly)
    assert not (folder / "prospect_brief.md").exists()
    assert not (folder / "document_manifest.json").exists()


# ---------------------------------------------------------------------------
# The imports below are only here so the linter does not flag them as
# unused. They surface the typed contracts the fixture depends on.
# ---------------------------------------------------------------------------

_ = (
    BenefitsBrief,
    Briefing,
    FAQDocument,
    ObjectionsRegister,
    ProductMapping,
    Session,
    Stage2CriticReport,
    write_benefits,
    write_briefing,
    write_critic_report,
    write_faq,
    write_objections,
    write_product_mapping,
)
