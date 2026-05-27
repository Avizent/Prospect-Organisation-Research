"""Tests for ``benefits.json`` / ``faq.json`` / ``objections.json``
storage helpers.

Step 18 adds three new artefact triplets in :mod:`backend.jobs.storage`:

* :func:`write_benefits` / :func:`read_benefits`
* :func:`write_faq` / :func:`read_faq`
* :func:`write_objections` / :func:`read_objections`

The shape of these tests mirrors
``tests/jobs/test_storage_product_mapping.py``: round-trip a populated
model, prove the file path lands inside the job folder under the
dedicated filename, assert the atomic-write postcondition (no leftover
``.tmp`` file), and exercise the error paths — missing file →
:class:`JobNotFound`, malformed JSON → :class:`json.JSONDecodeError`,
schema-invalid payload → :class:`pydantic.ValidationError`.

Why test all three together
---------------------------
The three writer artefacts use the same storage idiom (per-file
``_atomic_write_json`` + full Pydantic ``model_validate`` on read)
and share the same on-disk contract. Co-locating the tests makes a
future refactor that breaks the idiom (e.g. switching to a single
writer-artefact union) trivially detectable.

Why no cross-file atomicity test
--------------------------------
Step 18 deliberately does NOT attempt cross-file transactional
writes — see the docstring on
:meth:`backend.orchestrator.Orchestrator.run_stage2_writers`. Each
artefact is independently atomic; partial-completion on a downstream
writer failure is expected behaviour, not a bug.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
    Confidence,
)
from backend.agents.faq_models import (
    FAQCategory,
    FAQDocument,
    FAQEntry,
)
from backend.agents.objections_models import (
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
)
from backend.jobs.storage import (
    JobNotFound,
    create_job_folder,
    read_benefits,
    read_faq,
    read_objections,
    write_benefits,
    write_faq,
    write_objections,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Sample-model builders
# ---------------------------------------------------------------------------

def _claim(*, knowledge_excerpt_refs: list[int] | None = None) -> BenefitClaim:
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
        knowledge_excerpt_refs=(
            knowledge_excerpt_refs if knowledge_excerpt_refs is not None
            else [0]
        ),
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )


def _sample_benefits_brief() -> BenefitsBrief:
    """Populated brief — one claim per section, each audience pinned."""
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
        gaps=["No datasheet detail on Netropy 100G jitter floor."],
    )


def _sample_faq_document() -> FAQDocument:
    """Populated FAQ — 12 entries (the schema floor), every category
    represented at least once."""
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
        gaps=["Thin coverage on Commercial discount tiers."],
    )


def _sample_objections_register() -> ObjectionsRegister:
    """Populated register — 15 rows (the schema floor)."""
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


# ---------------------------------------------------------------------------
# Round-trip — benefits
# ---------------------------------------------------------------------------

def test_write_benefits_persists_to_disk_and_reads_back(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    brief = _sample_benefits_brief()
    path = write_benefits(job_id, brief)
    assert path == isolated_jobs_root / job_id / "benefits.json"
    assert path.exists()

    rebuilt = read_benefits(job_id)
    assert isinstance(rebuilt, BenefitsBrief)
    assert rebuilt.company_name == "Acme Ltd"
    assert rebuilt.written_at == date(2026, 5, 27)
    assert rebuilt.executive_summary.audience is Audience.CTO_CIO
    assert rebuilt.technical_fit.audience is Audience.ENGINEERS
    assert rebuilt.business_case.audience is Audience.IT_DIRECTOR
    assert rebuilt.executive_summary.body[0].confidence is Confidence.HIGH
    assert rebuilt.gaps == [
        "No datasheet detail on Netropy 100G jitter floor."
    ]


def test_write_benefits_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    """``write_benefits`` should idempotently mkdir the job folder
    — mirrors the other artefact writers so an intake-side race
    doesn't crash the writer."""
    job_id = _new_job_id()
    # Do NOT pre-create the folder.
    path = write_benefits(job_id, _sample_benefits_brief())
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_benefits_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    """Atomic-write contract: only the final file remains on the
    happy path. A stray ``.tmp`` would indicate a broken rename."""
    job_id = _new_job_id()
    write_benefits(job_id, _sample_benefits_brief())
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_benefits_uses_dedicated_filename(
    isolated_jobs_root: Path,
) -> None:
    """Pin that the helper writes ``benefits.json`` specifically — no
    collision with FAQ/objections or any Stage 1 artefact."""
    job_id = _new_job_id()
    write_benefits(job_id, _sample_benefits_brief())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["benefits.json"]


# ---------------------------------------------------------------------------
# Round-trip — FAQ
# ---------------------------------------------------------------------------

def test_write_faq_persists_to_disk_and_reads_back(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    doc = _sample_faq_document()
    path = write_faq(job_id, doc)
    assert path == isolated_jobs_root / job_id / "faq.json"
    assert path.exists()

    rebuilt = read_faq(job_id)
    assert isinstance(rebuilt, FAQDocument)
    assert rebuilt.company_name == "Acme Ltd"
    assert rebuilt.written_at == date(2026, 5, 27)
    assert len(rebuilt.entries) == 12
    assert rebuilt.entries[0].category is FAQCategory.ABOUT_ANS
    assert rebuilt.entries[0].confidence is Confidence.HIGH
    assert rebuilt.gaps == ["Thin coverage on Commercial discount tiers."]


def test_write_faq_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    path = write_faq(job_id, _sample_faq_document())
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_faq_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_faq(job_id, _sample_faq_document())
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_faq_uses_dedicated_filename(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_faq(job_id, _sample_faq_document())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["faq.json"]


# ---------------------------------------------------------------------------
# Round-trip — objections
# ---------------------------------------------------------------------------

def test_write_objections_persists_to_disk_and_reads_back(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    register = _sample_objections_register()
    path = write_objections(job_id, register)
    assert path == isolated_jobs_root / job_id / "objections.json"
    assert path.exists()

    rebuilt = read_objections(job_id)
    assert isinstance(rebuilt, ObjectionsRegister)
    assert rebuilt.company_name == "Acme Ltd"
    assert rebuilt.written_at == date(2026, 5, 27)
    assert len(rebuilt.rows) == 15
    assert rebuilt.rows[0].category is ObjectionCategory.PRICE
    assert rebuilt.rows[0].escalation_path == (
        "Account owner brings in solutions architect."
    )
    assert rebuilt.rows[0].confidence is Confidence.HIGH


def test_write_objections_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    path = write_objections(job_id, _sample_objections_register())
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_objections_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_objections(job_id, _sample_objections_register())
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_objections_uses_dedicated_filename(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_objections(job_id, _sample_objections_register())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["objections.json"]


# ---------------------------------------------------------------------------
# All three writer artefacts coexist (partial completion shape)
# ---------------------------------------------------------------------------

def test_three_writer_artefacts_coexist_in_one_job_folder(
    isolated_jobs_root: Path,
) -> None:
    """The three writer artefacts use distinct filenames, so a job
    that has run all three writers carries all three files. Pins
    the no-collision contract — a future renamer that accidentally
    aliased two would break this test."""
    job_id = _new_job_id()
    write_benefits(job_id, _sample_benefits_brief())
    write_faq(job_id, _sample_faq_document())
    write_objections(job_id, _sample_objections_register())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["benefits.json", "faq.json", "objections.json"]


# ---------------------------------------------------------------------------
# Error paths — benefits
# ---------------------------------------------------------------------------

def test_read_benefits_raises_when_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    with pytest.raises(JobNotFound):
        read_benefits(job_id)


def test_read_benefits_raises_validation_error_on_schema_invalid_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "benefits.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_benefits(job_id)


def test_read_benefits_raises_json_decode_error_on_malformed_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "benefits.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_benefits(job_id)


# ---------------------------------------------------------------------------
# Error paths — FAQ
# ---------------------------------------------------------------------------

def test_read_faq_raises_when_missing(isolated_jobs_root: Path) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    with pytest.raises(JobNotFound):
        read_faq(job_id)


def test_read_faq_raises_validation_error_on_schema_invalid_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "faq.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_faq(job_id)


def test_read_faq_raises_json_decode_error_on_malformed_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "faq.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_faq(job_id)


# ---------------------------------------------------------------------------
# Error paths — objections
# ---------------------------------------------------------------------------

def test_read_objections_raises_when_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    with pytest.raises(JobNotFound):
        read_objections(job_id)


def test_read_objections_raises_validation_error_on_schema_invalid_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "objections.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_objections(job_id)


def test_read_objections_raises_json_decode_error_on_malformed_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "objections.json").write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(json.JSONDecodeError):
        read_objections(job_id)
