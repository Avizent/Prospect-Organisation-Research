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
from backend.jobs.state import JobState, TransitionRecord


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

    The row is opened at :attr:`JobState.CREATED` with an empty
    ``transitions`` list and ``last_error=None``. Subsequent edges are
    persisted by :func:`append_transition`; failures are recorded by
    :func:`record_failure`.
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
# state.json — transition / failure mutators
# ---------------------------------------------------------------------------

def append_transition(job_id: str, record: TransitionRecord) -> JobStateFile:
    """Append a transition to ``state.json`` and update ``current_state``.

    Read-modify-write through the atomic JSON helper. The on-disk
    ``transitions`` list grows by one entry per call, in chronological
    order. ``current_state`` is updated to the transition's
    ``to_state``.

    The function does **not** validate that the transition is legal —
    callers are expected to call :func:`backend.jobs.state.apply_transition`
    first, which returns a :class:`TransitionRecord` only if the edge
    is in :data:`backend.jobs.state._ALLOWED_TRANSITIONS`. By the time
    a record reaches this function the legality check has already
    happened upstream.

    Raises :class:`JobNotFound` if no state.json exists for the job.
    """
    if not isinstance(record, TransitionRecord):
        raise TypeError(
            f"record must be a TransitionRecord, got {type(record).__name__}"
        )

    existing = read_state(job_id)
    transitions = list(existing.transitions)
    transitions.append(
        {
            "from": record.from_state.value,
            "to": record.to_state.value,
            "at": _iso(record.at),
            "reason": record.reason,
        }
    )

    updated = JobStateFile(
        job_id=existing.job_id,
        company_name=existing.company_name,
        company_url=existing.company_url,
        current_state=record.to_state,
        created_at=existing.created_at,
        transitions=transitions,
        last_error=existing.last_error,
    )

    payload = {
        "job_id": updated.job_id,
        "company_name": updated.company_name,
        "company_url": updated.company_url,
        "current_state": updated.current_state.value,
        "created_at": _iso(updated.created_at),
        "transitions": list(updated.transitions),
        "last_error": updated.last_error,
    }
    _atomic_write_json(_state_path(job_id), payload)
    return updated


def record_failure(
    job_id: str,
    *,
    category: str,
    details: str,
) -> JobStateFile:
    """Stamp ``last_error`` on state.json without touching ``current_state``.

    ``current_state`` is left alone — the caller is expected to have
    already applied a ``→ failed`` transition via
    :func:`append_transition` (which carries its own legality check).
    Splitting the two operations keeps each one small enough to reason
    about: ``append_transition`` records the *fact* of the transition;
    ``record_failure`` records the *diagnostic context* that explains
    why the transition happened.

    ``category`` is a short, stable label (e.g. ``"runaway_trap"``,
    ``"output_invalid"``, ``"crashed"``) consumed by the UI/CLI. The
    long-form ``details`` string is the human-readable explanation
    (``str(exc)``) and **must not contain secret material** — callers
    are expected to use the exception's ``reason`` attribute (for
    :class:`RunawayTrapFired`) rather than ``repr(exc)``, which can
    leak constructor state.

    Raises :class:`JobNotFound` if no state.json exists for the job.
    """
    existing = read_state(job_id)
    last_error = {"category": category, "details": details}

    updated = JobStateFile(
        job_id=existing.job_id,
        company_name=existing.company_name,
        company_url=existing.company_url,
        current_state=existing.current_state,
        created_at=existing.created_at,
        transitions=list(existing.transitions),
        last_error=last_error,
    )

    payload = {
        "job_id": updated.job_id,
        "company_name": updated.company_name,
        "company_url": updated.company_url,
        "current_state": updated.current_state.value,
        "created_at": _iso(updated.created_at),
        "transitions": list(updated.transitions),
        "last_error": updated.last_error,
    }
    _atomic_write_json(_state_path(job_id), payload)
    return updated


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
