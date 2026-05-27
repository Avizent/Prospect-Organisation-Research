"""Tests for ``product_mapping.json`` storage helpers.

Step 14 adds three helpers in :mod:`backend.jobs.storage`:

* :func:`backend.jobs.storage.write_product_mapping`
* :func:`backend.jobs.storage.read_product_mapping`
* the private path helper, exercised indirectly via the two above.

The shape of the tests mirrors the existing ``briefing.json`` tests
in ``test_job_storage.py``: round-trip a populated mapping, prove
the file path lands inside the job folder, assert the no-tmp-file
postcondition of the atomic write helper, and exercise every error
path (missing file → ``JobNotFound``, malformed JSON →
``JSONDecodeError``, schema-invalid payload →
``pydantic.ValidationError``, hand-edited out-of-range
``knowledge_excerpt_refs`` → ``ValidationError`` via the
:class:`ProductMapping` top-level validator).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

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
from backend.jobs.storage import (
    JobNotFound,
    create_job_folder,
    read_product_mapping,
    write_product_mapping,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


def _sample_product_mapping() -> ProductMapping:
    """A populated :class:`ProductMapping` with one match, one
    unmatched need, and two excerpts — enough to exercise every
    nested type (HttpUrl, date, ProductLine, KnowledgeSource,
    Confidence) and the model-level
    ``knowledge_excerpt_refs`` range validator on round-trip."""
    return ProductMapping(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        mapped_at=date(2026, 5, 27),
        matches=[
            NeedProductMatch(
                need_priority=1,
                need_summary="De-risk SD-WAN rollout",
                products=[
                    ProductReference(
                        product_line=ProductLine.EMULATORS,
                        product_name="Netropy 100G",
                        knowledge_excerpt_refs=[0, 1],
                    )
                ],
                use_case_framing=(
                    "Lab-emulate the planned SD-WAN topology with "
                    "realistic latency and loss before cutover."
                ),
                why_this_fits=(
                    "Briefing flags an active SD-WAN programme; "
                    "Netropy supports the bandwidth and impairment "
                    "ranges the design will hit."
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
            KnowledgeExcerptRef(
                source_file=KnowledgeSource.CASE_STUDIES,
                heading="SD-WAN lab validation",
                rationale="Prior example of similar engagement.",
            ),
        ],
        gaps=["No datasheet detail on Netropy 100G jitter floor."],
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_write_product_mapping_persists_to_disk_and_reads_back(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    mapping = _sample_product_mapping()
    path = write_product_mapping(job_id, mapping)
    assert path == isolated_jobs_root / job_id / "product_mapping.json"
    assert path.exists()

    rebuilt = read_product_mapping(job_id)
    assert isinstance(rebuilt, ProductMapping)
    assert rebuilt.company_name == "Acme Ltd"
    assert rebuilt.mapped_at == date(2026, 5, 27)
    assert rebuilt.matches[0].need_priority == 1
    assert rebuilt.matches[0].confidence is Confidence.HIGH
    assert (
        rebuilt.matches[0].products[0].product_line is ProductLine.EMULATORS
    )
    assert rebuilt.matches[0].products[0].knowledge_excerpt_refs == [0, 1]
    assert (
        rebuilt.knowledge_excerpts[0].source_file
        is KnowledgeSource.PRODUCTS_EMULATORS
    )
    assert rebuilt.unmatched_needs[0].need_priority == 2


def test_write_product_mapping_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    """``write_product_mapping`` should idempotently mkdir the job
    folder — mirrors the dossier / contacts / briefing helpers so an
    intake-side race doesn't crash the writer."""
    job_id = _new_job_id()
    # Do NOT pre-create the folder.
    path = write_product_mapping(job_id, _sample_product_mapping())
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_product_mapping_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    """Atomic write contract: only the final file should remain after
    a successful write. A stray ``.tmp`` file would be a real bug — a
    crash mid-rename would leave one behind, but the happy path must
    not."""
    job_id = _new_job_id()
    write_product_mapping(job_id, _sample_product_mapping())
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_product_mapping_uses_dedicated_filename(
    isolated_jobs_root: Path,
) -> None:
    """Pin that the mapping helper writes ``product_mapping.json``
    specifically — no collision with any of the four Stage 1
    artefacts."""
    job_id = _new_job_id()
    write_product_mapping(job_id, _sample_product_mapping())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["product_mapping.json"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_read_product_mapping_raises_when_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    with pytest.raises(JobNotFound):
        read_product_mapping(job_id)


def test_read_product_mapping_raises_validation_error_on_schema_invalid_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    # Missing every required field — ProductMapping should reject.
    (folder / "product_mapping.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_product_mapping(job_id)


def test_read_product_mapping_raises_validation_error_on_out_of_range_ref(
    isolated_jobs_root: Path,
) -> None:
    """The :class:`ProductMapping` model-level validator runs on
    :meth:`model_validate`, so a hand-edited mapping whose
    ``knowledge_excerpt_refs`` point past the end of
    ``knowledge_excerpts`` fails on reload — never silently rendering
    a broken citation in a downstream writer prompt."""
    job_id = _new_job_id()
    write_product_mapping(job_id, _sample_product_mapping())
    folder = isolated_jobs_root / job_id
    raw = json.loads(
        (folder / "product_mapping.json").read_text("utf-8")
    )
    # Sample has 2 knowledge_excerpts → valid indices are 0 and 1.
    # Point one at 5 to force the range validator to fire.
    raw["matches"][0]["products"][0]["knowledge_excerpt_refs"] = [0, 5]
    (folder / "product_mapping.json").write_text(
        json.dumps(raw), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_product_mapping(job_id)


def test_read_product_mapping_raises_json_decode_error_on_malformed_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "product_mapping.json").write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(json.JSONDecodeError):
        read_product_mapping(job_id)
