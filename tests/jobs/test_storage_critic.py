"""Tests for ``critic_report.json`` storage helpers.

Step 20 adds a single new artefact pair in :mod:`backend.jobs.storage`:

* :func:`write_critic_report` / :func:`read_critic_report`

The shape of these tests mirrors ``tests/jobs/test_storage_writers.py``
— round-trip a populated :class:`Stage2CriticReport`, prove the file
path lands inside the job folder under the dedicated filename
``critic_report.json``, assert the atomic-write postcondition (no
leftover ``.tmp`` file), and exercise the error paths: missing file
→ :class:`JobNotFound`, malformed JSON → :class:`json.JSONDecodeError`,
schema-invalid payload → :class:`pydantic.ValidationError`.

The schema-invalid path deliberately exercises the load-bearing
verdict/severity cross-field validator on
:class:`Stage2CriticReport` — a tampered report that pairs (e.g.) a
``BLOCKING`` issue with ``verdict = READY`` must fail at read time,
not silently mislead a downstream consumer about whether the writer
artefacts are shippable.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agents.critic_models import (
    CitedArtefact,
    CriticIssue,
    IssueCategory,
    Severity,
    Stage2CriticReport,
    Verdict,
)
from backend.jobs.storage import (
    JobNotFound,
    create_job_folder,
    read_critic_report,
    write_critic_report,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Sample-model builder
# ---------------------------------------------------------------------------

def _sample_critic_report() -> Stage2CriticReport:
    """Populated report with one WARNING issue → READY_WITH_WARNINGS.

    Carries every closed-enum slot we want to round-trip: a
    :class:`CitedArtefact`, a :class:`Severity`, and an
    :class:`IssueCategory`. The verdict is paired with the issue
    severity so the cross-field validator passes on construction.
    """
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
                description=(
                    "Answer veers into marketing-speak; tighten to a "
                    "senior pre-sales register."
                ),
                suggested_fix=(
                    "Replace 'world-class' with a concrete capability "
                    "claim grounded in the datasheet."
                ),
            ),
        ],
        summary=(
            "Artefacts hang together; one tone slip in the FAQ that "
            "an operator should clean up before sending."
        ),
        gaps=["Could not verify Netropy 100G jitter floor without datasheet."],
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_write_critic_report_persists_to_disk_and_reads_back(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    report = _sample_critic_report()
    path = write_critic_report(job_id, report)
    assert path == isolated_jobs_root / job_id / "critic_report.json"
    assert path.exists()

    rebuilt = read_critic_report(job_id)
    assert isinstance(rebuilt, Stage2CriticReport)
    assert rebuilt.company_name == "Acme Ltd"
    assert rebuilt.reviewed_at == date(2026, 5, 27)
    assert rebuilt.verdict is Verdict.READY_WITH_WARNINGS
    assert len(rebuilt.issues) == 1
    assert rebuilt.issues[0].artefact is CitedArtefact.FAQ
    assert rebuilt.issues[0].severity is Severity.WARNING
    assert rebuilt.issues[0].category is IssueCategory.OFF_BRAND_TONE
    assert rebuilt.gaps == [
        "Could not verify Netropy 100G jitter floor without datasheet."
    ]


def test_write_critic_report_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    """``write_critic_report`` should idempotently mkdir the job folder
    — mirrors the other artefact writers so a missing folder on a rare
    error path doesn't crash the writer."""
    job_id = _new_job_id()
    # Do NOT pre-create the folder.
    path = write_critic_report(job_id, _sample_critic_report())
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_critic_report_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    """Atomic-write contract: only the final file remains on the happy
    path. A stray ``.tmp`` would indicate a broken rename."""
    job_id = _new_job_id()
    write_critic_report(job_id, _sample_critic_report())
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_critic_report_uses_dedicated_filename(
    isolated_jobs_root: Path,
) -> None:
    """Pin that the helper writes ``critic_report.json`` specifically —
    no collision with any Stage 1 or Stage 2 writer artefact."""
    job_id = _new_job_id()
    write_critic_report(job_id, _sample_critic_report())
    folder = isolated_jobs_root / job_id
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == ["critic_report.json"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_read_critic_report_raises_when_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    create_job_folder(job_id)
    with pytest.raises(JobNotFound):
        read_critic_report(job_id)


def test_read_critic_report_raises_json_decode_error_on_malformed_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "critic_report.json").write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(json.JSONDecodeError):
        read_critic_report(job_id)


def test_read_critic_report_raises_validation_error_on_schema_invalid_file(
    isolated_jobs_root: Path,
) -> None:
    """A payload missing required fields → :class:`ValidationError`."""
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    (folder / "critic_report.json").write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_critic_report(job_id)


def test_read_critic_report_raises_when_verdict_contradicts_issues(
    isolated_jobs_root: Path,
) -> None:
    """The verdict/severity cross-field validator must re-run on
    reload. A hand-edit that pairs ``verdict = READY`` with a
    ``BLOCKING`` issue must fail here — otherwise a downstream
    consumer would think the artefacts are shippable when the critic
    flagged a blocker."""
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    tampered = {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "READY",  # contradicts the BLOCKING issue below
        "issues": [
            {
                "artefact": "BENEFITS",
                "locator": "executive_summary.body[0].claim",
                "severity": "BLOCKING",
                "category": "UNSUPPORTED_PRODUCT_CLAIM",
                "description": "Claim has no support in the knowledge bundle.",
                "suggested_fix": None,
            }
        ],
        "summary": "All clear.",
        "gaps": [],
    }
    (folder / "critic_report.json").write_text(
        json.dumps(tampered), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_critic_report(job_id)


# ---------------------------------------------------------------------------
# Co-existence with the writer artefacts
# ---------------------------------------------------------------------------

def test_critic_report_coexists_with_writer_artefacts(
    isolated_jobs_root: Path,
) -> None:
    """``critic_report.json`` uses a distinct filename, so a job that
    has run all three writers AND the critic carries all four files.
    Pins the no-collision contract — a future renamer that aliased
    critic onto a writer would break this test."""
    job_id = _new_job_id()
    folder = create_job_folder(job_id)
    # Stand-in writer artefacts — content shape doesn't matter for the
    # collision check; we only care that the filenames are distinct.
    (folder / "benefits.json").write_text("{}", encoding="utf-8")
    (folder / "faq.json").write_text("{}", encoding="utf-8")
    (folder / "objections.json").write_text("{}", encoding="utf-8")
    write_critic_report(job_id, _sample_critic_report())
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == [
        "benefits.json",
        "critic_report.json",
        "faq.json",
        "objections.json",
    ]
