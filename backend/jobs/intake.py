"""Job intake: validate inputs, create the DB row, create the folder.

The intake function is the only public surface in this module. It
performs five steps atomically (from the caller's perspective):

1. Validate and normalise ``company_name`` and ``company_url``.
2. Mint a UUID job id.
3. Open or upsert the ``Company`` row keyed by ``(name, website_url)``.
4. Insert the ``Job`` row at status :attr:`JobState.CREATED` with
   ``folder_path`` set to the absolute folder path.
5. Create the on-disk folder and write the initial ``state.json``.

If the DB transaction commits but the folder write fails the disk and
DB will disagree — read paths must treat ``Job.folder_path`` as the
authoritative location and tolerate a missing folder. The reverse
(folder created, transaction rolled back) is similarly survivable —
the folder is unreferenced and gets cleaned up by ops.

Why no transitions are applied here
-----------------------------------

Per Step 7a's scope, no state transitions are enabled. Intake opens
the row at :attr:`JobState.CREATED` and stops. The
``transitions`` list in state.json is empty. Step 7b (orchestrator
wiring) is responsible for the first ``created → researching`` edge.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Company, Job
from backend.jobs.state import JobState
from backend.jobs.storage import (
    create_job_folder,
    job_folder,
    write_initial_state,
)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class IntakeValidationError(ValueError):
    """Raised when intake input fails validation.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handlers behave predictably. The ``field`` attribute lets callers
    surface a per-field error to the UI without parsing the message.
    The message is constructed from a short, redacted reason — never
    echoes the offending value (which might be a typo'd company name
    the user doesn't want logged).
    """

    def __init__(self, *, field: str, reason: str) -> None:
        super().__init__(f"invalid {field}: {reason}")
        self.field = field
        self.reason = reason


# ---------------------------------------------------------------------------
# Pydantic-validated input shape
# ---------------------------------------------------------------------------

class _IntakeInputs(BaseModel):
    """Pydantic guards the URL shape; we do the name validation by hand.

    Pydantic's ``HttpUrl`` is the same class :class:`ResearchDossier`
    uses, so the round-trip through the agent layer is type-coherent.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntakeResult:
    """Return value from :func:`create_job` — the new job's identifiers.

    Callers (Step 7b orchestrator, future routes) typically only need
    ``job_id`` and ``folder_path``; the company_id is exposed so the
    upsert semantics are observable to tests.
    """

    job_id: str
    company_id: int
    folder_path: Path


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def create_job(
    *,
    db: Session,
    company_name: str,
    company_url: str,
    now: datetime | None = None,
) -> IntakeResult:
    """Create a job at status :attr:`JobState.CREATED`.

    Parameters
    ----------
    db
        An open SQLAlchemy session. The caller manages the
        transaction scope; this function commits on success.
    company_name
        Free-text company name. Whitespace is trimmed; the cleaned
        value must be at least 1 character.
    company_url
        A valid http(s) URL. Whitespace trimmed; Pydantic validates
        the rest.
    now
        Injection point for tests. Defaults to UTC wall clock.

    Returns
    -------
    :class:`IntakeResult` with ``job_id``, ``company_id``, and the
    absolute folder path.

    Raises
    ------
    :class:`IntakeValidationError`
        Inputs were not acceptable.
    """
    name = _normalise_name(company_name)
    url = _normalise_url(company_url)

    try:
        inputs = _IntakeInputs(company_name=name, company_url=url)
    except ValidationError as exc:
        # Surface the first error's location so the caller can route
        # the message to the right field. We do not echo the raw
        # value back — see IntakeValidationError docstring.
        first = exc.errors()[0]
        loc = first.get("loc", ("input",))
        field = str(loc[0])
        reason = first.get("msg", "validation failed")
        raise IntakeValidationError(field=field, reason=reason) from exc

    # Pydantic stringifies HttpUrl with a trailing slash in some
    # cases; persist the canonical string form.
    canonical_url = str(inputs.company_url)
    canonical_name = inputs.company_name

    when = now if now is not None else datetime.now(timezone.utc)

    company = _get_or_create_company(
        db=db, name=canonical_name, website_url=canonical_url, now=when,
    )

    job_id = str(uuid.uuid4())
    folder = job_folder(job_id)

    job = Job(
        id=job_id,
        company_id=company.id,
        started_at=when,
        status=JobState.CREATED.value,
        folder_path=str(folder),
    )
    db.add(job)
    db.commit()

    # Folder + state.json are created after the DB commit so a folder
    # without a matching row is the only inconsistency possible. The
    # opposite (row without folder) is recoverable by recreating the
    # folder; an orphan folder is harmless.
    create_job_folder(job_id)
    write_initial_state(
        job_id=job_id,
        company_name=canonical_name,
        company_url=canonical_url,
        created_at=when,
    )

    return IntakeResult(
        job_id=job_id,
        company_id=int(company.id),
        folder_path=folder,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_name(value: str | None) -> str:
    if value is None:
        raise IntakeValidationError(field="company_name", reason="missing")
    if not isinstance(value, str):
        raise IntakeValidationError(field="company_name", reason="must be a string")
    cleaned = " ".join(value.split())  # collapse internal whitespace
    if not cleaned:
        raise IntakeValidationError(field="company_name", reason="must not be empty")
    return cleaned


def _normalise_url(value: str | None) -> str:
    if value is None:
        raise IntakeValidationError(field="company_url", reason="missing")
    if not isinstance(value, str):
        raise IntakeValidationError(field="company_url", reason="must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise IntakeValidationError(field="company_url", reason="must not be empty")
    return cleaned


def _get_or_create_company(
    *,
    db: Session,
    name: str,
    website_url: str,
    now: datetime,
) -> Company:
    """Find an existing (name, website_url) row or create one.

    The ``UniqueConstraint("name", "website_url")`` on the companies
    table makes the pair the natural key. On a hit, we bump
    ``last_researched_at`` and ``research_count``; on a miss, we
    insert with both timestamps set to ``now``.
    """
    existing = db.execute(
        select(Company).where(
            Company.name == name,
            Company.website_url == website_url,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.last_researched_at = now
        existing.research_count = (existing.research_count or 0) + 1
        db.flush()
        return existing

    company = Company(
        name=name,
        website_url=website_url,
        first_researched_at=now,
        last_researched_at=now,
        research_count=1,
    )
    db.add(company)
    db.flush()  # populate company.id
    return company
