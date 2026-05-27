"""Behavioural tests for ``ans-tool stage1-dryrun`` (Step 9c).

The CLI runs the real :class:`backend.orchestrator.Orchestrator`
against a private :class:`_ScriptedCloudClient` defined inside
``backend.cli``. These tests assert four things:

1. **Happy path** — four scripted responses drive the full Stage 1
   chain; all four artefacts land on disk and the job ends at
   ``briefing_ready`` with exit code 0.
2. **Failure path** — a script that raises a trap on the second agent
   leaves the dossier on disk, marks the job ``failed``, and exits
   non-zero with a stderr summary.
3. **Script exhaustion** — a script shorter than the chain length
   triggers a clean non-zero exit (not an unhandled exception
   traceback).
4. **Malformed script** — invalid JSON, wrong top-level shape, or a
   disallowed exception class is rejected at load time with a
   distinct non-zero exit code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

from backend.agents.briefing_models import Briefing
from backend.agents.contact_models import ContactExtractionResult
from backend.agents.needs_models import NeedsAssessment
from backend.agents.research_models import ResearchDossier
from backend.cli import main
from backend.db.models import Job
from backend.jobs.state import JobState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_script(tmp_path: Path, entries: list[dict]) -> Path:
    """Serialise *entries* to a JSON script file and return the path."""
    script = tmp_path / "script.json"
    script.write_text(json.dumps(entries), encoding="utf-8")
    return script


def _four_entries(
    *,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> list[dict]:
    """Build the four-entry script for a successful Stage 1 run.

    Each entry's ``text`` field is the JSON the agent's output parser
    will JSON-decode and Pydantic-validate. ``model_dump_json`` from
    Pydantic produces exactly that shape.
    """
    return [
        {"text": sample_dossier.model_dump_json()},
        {"text": sample_contacts.model_dump_json()},
        {"text": sample_needs_assessment.model_dump_json()},
        {"text": sample_briefing.model_dump_json()},
    ]


def _read_job_status(db_path: Path, job_id: str) -> str:
    """Open a one-shot session to read ``Job.status`` after a CLI run.

    The CLI uses its own engine in-process, so we cannot share the
    conftest ``db_session`` — open a fresh connection here.
    """
    engine = sa.create_engine(f"sqlite:///{db_path}")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        job = session.execute(
            sa.select(Job).where(Job.id == job_id)
        ).scalar_one()
        return job.status
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_stage1_dryrun_happy_path_produces_all_four_artefacts(
    runner: CliRunner,
    db_path: Path,
    isolated_jobs_root: Path,
    seeded_job: str,
    tmp_path: Path,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    script = _write_script(
        tmp_path,
        _four_entries(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        ),
    )

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code == 0, (
        f"stdout={result.output!r} stderr={result.stderr!r} exc={result.exception!r}"
    )
    # Each of the four artefacts is named in stdout with a byte size.
    for name in (
        "research_dossier.json",
        "contacts.json",
        "needs_assessment.json",
        "briefing.json",
    ):
        assert name in result.output

    # All four artefacts are on disk.
    folder = isolated_jobs_root / seeded_job
    on_disk = sorted(p.name for p in folder.iterdir())
    assert on_disk == [
        "briefing.json",
        "contacts.json",
        "needs_assessment.json",
        "research_dossier.json",
        "state.json",
    ]

    # Job row is at BRIEFING_READY.
    assert _read_job_status(db_path, seeded_job) == (
        JobState.BRIEFING_READY.value
    )


def test_stage1_dryrun_user_context_forwarded(
    runner: CliRunner,
    db_path: Path,
    isolated_jobs_root: Path,
    seeded_job: str,
    tmp_path: Path,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs_assessment: NeedsAssessment,
    sample_briefing: Briefing,
) -> None:
    """``--user-context`` is forwarded all the way down to the agents.

    The dry-run prints success paths only; we don't have a direct
    handle on the calls list (the scripted client is private). Instead
    we assert that passing ``--user-context`` does not break the chain
    and the job still lands at ``briefing_ready`` — a regression
    against a typo in the option name would surface here as an
    ``UsageError``.
    """
    script = _write_script(
        tmp_path,
        _four_entries(
            sample_dossier=sample_dossier,
            sample_contacts=sample_contacts,
            sample_needs_assessment=sample_needs_assessment,
            sample_briefing=sample_briefing,
        ),
    )

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
            "--user-context", "focus on healthcare divisions",
        ],
    )
    assert result.exit_code == 0, (
        f"stdout={result.output!r} stderr={result.stderr!r} exc={result.exception!r}"
    )
    assert _read_job_status(db_path, seeded_job) == (
        JobState.BRIEFING_READY.value
    )


# ---------------------------------------------------------------------------
# 2. Failure path
# ---------------------------------------------------------------------------

def test_stage1_dryrun_trap_in_second_agent_exits_nonzero_keeps_dossier(
    runner: CliRunner,
    db_path: Path,
    isolated_jobs_root: Path,
    seeded_job: str,
    tmp_path: Path,
    sample_dossier: ResearchDossier,
) -> None:
    """A trap on agent #2 → exit 1, dossier kept, no contacts/needs/briefing,
    state.json carries the failure category, stderr summarises the error."""
    script = _write_script(
        tmp_path,
        [
            {"text": sample_dossier.model_dump_json()},
            {
                "raise": {
                    "class": "JobBudgetExceeded",
                    "reason": "would exceed per-job budget",
                }
            },
        ],
    )

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code == 1, (
        f"stdout={result.output!r} stderr={result.stderr!r}"
    )

    # Stderr identifies the exception class and reason.
    assert "JobBudgetExceeded" in result.stderr
    assert "would exceed per-job budget" in result.stderr
    # And the structured last_error line.
    assert "runaway_trap" in result.stderr

    # State.json carries the failure.
    state_payload = json.loads(
        (isolated_jobs_root / seeded_job / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["current_state"] == JobState.FAILED.value
    assert state_payload["last_error"]["category"] == "runaway_trap"

    # DB row matches.
    assert _read_job_status(db_path, seeded_job) == JobState.FAILED.value

    # Partial artefacts: dossier exists, downstream ones do not.
    folder = isolated_jobs_root / seeded_job
    assert (folder / "research_dossier.json").exists()
    assert not (folder / "contacts.json").exists()
    assert not (folder / "needs_assessment.json").exists()
    assert not (folder / "briefing.json").exists()


# ---------------------------------------------------------------------------
# 3. Script exhaustion
# ---------------------------------------------------------------------------

def test_stage1_dryrun_script_exhaustion_fails_safely(
    runner: CliRunner,
    db_path: Path,
    isolated_jobs_root: Path,
    seeded_job: str,
    tmp_path: Path,
    sample_dossier: ResearchDossier,
) -> None:
    """A script with one entry runs out on the second agent.

    The orchestrator labels this as ``crashed`` (an unexpected
    exception, not a trap) and the CLI exits non-zero. No
    unhandled-exception traceback should appear in user output.
    """
    script = _write_script(
        tmp_path,
        [{"text": sample_dossier.model_dump_json()}],  # only one entry
    )

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code == 1, (
        f"stdout={result.output!r} stderr={result.stderr!r}"
    )
    # The CLI catches the exception and reports it on stderr — the
    # exception class is _ScriptExhausted (a RuntimeError subclass).
    assert "_ScriptExhausted" in result.stderr or "ScriptExhausted" in (
        result.stderr
    )

    # State machine labels this ``crashed`` (no trap class is
    # involved — the scripted client raised a plain RuntimeError).
    state_payload = json.loads(
        (isolated_jobs_root / seeded_job / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["last_error"]["category"] == "crashed"
    assert state_payload["current_state"] == JobState.FAILED.value


# ---------------------------------------------------------------------------
# 4. Malformed script
# ---------------------------------------------------------------------------

def test_stage1_dryrun_malformed_json_exits_nonzero(
    runner: CliRunner,
    db_path: Path,
    seeded_job: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "bad.json"
    script.write_text("{this is not json", encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code != 0
    assert "not valid JSON" in result.stderr


def test_stage1_dryrun_script_not_a_list_exits_nonzero(
    runner: CliRunner,
    db_path: Path,
    seeded_job: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / "wrong_shape.json"
    script.write_text(json.dumps({"text": "x"}), encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code != 0
    assert "must contain a JSON list" in result.stderr


def test_stage1_dryrun_disallowed_exception_class_rejected(
    runner: CliRunner,
    db_path: Path,
    seeded_job: str,
    tmp_path: Path,
) -> None:
    """A class name outside the allow-list is rejected at load time.

    This is the key safety property: the scripted client never
    ``eval``s or dynamically imports class names from the script. A
    typo or malicious entry simply fails to load.
    """
    script = _write_script(
        tmp_path,
        [{"raise": {"class": "OSError", "reason": "would be bad"}}],
    )

    result = runner.invoke(
        main,
        [
            "stage1-dryrun",
            "--job-id", seeded_job,
            "--script", str(script),
            "--db-path", str(db_path),
        ],
    )

    assert result.exit_code != 0
    assert "allow-list" in result.stderr
    assert "OSError" in result.stderr
