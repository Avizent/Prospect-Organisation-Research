"""Pipeline orchestrator — Step 7b.

Step 7b ships a *minimal* orchestrator: one public coroutine,
:meth:`Orchestrator.run_research`, that drives a single
``created → researching → briefing_ready | failed`` slice of the
canonical job lifecycle. Briefing, mapping, writers, critic, and
delivery are out of scope until later steps; this module is purely
ResearchAgent execution + state persistence + failure recording.

The contract in one paragraph
-----------------------------

For a job currently at :attr:`JobState.CREATED`, the orchestrator
takes the Trap 7 semaphore slot, applies
``created → researching`` (DB + state.json), constructs a
:class:`backend.agents.research.ResearchAgent` with the injected
:class:`backend.agents._protocols.CloudClientProtocol`, calls
``agent.run`` on a worker thread (so the asyncio loop stays free
to schedule other coroutines while the agent is blocked on
``messages_create``), writes the returned :class:`ResearchDossier`
to ``research_dossier.json``, and applies
``researching → briefing_ready``. On any exception the orchestrator
applies ``researching → failed`` and records the failure into
state.json; the exception then re-raises **unchanged**.

Single catch-point
------------------

This module is the *only* catch site for :class:`RunawayTrapFired`
and :class:`AgentOutputInvalid`. The base agent never catches them
(see ``backend/agents/base.py`` design rule §2). The orchestrator's
catch is structural: it labels the failure and writes the artefact,
then re-raises so the caller (Step 8 briefing flow, the CLI, the
HTTP route) can decide what to surface to the operator.

Why no ``CloudClient`` import
-----------------------------

The orchestrator takes a :class:`CloudClientProtocol` — the same
structural type the agent layer uses. Construction of the real
:class:`backend.cost_control.cloud_client.CloudClient` happens
*outside* this module (in the route layer added in Step 9+) and
is injected. The static fence in
``tests/orchestrator/test_no_production_client_or_keychain.py``
enforces this: no ``anthropic``, no ``keyring``, no
``backend.credentials``, no ``CloudClient.for_production`` access.

The semaphore comes from :mod:`backend.cost_control.concurrency`
and is module-global; tests rebuild it via ``_reset_for_tests``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents._protocols import CloudClientProtocol
from backend.agents.output import AgentOutputInvalid
from backend.agents.research import ResearchAgent
from backend.agents.research_models import ResearchDossier
from backend.cost_control.concurrency import get_job_semaphore
from backend.cost_control.config_loader import Models
from backend.cost_control.exceptions import RunawayTrapFired
from backend.db.models import Job
from backend.jobs.state import JobState, apply_transition
from backend.jobs.storage import (
    JobNotFound,
    append_transition,
    record_failure,
    write_dossier,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure category labels
# ---------------------------------------------------------------------------

#: Stable category label written to ``state.json.last_error.category``
#: when a :class:`RunawayTrapFired` subclass propagates out of the agent.
_FAIL_TRAP: str = "runaway_trap"

#: Label for parse/validation failure after the wrapper's single retry.
_FAIL_OUTPUT_INVALID: str = "output_invalid"

#: Fallback label for any other ``Exception`` — bugs, OS errors, etc.
#: Deliberately distinct from ``runaway_trap`` so a crash is never
#: mis-attributed as a budget guard fire.
_FAIL_CRASHED: str = "crashed"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class JobNotInExpectedState(RuntimeError):
    """Raised when ``run_research`` is invoked on a job not at CREATED.

    Carries the actual state so callers can route the message. This
    is *not* a trap — it indicates a programming or operator error
    (the caller invoked the orchestrator twice, or out of order),
    not a runaway-cost event.
    """

    def __init__(self, *, job_id: str, actual: JobState) -> None:
        super().__init__(
            f"job {job_id} is in state {actual.value!r}, expected "
            f"{JobState.CREATED.value!r}"
        )
        self.job_id = job_id
        self.actual = actual


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ResearchOutcome:
    """Internal carrier for the result of one agent run.

    Successful runs carry the dossier; failed runs carry the exception
    and the failure category. Either way :meth:`_do_run_research` uses
    it to drive the post-call state writes uniformly.
    """

    dossier: ResearchDossier | None
    exc: BaseException | None
    category: str | None


class Orchestrator:
    """Drives a single research slice end-to-end.

    Dependencies are injected:

    ``client``
        A :class:`CloudClientProtocol` — usually the real
        :class:`CloudClient` in production, a hand-rolled fake in
        tests. The orchestrator never constructs one itself.
    ``models``
        The typed :class:`Models` dataclass with the model ids for
        each agent role.
    ``db_session_factory``
        Zero-arg callable returning a new :class:`Session`. The
        orchestrator opens a session per state-update transaction
        so a long-running agent call never holds a connection.
    """

    def __init__(
        self,
        *,
        client: CloudClientProtocol,
        models: Models,
        db_session_factory: Callable[[], Session],
    ) -> None:
        self._client = client
        self._models = models
        self._session_factory = db_session_factory

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_research(
        self,
        *,
        job_id: str,
        user_context: str | None = None,
    ) -> ResearchDossier:
        """Drive ``created → researching → briefing_ready | failed``.

        The semaphore is acquired *before* any state transition is
        applied — a job that has to queue stays at ``created`` in the
        database, which is the correct UI signal.

        Returns the validated :class:`ResearchDossier` on success.
        Re-raises any exception from the agent run unchanged; the
        caller is the single decision point for what happens next.
        """
        async with get_job_semaphore():
            return await self._do_run_research(
                job_id=job_id, user_context=user_context
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _do_run_research(
        self,
        *,
        job_id: str,
        user_context: str | None,
    ) -> ResearchDossier:
        """Slot-holding inner body. Assumes the semaphore is held."""
        state_file = self._load_state(job_id)
        if state_file_state := _state_file_state(state_file):
            if state_file_state is not JobState.CREATED:
                raise JobNotInExpectedState(
                    job_id=job_id, actual=state_file_state
                )

        company_name, company_url = self._read_intake_fields(state_file)

        # Transition 1: created → researching.
        # We apply this *before* the agent call so the row reflects
        # in-flight work the moment the semaphore slot is taken.
        record_created_to_researching = apply_transition(
            from_state=JobState.CREATED,
            to_state=JobState.RESEARCHING,
            reason="orchestrator pickup",
        )
        append_transition(job_id, record_created_to_researching)
        self._update_job_status(job_id, JobState.RESEARCHING)

        agent = ResearchAgent(client=self._client, models=self._models)

        try:
            # The agent's run() is synchronous (it makes one or more
            # blocking SDK calls). asyncio.to_thread keeps the event
            # loop free so the semaphore's other holders, the daily
            # budget poller, and any background work all keep
            # ticking.
            dossier = await asyncio.to_thread(
                agent.run,
                job_id=job_id,
                company_name=company_name,
                company_url=company_url,
                user_context=user_context,
            )
        except RunawayTrapFired as exc:
            self._record_research_failure(
                job_id=job_id,
                category=_FAIL_TRAP,
                exc=exc,
                trap_name=exc.__class__.__name__,
            )
            raise
        except AgentOutputInvalid as exc:
            self._record_research_failure(
                job_id=job_id,
                category=_FAIL_OUTPUT_INVALID,
                exc=exc,
                trap_name=None,
            )
            raise
        except Exception as exc:
            self._record_research_failure(
                job_id=job_id,
                category=_FAIL_CRASHED,
                exc=exc,
                trap_name=None,
            )
            raise

        # The agent returned a validated dossier. Persist it, then
        # apply the successful terminal transition.
        if not isinstance(dossier, ResearchDossier):
            # Defence-in-depth: BaseAgent.run is typed BaseModel; if
            # a subclass mis-declared ``output_model`` we'd surface a
            # wrong concrete type here. Treat the same as crashed.
            exc = TypeError(
                f"ResearchAgent returned {type(dossier).__name__}, "
                "expected ResearchDossier"
            )
            self._record_research_failure(
                job_id=job_id,
                category=_FAIL_CRASHED,
                exc=exc,
                trap_name=None,
            )
            raise exc

        write_dossier(job_id, dossier)
        record_researching_to_ready = apply_transition(
            from_state=JobState.RESEARCHING,
            to_state=JobState.BRIEFING_READY,
            reason="research dossier persisted",
        )
        append_transition(job_id, record_researching_to_ready)
        self._update_job_status(job_id, JobState.BRIEFING_READY)
        return dossier

    # ------------------------------------------------------------------
    # State / DB helpers
    # ------------------------------------------------------------------

    def _load_state(self, job_id: str):
        """Read ``state.json`` or raise :class:`JobNotFound`."""
        # Imported inside the method only to keep the module top-level
        # import block small; ``read_state`` is exported from storage.
        from backend.jobs.storage import read_state

        try:
            return read_state(job_id)
        except JobNotFound:
            raise

    @staticmethod
    def _read_intake_fields(state_file) -> tuple[str, str]:
        return state_file.company_name, state_file.company_url

    def _update_job_status(self, job_id: str, new_state: JobState) -> None:
        """Update ``Job.status`` in a fresh transaction.

        A short-lived session per write keeps the agent call (which
        can take minutes) from holding a connection. Failure here
        bubbles up — there is no recovery: the disk and DB
        projections of state must stay in sync.
        """
        with self._session_factory() as db:
            job = db.execute(
                select(Job).where(Job.id == job_id)
            ).scalar_one()
            job.status = new_state.value
            db.commit()

    def _record_research_failure(
        self,
        *,
        job_id: str,
        category: str,
        exc: BaseException,
        trap_name: str | None,
    ) -> None:
        """Apply researching→failed, write last_error, optionally bump
        trap_triggers.

        Best-effort: if a write fails here, log loudly but let the
        original exception propagate. Losing failure metadata is
        survivable; losing the *signal* that the job failed is not.
        """
        # Resolve a short, secret-safe message. Trap exceptions carry
        # a ``reason`` attribute by design; everything else falls back
        # to ``str(exc)``. We never call ``repr(exc)`` (which can leak
        # constructor state) or include traceback strings here.
        if isinstance(exc, RunawayTrapFired):
            details = exc.reason
        else:
            details = str(exc) or exc.__class__.__name__

        try:
            record = apply_transition(
                from_state=JobState.RESEARCHING,
                to_state=JobState.FAILED,
                reason=category,
            )
            append_transition(job_id, record)
        except Exception:
            log.exception(
                "failed to append researching→failed transition",
                extra={"job_id": job_id, "category": category},
            )

        try:
            record_failure(job_id, category=category, details=details)
        except Exception:
            log.exception(
                "failed to record last_error on state.json",
                extra={"job_id": job_id, "category": category},
            )

        try:
            with self._session_factory() as db:
                job = db.execute(
                    select(Job).where(Job.id == job_id)
                ).scalar_one()
                job.status = JobState.FAILED.value
                if trap_name is not None:
                    job.trap_triggers = _append_trap_trigger(
                        job.trap_triggers, trap_name
                    )
                db.commit()
        except Exception:
            log.exception(
                "failed to update Job row on failure",
                extra={"job_id": job_id, "category": category},
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_file_state(state_file) -> JobState | None:
    """Return the current_state, or ``None`` if state_file is falsy."""
    if state_file is None:
        return None
    return state_file.current_state


def _append_trap_trigger(existing: str | None, trap_name: str) -> str:
    """Return a JSON-array string with ``trap_name`` appended.

    The column stores a JSON array of trap class names (per
    ``backend/db/models.py`` comment). ``existing`` may be ``None``
    (no traps yet), an empty string, or a parseable JSON array. If
    parsing fails we start a fresh array — defence against legacy
    rows; never raise from inside the failure-recording path.
    """
    items: list[Any] = []
    if existing:
        try:
            decoded = json.loads(existing)
            if isinstance(decoded, list):
                items = list(decoded)
        except (ValueError, TypeError):
            items = []
    items.append(trap_name)
    return json.dumps(items)
