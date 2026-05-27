"""Audit and incident logging for the cost-control layer.

Two append-only logs live under :data:`ANS_HOME`:

  * ``audit/audit-YYYY-MM.jsonl`` — one row per *successful* Claude
    call. Schema matches the handover §"Audit logging" exactly:
    ``ts``, ``job_id``, ``agent``, ``model``, ``input_tokens``,
    ``output_tokens``, ``cost_usd``, ``prompt_hash``, ``approved_by``,
    ``budget_remaining_job``, ``budget_remaining_day``.
  * ``incidents.jsonl`` — one row per trap firing. Includes the trap
    name, severity, job/agent ids when known, a short ``reason``
    string, and a free-form ``context`` dict.

Both files are created with mode ``0o600`` and live inside a directory
created with mode ``0o700`` on first write. The logs are never read or
rewritten by application code — only appended to.

Secret hygiene
--------------
The :func:`write_audit` and :func:`write_incident` entry-point
signatures do not accept a raw API key, an Anthropic request body, or
a response body. Prompts are recorded as their SHA-256 hash only. The
trap-firing helper :func:`fire_trap` takes a short ``reason`` plus an
opt-in ``context`` mapping that callers are expected to pre-redact.

``ANS_HOME``
------------
Defaults to ``~/.ans-tool``. Tests monkey-patch this constant to point
at ``tmp_path`` so the real home directory is never touched.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.cost_control import notify
from backend.cost_control.exceptions import RunawayTrapFired


# ---------------------------------------------------------------------------
# Filesystem locations (monkey-patched in tests)
# ---------------------------------------------------------------------------

ANS_HOME: Path = Path.home() / ".ans-tool"
"""Root directory for all on-disk artefacts. Overridden in tests."""


def _audit_path() -> Path:
    """Return the JSONL path for the *current* month's audit log."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    return ANS_HOME / "audit" / f"audit-{stamp}.jsonl"


def _incident_path() -> Path:
    return ANS_HOME / "incidents.jsonl"


def _ensure_parent(path: Path) -> None:
    """Create the parent directory with mode ``0o700`` if missing."""
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        try:
            parent.chmod(0o700)
        except OSError:
            # Best-effort: chmod may fail on exotic filesystems; the
            # security posture is still better than a default umask.
            pass


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON row + newline. Creates the file 0o600 if needed.

    Never raises: audit/incident logging is observational and must not
    mask the underlying problem. Failures emit a one-line stderr note.
    """
    try:
        _ensure_parent(path)
        # Open with mode 0o600 on creation. ``open`` honours the umask,
        # so we explicitly chmod after touch for new files.
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
            fh.write("\n")
        if is_new:
            try:
                path.chmod(0o600)
            except OSError:
                pass
    except OSError as exc:
        print(f"warning: failed to append to {path}: {exc!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API — audit
# ---------------------------------------------------------------------------

def _utc_iso_now() -> str:
    """ISO-8601 UTC timestamp with second precision, ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_audit(
    *,
    job_id: str | None,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    prompt_hash: str,
    approved_by: str = "user",
    budget_remaining_job: float | None = None,
    budget_remaining_day: float | None = None,
) -> None:
    """Append one audit row for a *successful* Claude call.

    The ``prompt_hash`` argument is the only representation of the
    prompt that may appear in this log. Callers compute it by passing
    the request body through :func:`hash_prompt`.

    Never raises.
    """
    row: dict[str, Any] = {
        "ts": _utc_iso_now(),
        "job_id": job_id,
        "agent": agent,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": round(float(cost_usd), 6),
        "prompt_hash": prompt_hash,
        "approved_by": approved_by,
        "budget_remaining_job": (
            None if budget_remaining_job is None
            else round(float(budget_remaining_job), 6)
        ),
        "budget_remaining_day": (
            None if budget_remaining_day is None
            else round(float(budget_remaining_day), 6)
        ),
    }
    _append_jsonl(_audit_path(), row)


# ---------------------------------------------------------------------------
# Public API — incidents
# ---------------------------------------------------------------------------

def write_incident(
    *,
    trap: str,
    severity: str,
    job_id: str | None,
    agent: str | None,
    reason: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Append one incident row. Never raises."""
    row: dict[str, Any] = {
        "ts": _utc_iso_now(),
        "trap": trap,
        "severity": severity,
        "job_id": job_id,
        "agent": agent,
        "reason": reason,
        "context": dict(context or {}),
    }
    _append_jsonl(_incident_path(), row)


# ---------------------------------------------------------------------------
# Central trap-firing helper
# ---------------------------------------------------------------------------

def fire_trap(
    exc: RunawayTrapFired,
    *,
    update_job_callback=None,
) -> None:
    """Record a trap firing and notify the user, then re-raise.

    Call sites raise their typed exception, then ``fire_trap`` writes
    the incident row, sends the notification, optionally appends the
    trap name to ``jobs.trap_triggers`` via the supplied callback, and
    re-raises the exception so the orchestrator can unwind.

    ``update_job_callback`` (when provided) is invoked as
    ``update_job_callback(exc.job_id, exc.trap)`` and is allowed to
    raise; its exception is logged but not propagated.
    """
    write_incident(
        trap=exc.trap,
        severity=exc.severity,
        job_id=exc.job_id,
        agent=exc.agent,
        reason=exc.reason,
        context=exc.context,
    )
    # Notification is best-effort.
    try:
        notify.notify(
            title="ANS Prospect Tool — runaway trap fired",
            body=f"{exc.trap}: {exc.reason}",
        )
    except Exception as note_exc:  # pragma: no cover — notify never raises
        print(
            f"warning: notify suppressed exception during fire_trap: {note_exc!r}",
            file=sys.stderr,
        )

    if update_job_callback is not None and exc.job_id is not None:
        try:
            update_job_callback(exc.job_id, exc.trap)
        except Exception as cb_exc:
            print(
                f"warning: trap-trigger DB update failed: {cb_exc!r}",
                file=sys.stderr,
            )

    raise exc


# ---------------------------------------------------------------------------
# Prompt hashing (the only representation of a prompt that may leave RAM).
# ---------------------------------------------------------------------------

def hash_prompt(messages: Any) -> str:
    """Return ``sha256:<hex>`` for a deterministic JSON encoding of *messages*.

    Uses ``sort_keys=True`` and the most compact separators so the hash
    is stable across SDK versions and Python dict ordering.
    """
    import hashlib

    canonical = json.dumps(
        messages,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
