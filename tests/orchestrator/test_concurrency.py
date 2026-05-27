"""Trap 7 — concurrent-job semaphore — orchestrator-level tests.

The semaphore is module-global in
:mod:`backend.cost_control.concurrency`. The conftest's autouse
``_reset_job_semaphore`` resets it before each test; these tests
shrink it to ``max_jobs=1`` so the queuing behaviour is observable.

Three assertions are pinned here:

* ``run_research`` ``await``\\ s on the semaphore — when one slot
  is held, a second invocation blocks until the first releases.
* The semaphore is released on **failure** too (so a stuck job
  doesn't permanently consume a slot).
* The semaphore is released on **success** (so jobs serialize
  cleanly with ``max_jobs=1`` and the second's transitions look
  exactly like the first's).

Following the project convention (see ``tests/runaway/test_trap_7_
concurrent_jobs.py``), each async body is wrapped in
``asyncio.run(...)`` inside a sync test function rather than
relying on ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.research_models import ResearchDossier
from backend.cost_control import concurrency as _concurrency
from backend.cost_control.config_loader import Models
from backend.cost_control.exceptions import (
    JobBudgetExceeded,
    RunawayTrapFired,
)
from backend.db.models import Job
from backend.jobs.intake import create_job
from backend.jobs.state import JobState
from backend.orchestrator import Orchestrator
from tests.orchestrator.conftest import FakeCloudClient, make_response


def _dossier_response(dossier: ResearchDossier) -> object:
    return make_response(text=dossier.model_dump_json())


def _make_two_jobs(
    db_session: Session, when: datetime
) -> tuple[str, str]:
    a = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        now=when,
    )
    b = create_job(
        db=db_session,
        company_name="Beta Ltd",
        company_url="https://beta.example.com/",
        now=when,
    )
    return a.job_id, b.job_id


# ---------------------------------------------------------------------------
# Blocking semantics
# ---------------------------------------------------------------------------

def test_second_research_blocks_until_first_releases(
    db_session: Session,
    isolated_jobs_root: Path,
    session_factory,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    """With ``max_jobs=1``, the second ``run_research`` cannot proceed
    while the first holds the slot — we drive the first to a known
    pause via a threading.Event and assert the second hasn't called
    its agent yet."""
    _concurrency._reset_for_tests(max_jobs=1)

    # Threading-safe signals because the agent runs on a worker thread
    # (asyncio.to_thread). The agent thread sets ``started`` to tell
    # the loop "I'm inside the agent call", then blocks on ``release``.
    started = threading.Event()
    release = threading.Event()

    class BlockingClient:
        """A CloudClientProtocol fake that suspends until released."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def messages_create(self, **kwargs):
            self.calls.append(kwargs)
            started.set()
            # Bounded wait — the test will set ``release`` long before.
            if not release.wait(timeout=5.0):
                raise AssertionError(
                    "BlockingClient was never released by the test"
                )
            return make_response(text=sample_dossier.model_dump_json())

    blocking = BlockingClient()
    fast_client = FakeCloudClient()
    fast_client.script([_dossier_response(sample_dossier)])

    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    job_a, job_b = _make_two_jobs(db_session, when)

    orch_a = Orchestrator(
        client=blocking,
        models=test_models,
        db_session_factory=session_factory,
    )
    orch_b = Orchestrator(
        client=fast_client,
        models=test_models,
        db_session_factory=session_factory,
    )

    async def main() -> None:
        task_a = asyncio.create_task(orch_a.run_research(job_id=job_a))
        # Wait until the blocking agent has actually entered
        # messages_create — at this point job A holds the only slot.
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        assert started.is_set(), "blocking agent never entered messages_create"

        # Now fire job B. It should NOT make progress past the
        # semaphore acquire while A still holds it.
        task_b = asyncio.create_task(orch_b.run_research(job_id=job_b))
        # Give B a tick of opportunity to make progress (none expected).
        await asyncio.sleep(0.1)
        assert fast_client.calls == [], (
            "fast_client called while job A still holds the slot"
        )

        # Release A; both should complete.
        release.set()
        await asyncio.wait_for(task_a, timeout=5.0)
        await asyncio.wait_for(task_b, timeout=5.0)
        assert len(fast_client.calls) == 1

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Slot is released on failure
# ---------------------------------------------------------------------------

def test_slot_released_after_failed_run(
    db_session: Session,
    isolated_jobs_root: Path,
    session_factory,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    """A failed run must release the semaphore — otherwise one
    failure would permanently shrink concurrency by 1."""
    _concurrency._reset_for_tests(max_jobs=1)

    failing = FakeCloudClient()
    failing.script([JobBudgetExceeded("would exceed per-job budget")])
    second = FakeCloudClient()
    second.script([_dossier_response(sample_dossier)])

    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    job_a, job_b = _make_two_jobs(db_session, when)

    orch_a = Orchestrator(
        client=failing,
        models=test_models,
        db_session_factory=session_factory,
    )
    orch_b = Orchestrator(
        client=second,
        models=test_models,
        db_session_factory=session_factory,
    )

    async def main() -> None:
        with pytest.raises(RunawayTrapFired):
            await orch_a.run_research(job_id=job_a)
        # If the slot were leaked, this would hang. Bound it.
        await asyncio.wait_for(orch_b.run_research(job_id=job_b), timeout=5.0)

    asyncio.run(main())

    db_session.expire_all()
    job_b_row = db_session.execute(
        select(Job).where(Job.id == job_b)
    ).scalar_one()
    assert job_b_row.status == JobState.BRIEFING_READY.value


# ---------------------------------------------------------------------------
# Slot is released on success → serial execution still works
# ---------------------------------------------------------------------------

def test_two_successful_runs_serialize_under_max_one(
    db_session: Session,
    isolated_jobs_root: Path,
    session_factory,
    test_models: Models,
    sample_dossier: ResearchDossier,
) -> None:
    _concurrency._reset_for_tests(max_jobs=1)
    client_a = FakeCloudClient()
    client_a.script([_dossier_response(sample_dossier)])
    client_b = FakeCloudClient()
    client_b.script([_dossier_response(sample_dossier)])

    when = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    job_a, job_b = _make_two_jobs(db_session, when)

    orch_a = Orchestrator(
        client=client_a,
        models=test_models,
        db_session_factory=session_factory,
    )
    orch_b = Orchestrator(
        client=client_b,
        models=test_models,
        db_session_factory=session_factory,
    )

    async def main() -> None:
        # Run them concurrently; the semaphore serializes the agent
        # calls but both should still complete successfully.
        await asyncio.gather(
            orch_a.run_research(job_id=job_a),
            orch_b.run_research(job_id=job_b),
        )

    asyncio.run(main())

    db_session.expire_all()
    a_row = db_session.execute(
        select(Job).where(Job.id == job_a)
    ).scalar_one()
    b_row = db_session.execute(
        select(Job).where(Job.id == job_b)
    ).scalar_one()
    assert a_row.status == JobState.BRIEFING_READY.value
    assert b_row.status == JobState.BRIEFING_READY.value

    # Both state.jsons carry the two-edge transition history.
    for jid in (job_a, job_b):
        payload = json.loads(
            (isolated_jobs_root / jid / "state.json").read_text("utf-8")
        )
        edges = [(t["from"], t["to"]) for t in payload["transitions"]]
        assert edges == [
            ("created", "researching"),
            ("researching", "briefing_ready"),
        ]
