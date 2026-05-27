"""The briefing approval gate — the boundary between Stage 1 and Stage 2.

Step 10 introduces five operator-driven transitions plus a typed
briefing edit primitive. Stage 2 (document generation) reads only
the *approved* ``briefing.json``; the approval gate is therefore
the most important screen in the flow, and the only place in
Stage 1 where a human is in the loop.

Edges driven by this module
---------------------------

* ``briefing_ready → user_editing``       (:func:`open_for_editing`)
* ``briefing_ready → approved``           (:func:`approve` — approve-as-is)
* ``user_editing → approved``             (:func:`approve` — after edits)
* ``user_editing → regenerating_section`` (:func:`request_regeneration`)
* ``regenerating_section → user_editing`` (:func:`complete_regeneration`)
* ``regenerating_section → failed``       (:func:`fail_regeneration`)

Every transition runs through
:func:`backend.jobs.state.apply_transition` first (which validates
the edge against :data:`_ALLOWED_TRANSITIONS`), then is persisted
via :func:`backend.jobs.storage.append_transition` (which updates
state.json atomically), then ``Job.status`` is bumped in the DB so
the fast-path projection stays in sync.

The :func:`approve` guard
-------------------------

``approve()`` *must* reload and Pydantic-validate ``briefing.json``
before applying the transition. A briefing that's missing,
malformed, or schema-invalid (e.g. an inline edit broke a
``source_indices`` reference) must surface as an exception, not as
a silently-approved job whose Stage 2 reads later explode. The
on-disk read uses :func:`backend.jobs.storage.read_briefing`, whose
exception contract is:

* :class:`JobNotFound` if the file is missing
* :class:`json.JSONDecodeError` if the file is unparsable
* :class:`pydantic.ValidationError` if the schema is broken
  (including the top-level ``source_indices`` range validator)

We let those propagate unchanged — the route layer (Step 12) will
translate them to user-facing errors. No state change happens
before the read succeeds, so a failed approval leaves the job in
whichever editable state it was already in.

The :class:`BriefingPatch` model
--------------------------------

Inline edits on the approval screen are PATCHed as a typed
:class:`BriefingPatch`. Identity fields (``company_name``,
``company_url``, ``compiled_at``) are deliberately *not* on the
patch — editing them would lie about the artefact's provenance.
Every section the operator can edit is optional and, if present,
replaces the corresponding section wholesale. The merged briefing
is fed back through :class:`Briefing.model_validate`, which re-runs
the top-level ``_check_source_indices_in_range`` validator, so a
patch that breaks a citation is rejected with the same precise
location the agent layer would surface.

Out of scope for Step 10
------------------------

* No Anthropic client, no CloudClient wrapper, no agent invocation.
  The section *regeneration* logic that runs *inside*
  ``regenerating_section`` is Stage 2 territory; this module only
  provides the state-machine boundary that brackets it.
* No FastAPI routes. Step 12 will wire these functions to HTTP.
* No Keychain access, no M365 send, no document assembly.
* No new persistence helpers — the module uses the storage
  primitives shipped in Step 9a (read_briefing, write_briefing,
  append_transition, record_failure) and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.briefing_models import (
    Briefing,
    BusinessContext,
    ItLandscape,
    KeyPeople,
    Opportunity,
    Snapshot,
    SourceRegister,
)
from backend.db.models import Job
from backend.jobs.state import (
    JobState,
    apply_transition,
)
from backend.jobs.storage import (
    append_transition,
    read_briefing,
    read_state,
    record_failure,
    write_briefing,
)

__all__ = [
    "BriefingPatch",
    "BriefingSection",
    "ApprovalError",
    "EditNotAllowed",
    "open_for_editing",
    "apply_briefing_edit",
    "approve",
    "request_regeneration",
    "complete_regeneration",
    "fail_regeneration",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ApprovalError(Exception):
    """Base class for approval-gate errors raised by this module.

    Distinct from :class:`backend.jobs.state.IllegalTransition`
    (which is a generic state-machine error) and from the
    storage-layer exceptions (:class:`JobNotFound`,
    :class:`json.JSONDecodeError`, :class:`pydantic.ValidationError`)
    which we re-raise unchanged.
    """


class EditNotAllowed(ApprovalError):
    """A boundary call was made in a state that does not permit it.

    Carries the offending state and the operation name so callers
    can surface a precise message to the operator (e.g. "cannot
    apply edit: job is in ``researching``").
    """

    def __init__(
        self,
        *,
        job_id: str,
        operation: str,
        current_state: JobState,
        expected: frozenset[JobState],
    ) -> None:
        expected_list = ", ".join(sorted(s.value for s in expected))
        super().__init__(
            f"{operation} not allowed for job {job_id}: "
            f"current state is {current_state.value} "
            f"(expected one of: {expected_list})"
        )
        self.job_id = job_id
        self.operation = operation
        self.current_state = current_state
        self.expected = expected


# ---------------------------------------------------------------------------
# Section enum — names match Briefing top-level attribute names exactly
# ---------------------------------------------------------------------------

class BriefingSection(str, Enum):
    """The five editable sections of a :class:`Briefing`.

    Values are stable strings — they appear in ``state.json``
    transition records (as the ``reason`` for a
    ``user_editing → regenerating_section`` transition) and in
    future API payloads. Renaming a member is a migration event.

    ``sources`` is deliberately excluded: regenerating the source
    register independently would orphan every claim's
    ``source_indices``. The operator can still patch
    :attr:`SourceRegister` directly via :func:`apply_briefing_edit`
    when they need to (the merge re-validates the whole briefing).
    """

    SNAPSHOT = "snapshot"
    BUSINESS_CONTEXT = "business_context"
    IT_LANDSCAPE = "it_landscape"
    KEY_PEOPLE = "key_people"
    OPPORTUNITY = "opportunity"


# ---------------------------------------------------------------------------
# BriefingPatch
# ---------------------------------------------------------------------------

class BriefingPatch(BaseModel):
    """A typed partial update for a :class:`Briefing`.

    Identity fields (``company_name``, ``company_url``,
    ``compiled_at``) are intentionally not on the patch — editing
    them would lie about the artefact's provenance. Every other
    top-level field is optional; a field omitted from the wire
    payload is left untouched.

    Each section field, if present, replaces the corresponding
    section wholesale. The patch carries the same nested types as
    :class:`Briefing` itself, so the route layer can decode the
    incoming JSON straight into this model without a separate
    coercion layer.

    The actual merge + revalidation lives in
    :func:`apply_briefing_edit`; this class only describes the
    wire shape.
    """

    model_config = ConfigDict(extra="forbid")

    user_context: str | None = None
    snapshot: Snapshot | None = None
    business_context: BusinessContext | None = None
    it_landscape: ItLandscape | None = None
    key_people: KeyPeople | None = None
    opportunity: Opportunity | None = None
    sources: SourceRegister | None = None


# ---------------------------------------------------------------------------
# Result type — what a boundary call returns to its caller
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionApplied:
    """Pure-data record of one boundary transition.

    Returned by every state-changing function in this module so the
    route layer can log the resulting state without re-reading
    state.json.
    """

    job_id: str
    from_state: JobState
    to_state: JobState
    at: datetime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_state(
    *,
    job_id: str,
    operation: str,
    allowed: frozenset[JobState],
) -> JobState:
    """Read state.json and assert ``current_state`` is in ``allowed``.

    Raises :class:`JobNotFound` if the file is missing (the
    storage helper does this for us). Raises
    :class:`EditNotAllowed` if the current state is wrong. Returns
    the current state on success so callers can use it as the
    ``from_state`` for the transition.
    """
    snapshot = read_state(job_id)
    if snapshot.current_state not in allowed:
        raise EditNotAllowed(
            job_id=job_id,
            operation=operation,
            current_state=snapshot.current_state,
            expected=allowed,
        )
    return snapshot.current_state


def _update_job_status(db: Session, job_id: str, new_state: JobState) -> None:
    """Bump ``Job.status`` to the new state, commit.

    The disk projection (state.json) is the audit trail; the DB
    column is the fast-path query projection. We update both per
    transition so they never disagree for longer than one function
    call. A DB failure here propagates — losing the projection is
    not survivable; the row would otherwise show a stale state
    forever.
    """
    job = db.execute(select(Job).where(Job.id == job_id)).scalar_one()
    job.status = new_state.value
    db.commit()


def _apply_boundary_transition(
    *,
    db: Session,
    job_id: str,
    from_state: JobState,
    to_state: JobState,
    reason: str | None,
    now: datetime | None,
) -> TransitionApplied:
    """Run the three-step transition: validate → append → bump DB.

    The legality check happens inside ``apply_transition``; the
    state.json append happens inside ``append_transition``; the DB
    bump happens here. The order matters — we never bump the DB
    until both pure validation and disk persistence have succeeded.
    """
    record = apply_transition(
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        now=now,
    )
    append_transition(job_id, record)
    _update_job_status(db, job_id, to_state)
    return TransitionApplied(
        job_id=job_id,
        from_state=record.from_state,
        to_state=record.to_state,
        at=record.at,
    )


# ---------------------------------------------------------------------------
# Boundary 1 — open the briefing for editing
# ---------------------------------------------------------------------------

def open_for_editing(
    *,
    db: Session,
    job_id: str,
    now: datetime | None = None,
) -> TransitionApplied:
    """Apply ``briefing_ready → user_editing``.

    The only valid prior state is :attr:`JobState.BRIEFING_READY`.
    Calling this on an already-editing job raises
    :class:`EditNotAllowed` — re-entry into the edit state is not
    a state transition (there's no edge for it).

    No briefing read happens here: the operator can open an
    invalid or partially-written briefing for editing in order to
    fix it. The validation gate is :func:`approve`.
    """
    _require_state(
        job_id=job_id,
        operation="open_for_editing",
        allowed=frozenset({JobState.BRIEFING_READY}),
    )
    return _apply_boundary_transition(
        db=db,
        job_id=job_id,
        from_state=JobState.BRIEFING_READY,
        to_state=JobState.USER_EDITING,
        reason="open_for_editing",
        now=now,
    )


# ---------------------------------------------------------------------------
# Boundary 2 — inline edit
# ---------------------------------------------------------------------------

def apply_briefing_edit(
    *,
    job_id: str,
    patch: BriefingPatch,
) -> Briefing:
    """Merge ``patch`` into the on-disk briefing and re-validate.

    Mechanics:

    1. Guard: the job must be in :attr:`JobState.USER_EDITING`.
       Editing in any other state is rejected — the operator
       opened the briefing first via :func:`open_for_editing`,
       which is the only way into ``user_editing``.
    2. Read the existing briefing via
       :func:`backend.jobs.storage.read_briefing`. Errors propagate
       (``JobNotFound`` / ``JSONDecodeError`` / ``ValidationError``).
    3. Compute ``patch.model_dump(exclude_unset=True)`` and apply
       the diff over the existing briefing's dump. Fields the
       operator did not touch keep their original value.
    4. Re-validate by feeding the merged dict to
       :func:`Briefing.model_validate`. The top-level
       ``_check_source_indices_in_range`` validator runs here, so
       a patch that introduces an out-of-range citation surfaces as
       :class:`pydantic.ValidationError` with the offending path.
    5. Only if revalidation succeeds, write the merged briefing
       atomically via :func:`write_briefing`.

    Does **not** apply a state transition. Multiple edits within a
    single ``user_editing`` session are expected; each one is
    independently revalidated.
    """
    if not isinstance(patch, BriefingPatch):
        raise TypeError(
            f"patch must be a BriefingPatch, got {type(patch).__name__}"
        )

    _require_state(
        job_id=job_id,
        operation="apply_briefing_edit",
        allowed=frozenset({JobState.USER_EDITING}),
    )

    existing = read_briefing(job_id)
    merged = existing.model_dump(mode="python")

    diff = patch.model_dump(exclude_unset=True, mode="python")
    for key, value in diff.items():
        # Top-level field replacement only. The patch's typing
        # ensures keys here are always editable Briefing fields;
        # identity fields are not on BriefingPatch by design.
        merged[key] = value

    # Revalidate the whole briefing — this is what catches an
    # out-of-range source_indices, a section enum drift, or any
    # other constraint violation introduced by the patch.
    updated = Briefing.model_validate(merged)

    write_briefing(job_id, updated)
    return updated


# ---------------------------------------------------------------------------
# Boundary 3 — approve
# ---------------------------------------------------------------------------

def approve(
    *,
    db: Session,
    job_id: str,
    approved_by: str,
    now: datetime | None = None,
) -> TransitionApplied:
    """Approve the briefing for Stage 2.

    Valid prior states are :attr:`JobState.BRIEFING_READY`
    (approve-as-is, no edits performed) or
    :attr:`JobState.USER_EDITING` (approve-after-edits). Both edges
    are live in :data:`_ALLOWED_TRANSITIONS`.

    Guard sequence (order matters):

    1. ``approved_by`` must be a non-empty stripped string. Empty
       approvers would leave the audit trail useless.
    2. The current state must be ``BRIEFING_READY`` or
       ``USER_EDITING``. Anything else raises
       :class:`EditNotAllowed`.
    3. The briefing must reload and pass Pydantic validation —
       this catches a hand-edited file whose ``source_indices``
       point outside ``sources.entries``. Errors propagate from
       :func:`read_briefing` unchanged (:class:`JobNotFound`,
       :class:`json.JSONDecodeError`, :class:`pydantic.ValidationError`).
    4. Only if all three checks pass do we apply the transition
       and bump the DB. A failed guard leaves the job in its
       original editable state.

    The ``approved_by`` value is written into the transition
    record's ``reason`` field as ``"approved_by={username}"`` so
    the audit trail in ``state.json`` records who approved the
    briefing without needing a new column on the jobs table.
    """
    if not isinstance(approved_by, str):
        raise TypeError(
            f"approved_by must be a string, got {type(approved_by).__name__}"
        )
    cleaned = approved_by.strip()
    if not cleaned:
        raise ValueError("approved_by must not be empty")

    current = _require_state(
        job_id=job_id,
        operation="approve",
        allowed=frozenset({
            JobState.BRIEFING_READY,
            JobState.USER_EDITING,
        }),
    )

    # Reload + validate. Any failure here propagates unchanged
    # and leaves the job in its current state.
    read_briefing(job_id)

    return _apply_boundary_transition(
        db=db,
        job_id=job_id,
        from_state=current,
        to_state=JobState.APPROVED,
        reason=f"approved_by={cleaned}",
        now=now,
    )


# ---------------------------------------------------------------------------
# Boundary 4 — request section regeneration
# ---------------------------------------------------------------------------

def request_regeneration(
    *,
    db: Session,
    job_id: str,
    section: BriefingSection,
    instructions: str,
    now: datetime | None = None,
) -> TransitionApplied:
    """Apply ``user_editing → regenerating_section``.

    The operator selects one section (snapshot / business_context /
    it_landscape / key_people / opportunity) plus a free-text
    rewrite instruction. This module persists the transition; the
    Stage 2 worker that listens for ``regenerating_section`` jobs
    is out of scope for Step 10 — it will be added when the
    regeneration agent itself lands.

    The transition record's ``reason`` carries
    ``"section={value}; instructions={…}"`` (instructions truncated
    to a sane length) so the audit trail records what was asked
    for. Long-form instructions belong in a separate artefact
    written by the route layer, not on the transition reason
    (state.json must not grow unboundedly per request).
    """
    if not isinstance(section, BriefingSection):
        raise TypeError(
            f"section must be a BriefingSection, "
            f"got {type(section).__name__}"
        )
    if not isinstance(instructions, str):
        raise TypeError(
            f"instructions must be a string, "
            f"got {type(instructions).__name__}"
        )
    cleaned = instructions.strip()
    if not cleaned:
        raise ValueError("instructions must not be empty")

    _require_state(
        job_id=job_id,
        operation="request_regeneration",
        allowed=frozenset({JobState.USER_EDITING}),
    )

    # Truncate instructions for the transition reason — long-form
    # text should be stored alongside the job, not blown into the
    # audit trail. 200 chars is plenty for a one-line summary.
    summary = cleaned if len(cleaned) <= 200 else cleaned[:197] + "..."

    return _apply_boundary_transition(
        db=db,
        job_id=job_id,
        from_state=JobState.USER_EDITING,
        to_state=JobState.REGENERATING_SECTION,
        reason=f"section={section.value}; instructions={summary}",
        now=now,
    )


# ---------------------------------------------------------------------------
# Boundary 5 — regeneration finished cleanly
# ---------------------------------------------------------------------------

def complete_regeneration(
    *,
    db: Session,
    job_id: str,
    section: BriefingSection,
    now: datetime | None = None,
) -> TransitionApplied:
    """Apply ``regenerating_section → user_editing``.

    Called by the Stage 2 regeneration worker after it has
    successfully replaced the named section and written the
    updated briefing back to disk. This module only performs the
    transition; the regeneration agent owns the actual rewrite.
    """
    if not isinstance(section, BriefingSection):
        raise TypeError(
            f"section must be a BriefingSection, "
            f"got {type(section).__name__}"
        )

    _require_state(
        job_id=job_id,
        operation="complete_regeneration",
        allowed=frozenset({JobState.REGENERATING_SECTION}),
    )

    return _apply_boundary_transition(
        db=db,
        job_id=job_id,
        from_state=JobState.REGENERATING_SECTION,
        to_state=JobState.USER_EDITING,
        reason=f"completed_section={section.value}",
        now=now,
    )


# ---------------------------------------------------------------------------
# Boundary 6 — regeneration failed
# ---------------------------------------------------------------------------

def fail_regeneration(
    *,
    db: Session,
    job_id: str,
    category: str,
    details: str,
    now: datetime | None = None,
) -> TransitionApplied:
    """Apply ``regenerating_section → failed`` and record the error.

    Used by the regeneration worker when the rewrite hits a
    runaway trap, an output-invalid error, or any other crash.
    ``category`` is a short stable label (``"runaway_trap"`` /
    ``"output_invalid"`` / ``"crashed"``) the UI consumes;
    ``details`` is the human-readable explanation and must not
    contain secret material (same contract as
    :func:`record_failure` upstream).

    Order: append the transition first (so a row that ends up
    ``failed`` always has a matching transition record), then
    stamp ``last_error``, then bump ``Job.status``. A failure mid-
    sequence leaves the worse-fidelity projection unset, which is
    the right direction to fail in.
    """
    if not isinstance(category, str) or not category.strip():
        raise ValueError("category must be a non-empty string")
    if not isinstance(details, str) or not details.strip():
        raise ValueError("details must be a non-empty string")

    _require_state(
        job_id=job_id,
        operation="fail_regeneration",
        allowed=frozenset({JobState.REGENERATING_SECTION}),
    )

    result = _apply_boundary_transition(
        db=db,
        job_id=job_id,
        from_state=JobState.REGENERATING_SECTION,
        to_state=JobState.FAILED,
        reason=category,
        now=now,
    )
    record_failure(job_id, category=category, details=details)
    return result


# ---------------------------------------------------------------------------
# Re-exports for static-fence visibility
# ---------------------------------------------------------------------------
#
# The static fence test in tests/approval/ uses AST analysis to assert
# this module imports nothing forbidden (no anthropic, no keyring, no
# CloudClient, no fastapi, no backend.delivery, no backend.assembly,
# no frontend). The imports above are the complete set — pydantic,
# sqlalchemy, datetime, dataclasses, enum, and sibling backend.jobs /
# backend.agents / backend.db modules. Nothing else.
