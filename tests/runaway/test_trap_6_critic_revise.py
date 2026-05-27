"""Trap 6 — critic-revise loop bound.

The :class:`CriticReviseLimiter` raises on the *second* revise call
for the same (job, agent) — matching the handover rule that at most
one revision pass is permitted before shipping v2.
"""

from __future__ import annotations

import pytest

from backend.cost_control import CriticReviseLimitExceeded, CriticReviseLimiter


def test_first_revise_is_allowed():
    limiter = CriticReviseLimiter()
    limiter.attempt(job_id="j1", agent="benefits_writer")
    assert limiter.revisions_taken(job_id="j1", agent="benefits_writer") == 1


def test_second_revise_for_same_agent_raises():
    limiter = CriticReviseLimiter()
    limiter.attempt(job_id="j1", agent="benefits_writer")
    with pytest.raises(CriticReviseLimitExceeded):
        limiter.attempt(job_id="j1", agent="benefits_writer")


def test_separate_agents_have_separate_counters():
    limiter = CriticReviseLimiter()
    limiter.attempt(job_id="j1", agent="benefits_writer")
    limiter.attempt(job_id="j1", agent="faq_writer")  # different agent → ok
    assert limiter.revisions_taken(job_id="j1", agent="benefits_writer") == 1
    assert limiter.revisions_taken(job_id="j1", agent="faq_writer") == 1


def test_separate_jobs_have_separate_counters():
    limiter = CriticReviseLimiter()
    limiter.attempt(job_id="j1", agent="benefits_writer")
    limiter.attempt(job_id="j2", agent="benefits_writer")
    assert limiter.revisions_taken(job_id="j2", agent="benefits_writer") == 1


def test_max_zero_blocks_any_revise():
    limiter = CriticReviseLimiter(max_revisions=0)
    with pytest.raises(CriticReviseLimitExceeded):
        limiter.attempt(job_id="j1", agent="benefits_writer")


def test_negative_max_is_rejected():
    with pytest.raises(ValueError):
        CriticReviseLimiter(max_revisions=-1)
