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

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from backend.agents.benefits_models import BenefitsBrief
from backend.agents.briefing_models import Briefing
from backend.agents.contact_models import ContactExtractionResult
from backend.agents.critic_models import Stage2CriticReport
from backend.agents.faq_models import FAQDocument
from backend.agents.mapping_models import ProductMapping
from backend.agents.needs_models import NeedsAssessment
from backend.agents.objections_models import ObjectionsRegister
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
# contacts.json
# ---------------------------------------------------------------------------
#
# Step 9a adds three more job-folder artefacts. Each pair mirrors the
# dossier helpers above: ``model_dump(mode="json")`` on the way out so
# Pydantic handles ``HttpUrl`` / ``date`` / enum → string; full
# ``model_validate`` on the way in so corruption surfaces as
# ``ValidationError`` at read time, not deep in a downstream consumer.
# We do NOT cross-validate the artefact's ``company_name`` against
# ``state.json`` — the readers are stateless by design (see Step 9a
# plan, design decision 4).

def _contacts_path(job_id: str) -> Path:
    return job_folder(job_id) / "contacts.json"


def write_contacts(
    job_id: str, contacts: ContactExtractionResult
) -> Path:
    """Atomically write the Pydantic contact extraction result to disk.

    Returns the path written. The folder is created if absent —
    intake-side races on rare error paths should not cause this helper
    to raise.
    """
    create_job_folder(job_id)
    payload = contacts.model_dump(mode="json")
    target = _contacts_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_contacts(job_id: str) -> ContactExtractionResult:
    """Read and Pydantic-validate ``contacts.json``.

    Raises :class:`JobNotFound` if the file is missing. A malformed
    JSON file raises :class:`json.JSONDecodeError`; a schema-invalid
    payload raises :class:`pydantic.ValidationError`. Neither is
    caught here — Step 9a is storage-only; recovery belongs to the
    orchestrator.
    """
    path = _contacts_path(job_id)
    if not path.exists():
        raise JobNotFound(f"contacts.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ContactExtractionResult.model_validate(raw)


# ---------------------------------------------------------------------------
# needs_assessment.json
# ---------------------------------------------------------------------------

def _needs_assessment_path(job_id: str) -> Path:
    return job_folder(job_id) / "needs_assessment.json"


def write_needs_assessment(
    job_id: str, assessment: NeedsAssessment
) -> Path:
    """Atomically write the Pydantic needs assessment to disk.

    Returns the path written.
    """
    create_job_folder(job_id)
    payload = assessment.model_dump(mode="json")
    target = _needs_assessment_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_needs_assessment(job_id: str) -> NeedsAssessment:
    """Read and Pydantic-validate ``needs_assessment.json``.

    Same exception contract as :func:`read_contacts`.
    """
    path = _needs_assessment_path(job_id)
    if not path.exists():
        raise JobNotFound(
            f"needs_assessment.json not found for job {job_id}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return NeedsAssessment.model_validate(raw)


# ---------------------------------------------------------------------------
# briefing.json
# ---------------------------------------------------------------------------

def _briefing_path(job_id: str) -> Path:
    return job_folder(job_id) / "briefing.json"


def write_briefing(job_id: str, briefing: Briefing) -> Path:
    """Atomically write the Pydantic briefing to disk.

    Returns the path written. ``briefing.json`` is the load-bearing
    artefact for the human approval gate — Stage 2 writers read the
    *approved* (post-edit) version, never the dossier. See handover
    §9.2 step 5.
    """
    create_job_folder(job_id)
    payload = briefing.model_dump(mode="json")
    target = _briefing_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_briefing(job_id: str) -> Briefing:
    """Read and Pydantic-validate ``briefing.json``.

    Same exception contract as :func:`read_contacts`. Importantly,
    the :class:`Briefing` top-level validator runs on
    :meth:`model_validate`, so a hand-edited briefing whose
    ``source_indices`` point outside ``sources.entries`` will fail
    here on reload — not silently render a broken citation.
    """
    path = _briefing_path(job_id)
    if not path.exists():
        raise JobNotFound(f"briefing.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Briefing.model_validate(raw)


# ---------------------------------------------------------------------------
# product_mapping.json
# ---------------------------------------------------------------------------

def _product_mapping_path(job_id: str) -> Path:
    return job_folder(job_id) / "product_mapping.json"


def write_product_mapping(job_id: str, mapping: ProductMapping) -> Path:
    """Atomically write the Pydantic product mapping to disk.

    Returns the path written. Stage 2's first artefact: the bridge
    between the approved briefing and the three downstream writers
    (benefits / FAQ / objections). Mirrors :func:`write_briefing` —
    ``model_dump(mode="json")`` handles the ``HttpUrl`` / ``date`` /
    enum → string conversions, ``create_job_folder`` is idempotent so
    a missing folder on a rare error path doesn't blow up the writer.
    """
    create_job_folder(job_id)
    payload = mapping.model_dump(mode="json")
    target = _product_mapping_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_product_mapping(job_id: str) -> ProductMapping:
    """Read and Pydantic-validate ``product_mapping.json``.

    Same exception contract as :func:`read_briefing`. The model-level
    validator on :class:`ProductMapping` re-checks the
    ``knowledge_excerpt_refs`` range on reload, so a hand-edited
    mapping with broken indices fails here rather than producing a
    broken citation in a downstream writer.
    """
    path = _product_mapping_path(job_id)
    if not path.exists():
        raise JobNotFound(
            f"product_mapping.json not found for job {job_id}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProductMapping.model_validate(raw)


# ---------------------------------------------------------------------------
# Stage 2 writer artefacts — benefits.json / faq.json / objections.json
# ---------------------------------------------------------------------------
#
# Step 18 adds the three Stage 2 writer artefacts. Each pair mirrors
# the briefing / product_mapping helpers above: ``model_dump(mode="json")``
# on the way out so Pydantic handles ``HttpUrl`` / ``date`` / enum →
# string; full ``model_validate`` on the way in so corruption surfaces
# as :class:`pydantic.ValidationError` at read time, not deep in a
# downstream consumer. We do NOT cross-validate the writer artefact's
# refs against the upstream briefing/mapping at the storage layer —
# that is the critic's job in a later step (matches the soft contract
# already used in :func:`read_product_mapping`).

def _benefits_path(job_id: str) -> Path:
    return job_folder(job_id) / "benefits.json"


def write_benefits(job_id: str, benefits: BenefitsBrief) -> Path:
    """Atomically write the Pydantic benefits brief to disk.

    Returns the path written. ``benefits.json`` is the first of three
    Stage 2 writer artefacts; the orchestrator (Step 18) calls this
    immediately after :class:`BenefitsWriter` succeeds so a downstream
    writer failure leaves the brief in place for retry / inspection.
    """
    create_job_folder(job_id)
    payload = benefits.model_dump(mode="json")
    target = _benefits_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_benefits(job_id: str) -> BenefitsBrief:
    """Read and Pydantic-validate ``benefits.json``.

    Same exception contract as :func:`read_briefing`. The
    :class:`BenefitsBrief` model-level validators (audience-matches-
    section and non-negative refs) run on :meth:`model_validate`, so
    a hand-edited brief with broken audience/refs fails here on
    reload — never silently rendering a broken section in the PDF.
    """
    path = _benefits_path(job_id)
    if not path.exists():
        raise JobNotFound(f"benefits.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return BenefitsBrief.model_validate(raw)


def _faq_path(job_id: str) -> Path:
    return job_folder(job_id) / "faq.json"


def write_faq(job_id: str, faq: FAQDocument) -> Path:
    """Atomically write the Pydantic FAQ document to disk.

    Returns the path written. Second of three Stage 2 writer artefacts;
    same per-writer immediate-persistence contract as
    :func:`write_benefits`.
    """
    create_job_folder(job_id)
    payload = faq.model_dump(mode="json")
    target = _faq_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_faq(job_id: str) -> FAQDocument:
    """Read and Pydantic-validate ``faq.json``.

    Same exception contract as :func:`read_briefing`. The
    :class:`FAQDocument` cardinality bounds (12-15 entries) and
    closed-enum / non-negative-refs validators all run on
    :meth:`model_validate`, so a hand-edited FAQ that violates the
    contract fails here on reload.
    """
    path = _faq_path(job_id)
    if not path.exists():
        raise JobNotFound(f"faq.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FAQDocument.model_validate(raw)


def _objections_path(job_id: str) -> Path:
    return job_folder(job_id) / "objections.json"


def write_objections(
    job_id: str, objections: ObjectionsRegister
) -> Path:
    """Atomically write the Pydantic objections register to disk.

    Returns the path written. Third of three Stage 2 writer artefacts;
    same per-writer immediate-persistence contract as
    :func:`write_benefits`.
    """
    create_job_folder(job_id)
    payload = objections.model_dump(mode="json")
    target = _objections_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_objections(job_id: str) -> ObjectionsRegister:
    """Read and Pydantic-validate ``objections.json``.

    Same exception contract as :func:`read_briefing`. The
    :class:`ObjectionsRegister` cardinality bounds (15-20 rows),
    closed-enum, non-empty escalation_path, and non-negative-refs
    validators all run on :meth:`model_validate`, so a hand-edited
    register that violates the contract fails here on reload — never
    silently rendering a blank cell or broken citation in the XLSX.
    """
    path = _objections_path(job_id)
    if not path.exists():
        raise JobNotFound(f"objections.json not found for job {job_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ObjectionsRegister.model_validate(raw)


# ---------------------------------------------------------------------------
# critic_report.json — Stage 2 critic verdict
# ---------------------------------------------------------------------------
#
# Step 20 adds the Stage 2 critic artefact. Same per-pair storage idiom
# as the writer artefacts above: ``model_dump(mode="json")`` on the way
# out so Pydantic handles enum/HttpUrl/date → string; full
# ``model_validate`` on the way in so a corrupt or hand-edited report
# surfaces as :class:`pydantic.ValidationError` at read time. The
# verdict/severity cross-field validator on :class:`Stage2CriticReport`
# re-runs on reload, so a tampered report that pairs (e.g.) a BLOCKING
# issue with ``verdict = READY`` fails here instead of misleading a
# downstream consumer about whether the artefacts are shippable.

def _critic_report_path(job_id: str) -> Path:
    return job_folder(job_id) / "critic_report.json"


def write_critic_report(job_id: str, report: Stage2CriticReport) -> Path:
    """Atomically write the Pydantic critic report to disk.

    Returns the path written. Step 20 keeps the critic's verdict
    advisory: the orchestrator persists this artefact in place at
    :attr:`JobState.APPROVED` and does not branch on the verdict.
    """
    create_job_folder(job_id)
    payload = report.model_dump(mode="json")
    target = _critic_report_path(job_id)
    _atomic_write_json(target, payload)
    return target


def read_critic_report(job_id: str) -> Stage2CriticReport:
    """Read and Pydantic-validate ``critic_report.json``.

    Same exception contract as :func:`read_briefing`. The
    :class:`Stage2CriticReport` verdict/severity cross-field validator
    runs on :meth:`model_validate`, so a hand-edited report that
    contradicts itself (e.g. ``verdict = READY`` alongside a
    ``BLOCKING`` issue) fails here on reload.
    """
    path = _critic_report_path(job_id)
    if not path.exists():
        raise JobNotFound(
            f"critic_report.json not found for job {job_id}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Stage2CriticReport.model_validate(raw)


# ---------------------------------------------------------------------------
# prospect_brief.md / document_manifest.json — Step 29 assembly outputs
# ---------------------------------------------------------------------------
#
# Step 29 adds two terminal artefacts produced by
# :mod:`backend.assembly.markdown`:
#
# * ``prospect_brief.md``      — the human-readable Markdown rollup of
#                                the briefing + Stage 2 artefacts.
# * ``document_manifest.json`` — provenance/integrity record for the
#                                ``.md`` and its input artefacts.
#
# Both are *outputs*: nothing in the codebase reads them back, so there
# is no corresponding ``read_*`` helper. The write helpers reuse the
# same atomic-rename pattern as every other artefact in this module
# (``_atomic_write_json`` for JSON, the new ``_atomic_write_text`` for
# the ``.md``). Neither writer touches state.json, the job status, or
# any transition: assembly is a side-effect-free *render* over existing
# on-disk artefacts (see ``backend/assembly/markdown.py``).

def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically as UTF-8.

    Same write-tmp-then-``os.replace`` pattern as
    :func:`_atomic_write_json`: serialise to bytes, write to
    ``target.tmp``, ``os.replace`` over ``target``. On POSIX the rename
    is atomic; the reader sees either the old file or the new one,
    never a partial one. The tmp file lives in the same directory as
    ``target`` so the rename is a same-filesystem operation.

    The text is written verbatim (no normalisation, no BOM). Callers
    are expected to terminate the string with a single trailing
    newline if they want one — the helper does not add or strip one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _prospect_brief_md_path(job_id: str) -> Path:
    return job_folder(job_id) / "prospect_brief.md"


def _document_manifest_path(job_id: str) -> Path:
    return job_folder(job_id) / "document_manifest.json"


def write_prospect_brief_markdown(job_id: str, text: str) -> Path:
    """Atomically write the assembled prospect brief Markdown to disk.

    Returns the path written. Terminal artefact: nothing reads this
    back. Folder is created if absent (mirrors the other writers'
    defence-in-depth against a missing folder on a rare error path).
    """
    create_job_folder(job_id)
    target = _prospect_brief_md_path(job_id)
    _atomic_write_text(target, text)
    return target


def read_prospect_brief_markdown(job_id: str) -> str:
    """Read the assembled prospect brief Markdown as UTF-8 text.

    Step 31 adds this read helper so the HTTP layer can serve the
    Markdown body to the in-app viewer. The assembler
    (:mod:`backend.assembly.markdown`) is the only writer; this helper
    intentionally returns raw text without any rendering or
    interpretation. The viewer renders the text safely on the client.

    Raises :class:`JobNotFound` if the file is missing. There is no
    Pydantic round-trip here — Markdown is a display artefact, not a
    structured payload — so the only failure mode at this layer is the
    file being absent (which the route maps to HTTP 404).
    """
    path = _prospect_brief_md_path(job_id)
    if not path.exists():
        raise JobNotFound(
            f"prospect_brief.md not found for job {job_id}"
        )
    return path.read_text(encoding="utf-8")


def write_document_manifest(
    job_id: str, manifest: dict[str, Any]
) -> Path:
    """Atomically write the assembly manifest to disk as JSON.

    Returns the path written. The manifest is a plain ``dict`` (not a
    Pydantic model) — the assembler defines its shape; this helper
    just persists it. Same JSON-formatting policy as every other
    artefact (``indent=2, sort_keys=False, ensure_ascii=False``).
    """
    create_job_folder(job_id)
    target = _document_manifest_path(job_id)
    _atomic_write_json(target, manifest)
    return target


# ---------------------------------------------------------------------------
# Exports (Steps 36 / 38 / 40)
# ---------------------------------------------------------------------------
#
# The export layer (``backend.exporters``) produces derivative artefacts
# from the canonical Markdown brief. Files land under
# ``~/.ans-tool/jobs/{job_id}/exports/`` and their provenance is
# recorded in the manifest's ``exports[]`` array.
#
# Storage helpers here are deliberately schema-agnostic about the
# *renderer* — they take ``format`` as a short token (``"pdf"``,
# ``"docx"``, ...) and ``bytes`` and persist verbatim. The exporter
# package is the single source of truth for what bytes a given
# ``format`` carries.
#
# Step 40 — append-only immutable lineage
# ---------------------------------------
#
# ``exports[]`` is now **append-only**. Every new render appends a
# new entry; existing entries are NEVER mutated and NEVER removed.
# Each entry carries:
#
#   * ``export_id`` (== ``sha256``) — content-addressed identity
#   * ``version: int`` — monotonic per (job_id, format), starts at 1
#   * ``supersedes: str | None`` — the prior latest export_id for
#     that format, or ``None`` for v1. (Reverse lineage —
#     ``superseded_by`` — is derived dynamically from the array, never
#     persisted; this keeps every existing entry strictly immutable.)
#   * ``manifest_sha256_at_export: str`` — SHA-256 of the canonical
#     ``document_manifest.json`` bytes that were the *input* to this
#     render. Captures full manifest provenance, not just markdown
#     provenance.
#   * ``regeneration_count: int`` — derived from ``version - 1`` and
#     persisted for wire-compat with Step 36/38 consumers.
#
# A new top-level field ``latest_export_id: {format: export_id}`` is
# the O(1) pointer the route layer uses to choose "what is current"
# without re-deriving from positional order.
#
# Bytes are written twice: once to the stable alias
# ``exports/prospect_brief.{fmt}`` (consumed by ``read_export_bytes``
# / the GET route) and once to the per-version archive
# ``exports/archive/prospect_brief.v{N}.{fmt}``. The archive copy is
# the source of truth; the alias is route-layer convenience.
#
# Idempotency: if a new render produces ``sha256 ==
# latest_export_id[fmt]``, ``upsert_export_entry`` short-circuits —
# no new entry is appended, ``was_appended`` is ``False``, and the
# existing entry is returned. Bytes still ride through to disk via
# the caller (a no-op rewrite of identical content is harmless).
#
# Lazy v2→v3 migration: ``read_document_manifest`` projects a v3
# view over any v1/v2 manifest on read; the on-disk file is only
# rewritten on the next ``upsert_export_entry`` call, at which point
# it is materialised as canonical v3.

_EXPORT_FORMATS: frozenset[str] = frozenset({"pdf", "docx"})  # Steps 36+38

_EXPORT_FILENAMES: dict[str, str] = {
    "pdf": "prospect_brief.pdf",
    "docx": "prospect_brief.docx",
}

_MANIFEST_SCHEMA_VERSION: int = 3  # Step 40 — append-only lineage.

# Sub-folder under ``exports/`` for the immutable per-version archive.
_EXPORT_ARCHIVE_FOLDER_NAME: str = "archive"


def _exports_folder(job_id: str) -> Path:
    return job_folder(job_id) / "exports"


def _exports_archive_folder(job_id: str) -> Path:
    return _exports_folder(job_id) / _EXPORT_ARCHIVE_FOLDER_NAME


def _export_path(job_id: str, format: str) -> Path:
    if format not in _EXPORT_FORMATS:
        raise ValueError(
            f"unknown export format {format!r}; "
            f"expected one of {sorted(_EXPORT_FORMATS)}"
        )
    return _exports_folder(job_id) / _EXPORT_FILENAMES[format]


def _archive_export_path(job_id: str, format: str, version: int) -> Path:
    """Return the on-disk path for the immutable archived export bytes.

    Layout: ``exports/archive/prospect_brief.v{N}.{fmt}``. Versions
    are monotonic per (job_id, format) and start at 1.
    """
    if format not in _EXPORT_FORMATS:
        raise ValueError(
            f"unknown export format {format!r}; "
            f"expected one of {sorted(_EXPORT_FORMATS)}"
        )
    if not isinstance(version, int) or version < 1:
        raise ValueError(
            f"export archive version must be a positive int, got {version!r}"
        )
    return (
        _exports_archive_folder(job_id)
        / f"prospect_brief.v{version}.{format}"
    )


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Atomically write raw bytes to ``target`` (same pattern as the
    JSON/text helpers above: write-tmp-then-``os.replace``).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def write_export(job_id: str, format: str, payload: bytes) -> Path:
    """Write the export bytes to the stable alias atomically.

    Returns the path written. Creates the per-job exports folder if
    absent. Does NOT touch the manifest and does NOT write the
    per-version archive copy — callers compose the write with
    :func:`write_export_archive` and :func:`upsert_export_entry` so
    the archive copy, the alias, and the manifest entry all land
    together. (We deliberately do archive-first then alias then
    manifest: a crash mid-sequence leaves either an orphan archive
    file or an orphan alias file, both of which are invisible to
    retrieval because the manifest has no entry, and the next
    successful run replaces atomically.)
    """
    create_job_folder(job_id)
    target = _export_path(job_id, format)
    _atomic_write_bytes(target, payload)
    return target


def write_export_archive(
    job_id: str, format: str, version: int, payload: bytes,
) -> Path:
    """Write the per-version archive copy atomically (Step 40).

    Returns the path written. The archive copy is the immutable
    source-of-truth for that version's bytes; the stable alias
    written by :func:`write_export` is route-layer convenience.
    """
    create_job_folder(job_id)
    target = _archive_export_path(job_id, format, version)
    _atomic_write_bytes(target, payload)
    return target


def read_export_bytes(job_id: str, format: str) -> bytes:
    """Read the export file from disk as raw bytes.

    Raises :class:`JobNotFound` if the file is absent.
    """
    path = _export_path(job_id, format)
    if not path.exists():
        raise JobNotFound(
            f"export {format!r} not found for job {job_id}"
        )
    return path.read_bytes()


def read_export_archive_bytes(
    job_id: str, format: str, version: int,
) -> bytes:
    """Read the per-version archive bytes from disk (Step 40).

    Raises :class:`JobNotFound` if the file is absent.
    """
    path = _archive_export_path(job_id, format, version)
    if not path.exists():
        raise JobNotFound(
            f"export archive v{version} for {format!r} "
            f"not found for job {job_id}"
        )
    return path.read_bytes()


def export_path_for(job_id: str, format: str) -> Path:
    """Return the on-disk path the export *would* live at.

    Helper for tests that need to inspect mtime / existence without
    triggering a read. Does not check existence.
    """
    return _export_path(job_id, format)


def export_archive_path_for(
    job_id: str, format: str, version: int,
) -> Path:
    """Return the on-disk path the archive entry *would* live at.

    Helper for tests; does not check existence.
    """
    return _archive_export_path(job_id, format, version)


# ---------------------------------------------------------------------------
# v3 manifest projector — lazy migration from v1/v2 on read
# ---------------------------------------------------------------------------

def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_document_manifest_sha256(job_id: str) -> str | None:
    """Return SHA-256 of the on-disk ``document_manifest.json`` bytes.

    The hash is taken over the canonical on-disk byte stream (whatever
    :func:`write_document_manifest` last wrote). Returns ``None`` if
    the file is absent. Used by :func:`upsert_export_entry` to record
    ``manifest_sha256_at_export`` on each new entry — provenance of the
    manifest state the export was rendered against.
    """
    path = _document_manifest_path(job_id)
    if not path.exists():
        return None
    return _sha256_hex(path.read_bytes())


def _project_manifest_v3(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical v3-shaped view of a v1/v2/v3 manifest dict.

    The projection is purely in-memory and is used **only inside**
    :func:`upsert_export_entry` to compute the v3 lineage fields before
    materialising the manifest to disk. The public read helper
    :func:`read_document_manifest` deliberately does NOT pass through
    this projector — Step 34/35/37 callers (the manifest route, the
    assembler) require byte-for-byte passthrough of legacy v1/v2 files.

    * v1: missing ``exports`` → added as ``[]``. Then promoted to v2.
    * v2: each entry receives the v3 lineage fields (``export_id``,
      ``version``, ``supersedes``, ``manifest_sha256_at_export``,
      ``regeneration_count``) derived from position-in-array and
      content (``export_id`` defaults to ``sha256`` when absent).
      A top-level ``latest_export_id: {fmt: export_id}`` map is built
      from the last entry per format.
    * v3: passed through; ``latest_export_id`` defaulted to ``{}`` if
      missing.

    No on-disk write happens here — :func:`upsert_export_entry` is the
    only writer that materialises v3 to disk.
    """
    projected = dict(raw)
    schema_version = projected.get("schema_version", 1)
    try:
        schema_int = int(schema_version) if schema_version is not None else 1
    except (TypeError, ValueError):
        schema_int = 1

    raw_exports = projected.get("exports")
    if not isinstance(raw_exports, list):
        raw_exports = []

    if schema_int >= _MANIFEST_SCHEMA_VERSION:
        # Already v3 (or beyond). Ensure latest_export_id is a dict.
        latest = projected.get("latest_export_id")
        if not isinstance(latest, dict):
            projected["latest_export_id"] = {}
        return projected

    # v1 / v2 → projected v3.
    upgraded_entries: list[Any] = []
    per_format_count: dict[str, int] = {}
    latest_map: dict[str, str] = {}
    for entry in raw_exports:
        if not isinstance(entry, dict):
            upgraded_entries.append(entry)
            continue
        fmt_raw = entry.get("format")
        if not isinstance(fmt_raw, str) or not fmt_raw:
            upgraded_entries.append(entry)
            continue
        position = per_format_count.get(fmt_raw, 0) + 1
        per_format_count[fmt_raw] = position
        upgraded = dict(entry)
        sha = entry.get("sha256")
        sha_str = sha if isinstance(sha, str) else ""
        if "export_id" not in upgraded or not upgraded.get("export_id"):
            upgraded["export_id"] = sha_str
        if "version" not in upgraded or not isinstance(
            upgraded.get("version"), int,
        ):
            upgraded["version"] = position
        if "supersedes" not in upgraded:
            upgraded["supersedes"] = None
        if "manifest_sha256_at_export" not in upgraded:
            upgraded["manifest_sha256_at_export"] = ""
        if "regeneration_count" not in upgraded:
            upgraded["regeneration_count"] = position - 1
        upgraded_entries.append(upgraded)
        eid = upgraded.get("export_id")
        if isinstance(eid, str) and eid:
            latest_map[fmt_raw] = eid

    projected["schema_version"] = _MANIFEST_SCHEMA_VERSION
    projected["exports"] = upgraded_entries
    projected["latest_export_id"] = latest_map
    return projected


def upsert_export_entry(
    job_id: str, entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Append an immutable lineage entry to ``manifest.exports[]``.

    Behaviour (Step 40 — append-only):

    * If ``document_manifest.json`` is missing, raises
      :class:`JobNotFound` — the caller must assemble the brief
      first.
    * The manifest is read through the v3 projector
      (:func:`_project_manifest_v3`); v1/v2 manifests are upgraded
      in-memory then materialised as canonical v3 by the write at
      the end of this call.
    * **Idempotency.** If the new ``entry["sha256"]`` equals the
      current ``latest_export_id[fmt]`` (the renderer produced the
      exact bytes already on record), no append happens. The
      existing entry is returned with ``was_appended=False``. The
      manifest file is **not rewritten** in this branch.
    * Otherwise the entry is appended with:
        - ``export_id`` = the new ``sha256``,
        - ``version`` = ``max(prior versions for fmt) + 1``,
        - ``supersedes`` = the prior ``latest_export_id[fmt]`` (or
          ``None`` if this is v1),
        - ``manifest_sha256_at_export`` = SHA-256 of the on-disk
          ``document_manifest.json`` bytes *as they exist now*
          (captures the manifest state the export was rendered
          against; reverse lineage is derived, never persisted),
        - ``regeneration_count`` = ``version - 1`` (derived; kept on
          the entry for wire-compat with Step 36/38 consumers).
      ``latest_export_id[fmt]`` is updated. Prior entries are left
      strictly unchanged.

    Returns ``(final_entry, was_appended)``. The write is atomic via
    :func:`_atomic_write_json`.
    """
    if "format" not in entry:
        raise ValueError("export entry must carry a 'format' key")

    fmt = entry["format"]
    if not isinstance(fmt, str) or fmt not in _EXPORT_FORMATS:
        raise ValueError(
            f"unknown export format {fmt!r}; "
            f"expected one of {sorted(_EXPORT_FORMATS)}"
        )

    # Capture the manifest SHA-256 BEFORE we mutate it — this is the
    # manifest state the renderer operated on.
    manifest_sha_at_export = read_document_manifest_sha256(job_id) or ""

    # Read raw manifest from disk, then project to v3 internally so the
    # public ``read_document_manifest`` can remain a verbatim
    # passthrough for legacy v1/v2 callers (the manifest route, the
    # assembler).
    raw_manifest = read_document_manifest(job_id)
    if not isinstance(raw_manifest, dict):
        raise ValueError(
            f"document_manifest.json for {job_id} is not a JSON object"
        )
    manifest = _project_manifest_v3(raw_manifest)
    exports = list(manifest.get("exports") or [])
    latest_map = dict(manifest.get("latest_export_id") or {})

    new_sha_raw = entry.get("sha256")
    new_sha = new_sha_raw if isinstance(new_sha_raw, str) else ""

    # Idempotency: same bytes as current latest → no-op.
    if new_sha and latest_map.get(fmt) == new_sha:
        # Locate the existing entry to return verbatim.
        for candidate in reversed(exports):
            if (
                isinstance(candidate, dict)
                and candidate.get("format") == fmt
                and candidate.get("export_id") == new_sha
            ):
                return dict(candidate), False
        # If we got here the latest_export_id pointer is stale; fall
        # through to the append path and self-heal on the next write.

    # Compute version and supersedes.
    prior_version = 0
    for candidate in exports:
        if (
            isinstance(candidate, dict)
            and candidate.get("format") == fmt
        ):
            v_raw = candidate.get("version")
            if isinstance(v_raw, int) and v_raw > prior_version:
                prior_version = v_raw
    new_version = prior_version + 1
    prior_latest = latest_map.get(fmt)
    supersedes = prior_latest if isinstance(prior_latest, str) and prior_latest else None

    final_entry: dict[str, Any] = dict(entry)
    final_entry["export_id"] = new_sha
    final_entry["version"] = new_version
    final_entry["supersedes"] = supersedes
    final_entry["manifest_sha256_at_export"] = manifest_sha_at_export
    final_entry["regeneration_count"] = new_version - 1

    exports.append(final_entry)
    if new_sha:
        latest_map[fmt] = new_sha

    manifest["schema_version"] = _MANIFEST_SCHEMA_VERSION
    manifest["exports"] = exports
    manifest["latest_export_id"] = latest_map

    write_document_manifest(job_id, manifest)
    return final_entry, True


def read_document_manifest(job_id: str) -> dict[str, Any]:
    """Read the assembly manifest from disk as a plain ``dict``.

    Step 34 adds this read helper so the HTTP layer can serve the
    Step 29 ``document_manifest.json`` to the in-app provenance
    viewer. The assembler is the only writer; this helper only
    persists the parse and lets the caller decide what to do with
    a malformed-on-disk file.

    Step 40 note: this helper deliberately does NOT project legacy
    v1/v2 manifests into the v3 shape. The Step 34 manifest route, the
    Step 37 assembler-preservation contract, and the
    :func:`compute_manifest_lifecycle` helper all rely on byte-for-byte
    passthrough — surfacing synthetic v3 lineage fields here would
    silently mutate those callers. The v3 projection is the private
    concern of :func:`upsert_export_entry`, which materialises the
    upgraded manifest to disk before any consumer sees the new shape.

    Raises
    ------
    JobNotFound
        if ``document_manifest.json`` does not exist on disk.
    ValueError
        if the ``job_id`` is not a valid UUID (raised by
        :func:`job_folder`'s validator).
    json.JSONDecodeError
        if the file exists but is not valid JSON — the route layer
        catches this and maps it to ``500 manifest_corrupt``.

    We deliberately do **not** Pydantic-validate the manifest here:
    the assembler is the single source of truth for its shape, the
    response envelope passes the dict through verbatim, and the
    frontend iterates the fields generically, so a typed re-validation
    at this layer would only invite drift.
    """
    path = _document_manifest_path(job_id)
    if not path.exists():
        raise JobNotFound(
            f"document_manifest.json not found for job {job_id}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


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
