"""Job-folder layout and atomic JSON I/O.

Two distinct artefacts live in every job folder
``~/.ans-tool/jobs/{job_id}/`` (root overridable via ``ANS_JOBS_ROOT``
for tests):

* ``state.json``         — the *audit trail* projection. Records
  ``current_state`` plus a list of past transitions and an optional
  ``last_error`` payload. The DB column ``Job.status`` is the
  fast-path query projection of the same information.
* ``research_dossier.json`` — the ResearchAgent's Pydantic-serialised
  output. Step 7a ships the read/write helpers but never calls the
  agent — tests exercise them with a fixture
  :class:`ResearchDossier`.

Atomicity
---------

Both writes use the *write-tmp-then-rename* pattern (``os.replace``
is atomic on POSIX). A crash mid-write leaves either the previous
file intact or no file at all — never a half-written file. We do not
``fsync`` — neither does the rest of the codebase (data.db is the
canonical durable store; these files are best-effort projections).

Validation
----------

Dossier reads run the full :class:`ResearchDossier` Pydantic
validation, so a corrupted file surfaces as ``ValidationError`` at
read time rather than producing a half-shaped object that explodes
deep in a writer agent. ``state.json`` is a small fixed shape and is
validated with a hand-rolled check (Pydantic would be overkill).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from backend.agents.research_models import ResearchDossier
from backend.jobs.state import JobState


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class JobNotFound(FileNotFoundError):
    """The job folder (or a required file inside it) does not exist.

    Subclasses :class:`FileNotFoundError` so existing ``except OSError``
    handlers behave predictably, while remaining greppable.
    """


# ---------------------------------------------------------------------------
# Jobs-root resolution
# ---------------------------------------------------------------------------

_ENV_JOBS_ROOT: ClassVar[str] = "ANS_JOBS_ROOT"


def jobs_root() -> Path:
    """Return the directory under which every job folder lives.

    Resolution order:

    1. ``$ANS_JOBS_ROOT`` if set and non-empty (tests use ``tmp_path``).
    2. ``~/.ans-tool/jobs/`` — alongside ``~/.ans-tool/data.db``.

    The directory is **not** created here. :func:`create_job_folder`
    handles per-call creation with ``parents=True, exist_ok=True``.
    """
    env_value = os.environ.get(_ENV_JOBS_ROOT, "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".ans-tool" / "jobs"


def job_folder(job_id: str) -> Path:
    """Return ``jobs_root() / job_id``. Does not create or check it."""
    _validate_job_id(job_id)
    return jobs_root() / job_id


def _validate_job_id(job_id: str) -> None:
    """Reject anything that isn't a parseable UUID string.

    The id is used to compose a filesystem path; allowing arbitrary
    strings would invite directory traversal (``../../etc``) and
    case-insensitive-filesystem collisions. UUIDs are also what
    :func:`create_job_folder` mints, so this is a tight contract.
    """
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id must be a non-empty string")
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise ValueError(f"job_id is not a valid UUID: {job_id!r}") from exc


# ---------------------------------------------------------------------------
# Folder creation
# ---------------------------------------------------------------------------

def create_job_folder(job_id: str) -> Path:
    """Create the job folder if absent, return its absolute path.

    Idempotent: a second call with the same id is a no-op. This
    matters because the intake transaction could partially fail and
    retry — we never want the second attempt to error out on
    "folder already exists".
    """
    folder = job_folder(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------------------
# Atomic JSON write helper
# ---------------------------------------------------------------------------

def _atomic_write_json(target: Path, payload: Any) -> None:
    """Write ``payload`` as JSON to ``target`` atomically.

    Strategy: serialise to bytes, write to ``target.tmp``, ``os.replace``
    over ``target``. On POSIX the rename is atomic; the reader sees
    either the old file or the new one, never a partial one. The tmp
    file lives in the same directory as ``target`` so the rename is
    a same-filesystem operation (``os.replace`` requires this).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    data = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobStateFile:
    """In-memory image of one ``state.json`` file.

    The shape is small and stable; we don't reach for Pydantic. Field
    names match the on-disk keys 1:1 to keep the round-trip obvious.
    """

    job_id: str
    company_name: str
    company_url: str
    current_state: JobState
    created_at: datetime
    transitions: list[dict[str, Any]]
    last_error: dict[str, Any] | None


def _state_path(job_id: str) -> Path:
    return job_folder(job_id) / "state.json"


def write_initial_state(
    *,
    job_id: str,
    company_name: str,
    company_url: str,
    created_at: datetime | None = None,
) -> JobStateFile:
    """Write the first state.json for a freshly-created job.

    Step 7a only ever writes a row at :attr:`JobState.CREATED` with an
    empty ``transitions`` list and ``last_error=None``. Step 7b will
    extend this with an ``append_transition`` helper.
    """
    when = created_at if created_at is not None else datetime.now(timezone.utc)
    record = JobStateFile(
        job_id=job_id,
        company_name=company_name,
        company_url=company_url,
        current_state=JobState.CREATED,
        created_at=when,
        transitions=[],
        last_error=None,
    )
    payload = {
        "job_id": record.job_id,
        "company_name": record.company_name,
        "company_url": record.company_url,
        "current_state": record.current_state.value,
        "created_at": _iso(record.created_at),
        "transitions": list(record.transitions),
        "last_error": record.last_error,
    }
    _atomic_write_json(_state_path(job_id), payload)
    return record


def read_state(job_id: str) -> JobStateFile:
    """Read and validate ``state.json`` for ``job_id``.

    Raises :class:`JobNotFound` if the file is missing,
    :class:`ValueError` if the shape is wrong.
    """
    path = _state_path(job_id)
    if not path.exists():
        raise JobNotFound(f"state.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))

    try:
        current = JobState(raw["current_state"])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"state.json for {job_id} has invalid current_state"
        ) from exc

    return JobStateFile(
        job_id=str(raw["job_id"]),
        company_name=str(raw["company_name"]),
        company_url=str(raw["company_url"]),
        current_state=current,
        created_at=_parse_iso(raw["created_at"]),
        transitions=list(raw.get("transitions") or []),
        last_error=raw.get("last_error"),
    )


# ---------------------------------------------------------------------------
# research_dossier.json
# ---------------------------------------------------------------------------

def _dossier_path(job_id: str) -> Path:
    return job_folder(job_id) / "research_dossier.json"


def write_dossier(job_id: str, dossier: ResearchDossier) -> Path:
    """Atomically write the Pydantic dossier to disk.

    Returns the path written. Pydantic's ``model_dump(mode="json")``
    handles the date/HttpUrl → string conversion; we don't massage the
    payload further.
    """
    create_job_folder(job_id)  # idempotent — defence against missing folder
    payload = dossier.model_dump(mode="json")
    target = _dossier_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_dossier(job_id: str) -> ResearchDossier:
    """Read and Pydantic-validate the dossier.

    Raises :class:`JobNotFound` if the file is missing. A corrupt
    file surfaces as :class:`pydantic.ValidationError` (not caught
    here — caller decides what to do, e.g. mark the job failed).
    """
    path = _dossier_path(job_id)
    if not path.exists():
        raise JobNotFound(f"research_dossier.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ResearchDossier.model_validate(raw)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``.

    All times in state.json are UTC. A naive datetime is assumed UTC
    rather than coerced, on the principle that callers should know
    what they're storing.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string back to a tz-aware UTC datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
