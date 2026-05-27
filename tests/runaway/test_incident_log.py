"""Incident JSONL — appended on every trap firing; notification fires too."""

from __future__ import annotations

import json

import pytest

from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetGuard,
    MaxTokensExceeded,
)
from backend.cost_control import audit as audit_mod


def _incident_rows() -> list[dict]:
    path = audit_mod.ANS_HOME / "incidents.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_fire_trap_writes_incident_and_notifies(notify_recorder):
    exc = MaxTokensExceeded(
        "agent 'research' requested max_tokens=99999 > ceiling 4000",
        job_id="jid-7",
        agent="research",
        context={"agent": "research", "max_tokens": 99999, "ceiling": 4000},
    )
    with pytest.raises(MaxTokensExceeded):
        audit_mod.fire_trap(exc)

    rows = _incident_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["trap"] == "MaxTokensExceeded"
    assert row["severity"] == "high"
    assert row["job_id"] == "jid-7"
    assert row["agent"] == "research"
    assert row["context"]["ceiling"] == 4000

    # Notification fired with the trap name in the body.
    assert len(notify_recorder.calls) == 1
    assert "MaxTokensExceeded" in notify_recorder.calls[0].body


def test_cloud_client_trap_writes_incident(
    fake_sdk, budget_state: BudgetState, db_session, job_id, notify_recorder
):
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )
    with pytest.raises(MaxTokensExceeded):
        client.messages_create(
            agent="critic",
            model="claude-haiku-4-5",
            job_id=job_id,
            messages=[{"role": "user", "content": "x"}],
            max_tokens=99_999,  # way above critic's ceiling 2000
            input_tokens_estimate=10,
        )

    rows = _incident_rows()
    assert len(rows) == 1
    assert rows[0]["trap"] == "MaxTokensExceeded"
    assert rows[0]["job_id"] == job_id
    assert len(notify_recorder.calls) == 1


def test_incident_file_mode_is_0o600(notify_recorder):
    exc = MaxTokensExceeded("x", job_id="jid", agent="research", context={})
    with pytest.raises(MaxTokensExceeded):
        audit_mod.fire_trap(exc)
    path = audit_mod.ANS_HOME / "incidents.jsonl"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_trap_trigger_callback_invoked(notify_recorder):
    calls: list[tuple] = []

    def cb(job_id: str, trap: str) -> None:
        calls.append((job_id, trap))

    exc = MaxTokensExceeded("x", job_id="jid-9", agent="needs", context={})
    with pytest.raises(MaxTokensExceeded):
        audit_mod.fire_trap(exc, update_job_callback=cb)
    assert calls == [("jid-9", "MaxTokensExceeded")]


def test_trap_trigger_callback_exception_does_not_mask_trap(notify_recorder):
    def boom(job_id: str, trap: str) -> None:
        raise RuntimeError("DB blew up")

    exc = MaxTokensExceeded("x", job_id="jid-10", agent="needs", context={})
    # The trap exception must still be the one raised; the callback
    # failure is logged but not propagated.
    with pytest.raises(MaxTokensExceeded):
        audit_mod.fire_trap(exc, update_job_callback=boom)
