"""Job intake, state, and on-disk artifact storage.

Step 7a scope: infrastructure only. This package owns

* the canonical :class:`backend.jobs.state.JobState` enum (matches the
  handover §9.1 list exactly — no off-spec intermediate states)
* the transition primitive (``apply_transition``), wired with **no**
  edges yet — every input pair raises :class:`IllegalTransition`. Step
  7b activates the first edges
* job-folder layout under ``~/.ans-tool/jobs/{job_id}/`` (override via
  ``ANS_JOBS_ROOT`` env var, used by tests)
* atomic read/write of ``state.json`` and ``research_dossier.json``
* ``intake.create_job`` — validates the company name/URL, opens a
  database row at status ``created``, creates the folder, writes the
  initial ``state.json``

What this package deliberately does **not** do
----------------------------------------------

* No :class:`backend.agents.research.ResearchAgent` execution.
* No :class:`backend.cost_control.cloud_client.CloudClient` import or
  injection.
* No concurrent-job semaphore.
* No transition edges enabled.
* No writes to ``Job.trap_triggers``.
* No FastAPI routes.

The static fence
:func:`tests/jobs/test_no_keyring_or_credentials_imports.py`
enforces several of these constraints by AST-walking every file in
this package.
"""
