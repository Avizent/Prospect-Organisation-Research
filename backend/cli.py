"""CLI entry point for ans-tool.

Commands:
    ans-tool db upgrade          Run Alembic migrations to head
    ans-tool audit week          Spend + trap summary, exports CSV  [step 5]
    ans-tool stage1-dryrun       Fake Stage 1 dry-run with scripted responses
    ans-tool delete-contact <id> GDPR contact deletion              [step 14]
    ans-tool delete-company <id> GDPR company cascade delete        [step 14]
    ans-tool export-person <name> SAR export for a named individual [step 14]

Entry point registered in pyproject.toml: ans-tool = "backend.cli:main"

Design rule — defence-in-depth on the dry-run path
--------------------------------------------------

The ``stage1-dryrun`` command runs the real
:class:`backend.orchestrator.Orchestrator` against a *private* in-module
scripted client (:class:`_ScriptedCloudClient`). The scripted client is
deliberately confined to this file because it is a development /
CLI-only harness primitive, not part of production cost-control
infrastructure — putting it under ``backend/cost_control/`` would
create a misuse surface where production routes could accidentally
import it. The static fence
``tests/cli/test_no_cli_production_client_or_keychain.py`` proves
this module never imports the Anthropic SDK, ``keyring``,
``backend.credentials``, ``backend.delivery``, ``backend.assembly``,
``frontend``, or ``fastapi``, and never references ``CloudClient``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click


@click.group()
def main() -> None:
    """ANS Prospect Tool command-line interface."""


# ---------------------------------------------------------------------------
# db group
# ---------------------------------------------------------------------------
@main.group()
def db() -> None:
    """Database management commands."""


@db.command("upgrade")
def db_upgrade() -> None:
    """Apply all pending Alembic migrations to ~/.ans-tool/data.db."""
    from alembic.config import Config
    from alembic import command as alembic_command

    # Ensure the data directory exists before Alembic tries to open the DB.
    db_path = Path.home() / ".ans-tool" / "data.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Locate alembic.ini relative to this file (project root).
    ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini_path.exists():
        raise click.ClickException(f"alembic.ini not found at {ini_path}")

    cfg = Config(str(ini_path))
    alembic_command.upgrade(cfg, "head")
    click.echo(f"Database up to date: {db_path}")


# ---------------------------------------------------------------------------
# stage1-dryrun — scripted Stage 1 pipeline (Step 9c)
# ---------------------------------------------------------------------------
#
# The command and its private scripted client live together so the
# import-fence test can keep the surface narrow. Production routes
# never reach this code: it is not exported, and is referenced only
# by the CLI Click command below.

#: Allow-list of exception classes that a script entry may raise.
#: Built lazily inside :meth:`_ScriptedCloudClient._build_exception` so
#: this module's import-time surface stays minimal and the AST fence
#: test can be confident about what backend/cli.py touches.
_ALLOWED_SCRIPT_EXCEPTIONS: tuple[str, ...] = (
    # Cost-control traps the orchestrator knows how to label as
    # ``runaway_trap`` (and bump ``trap_triggers`` for).
    "JobBudgetExceeded",
    "DailyBudgetExceeded",
    "MonthlyBudgetExceeded",
    "MaxTokensExceeded",
    "ToolCallLimitExceeded",
    "CallTimeoutExceeded",
    "AgentTimeoutExceeded",
    "JobTimeoutExceeded",
    "CriticReviseLimitExceeded",
    "ConcurrentJobLimitExceeded",
    "UnknownAgent",
    "UnknownModel",
    # One generic exception so tests can exercise the orchestrator's
    # ``crashed`` failure category from the script too.
    "RuntimeError",
)


class _ScriptExhausted(RuntimeError):
    """The scripted client ran out of entries before the chain finished."""


class _MalformedScript(click.ClickException):
    """The script file could not be loaded or has the wrong shape."""

    exit_code = 2  # distinct from generic stage-1 failure (1)


class _ScriptedCloudClient:
    """Private dry-run replacement for :class:`CloudClient`.

    Implements only the single method the orchestrator (via the agent
    layer) calls — :meth:`messages_create` — and returns
    :class:`CloudCallResult` instances built from a JSON script.

    Each script entry is one of:

    * ``{"text": "<json>"}`` — wrap ``<json>`` in a single text block
      and return a :class:`CloudCallResult`. ``<json>`` is the string
      the agent's output parser will JSON-decode and Pydantic-validate.
    * ``{"raise": {"class": "<name>", "reason": "<short>"}}`` — raise
      ``<name>`` (looked up against
      :data:`_ALLOWED_SCRIPT_EXCEPTIONS`) with ``<reason>`` as the
      constructor message. The lookup is a string compare against an
      allow-list — never ``eval`` or dynamic import — so a malicious
      script cannot reach for arbitrary classes.

    Out of scope:

    * No SDK import. No Keychain read. No CloudClient construction.
      The static fence test enforces this.
    """

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries: list[dict[str, Any]] = list(entries)
        # ``calls`` is captured for future debugging surfaces but is not
        # surfaced by the CLI today — keeping it private avoids tempting
        # production code to reach in.
        self._calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Construction from disk
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path) -> "_ScriptedCloudClient":
        """Read a JSON list of entries from *path*.

        A malformed file (not JSON, not a list, entry missing both
        ``text`` and ``raise``) raises :class:`_MalformedScript` —
        which the Click runtime turns into a non-zero exit with a
        useful message, with no traceback leaking into stdout.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _MalformedScript(f"could not read script file: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _MalformedScript(
                f"script file is not valid JSON: {exc.msg} "
                f"(line {exc.lineno} col {exc.colno})"
            ) from exc

        if not isinstance(data, list):
            raise _MalformedScript(
                "script file must contain a JSON list; "
                f"got {type(data).__name__}"
            )

        # Validate each entry up front so an obviously-bad script is
        # rejected before any side-effects (state transitions, audit
        # writes) happen.
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise _MalformedScript(
                    f"script entry #{i} must be an object, "
                    f"got {type(entry).__name__}"
                )
            if "text" in entry and "raise" in entry:
                raise _MalformedScript(
                    f"script entry #{i} has both 'text' and 'raise'; "
                    "use one or the other"
                )
            if "text" not in entry and "raise" not in entry:
                raise _MalformedScript(
                    f"script entry #{i} must have 'text' or 'raise'"
                )
            if "text" in entry and not isinstance(entry["text"], str):
                raise _MalformedScript(
                    f"script entry #{i}: 'text' must be a string"
                )
            if "raise" in entry:
                spec = entry["raise"]
                if not isinstance(spec, dict):
                    raise _MalformedScript(
                        f"script entry #{i}: 'raise' must be an object"
                    )
                cls_name = spec.get("class")
                if not isinstance(cls_name, str) or not cls_name:
                    raise _MalformedScript(
                        f"script entry #{i}: 'raise.class' must be a "
                        "non-empty string"
                    )
                if cls_name not in _ALLOWED_SCRIPT_EXCEPTIONS:
                    raise _MalformedScript(
                        f"script entry #{i}: 'raise.class'={cls_name!r} "
                        "is not in the allow-list "
                        f"({', '.join(_ALLOWED_SCRIPT_EXCEPTIONS)})"
                    )

        return cls(data)

    # ------------------------------------------------------------------
    # CloudClientProtocol surface
    # ------------------------------------------------------------------

    def messages_create(self, **kwargs: Any):
        """Pop the next script entry and return / raise it.

        Signature matches
        :meth:`backend.cost_control.cloud_client.CloudClient.messages_create`
        structurally (we discard the kwargs — the scripted client does
        not enforce traps; the orchestrator path under test does).
        """
        self._calls.append(kwargs)
        if not self._entries:
            raise _ScriptExhausted(
                "scripted cloud client has no more entries; "
                "the chain called more agents than the script provides"
            )
        entry = self._entries.pop(0)
        if "raise" in entry:
            raise self._build_exception(entry["raise"])
        return self._build_success(entry["text"])

    # ------------------------------------------------------------------
    # Entry builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_success(text: str):
        """Wrap ``text`` in a :class:`CloudCallResult`.

        The result mirrors the dict shape ``CloudClient._extract_usage``
        and ``backend.agents.output.extract_text`` accept.
        """
        # Local import keeps the top of this module clean. The fence
        # test allows ``backend.cost_control.cloud_client`` as an import
        # because :class:`CloudCallResult` is the typed return surface;
        # what the fence forbids is any reference to the name
        # ``CloudClient`` (construction or attribute access).
        from backend.cost_control.cloud_client import CloudCallResult

        response = {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        return CloudCallResult(
            response=response,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

    @staticmethod
    def _build_exception(spec: dict[str, Any]) -> BaseException:
        """Look up an allowed exception class by name and instantiate it.

        The lookup is a single ``if/elif`` chain over the allow-list,
        so no dynamic import, no ``eval``, no ``getattr`` on a module.
        A class name not in the allow-list has already been rejected
        in :meth:`from_file`; the final ``else`` is defensive.
        """
        cls_name = spec["class"]
        reason = str(spec.get("reason") or "scripted failure")

        # Import the trap classes lazily — keeps top-of-module imports
        # narrow for the fence test.
        from backend.cost_control.exceptions import (
            AgentTimeoutExceeded,
            CallTimeoutExceeded,
            ConcurrentJobLimitExceeded,
            CriticReviseLimitExceeded,
            DailyBudgetExceeded,
            JobBudgetExceeded,
            JobTimeoutExceeded,
            MaxTokensExceeded,
            MonthlyBudgetExceeded,
            ToolCallLimitExceeded,
            UnknownAgent,
            UnknownModel,
        )

        if cls_name == "JobBudgetExceeded":
            return JobBudgetExceeded(reason)
        if cls_name == "DailyBudgetExceeded":
            return DailyBudgetExceeded(reason)
        if cls_name == "MonthlyBudgetExceeded":
            return MonthlyBudgetExceeded(reason)
        if cls_name == "MaxTokensExceeded":
            return MaxTokensExceeded(reason)
        if cls_name == "ToolCallLimitExceeded":
            return ToolCallLimitExceeded(reason)
        if cls_name == "CallTimeoutExceeded":
            return CallTimeoutExceeded(reason)
        if cls_name == "AgentTimeoutExceeded":
            return AgentTimeoutExceeded(reason)
        if cls_name == "JobTimeoutExceeded":
            return JobTimeoutExceeded(reason)
        if cls_name == "CriticReviseLimitExceeded":
            return CriticReviseLimitExceeded(reason)
        if cls_name == "ConcurrentJobLimitExceeded":
            return ConcurrentJobLimitExceeded(reason)
        if cls_name == "UnknownAgent":
            return UnknownAgent(reason)
        if cls_name == "UnknownModel":
            return UnknownModel(reason)
        if cls_name == "RuntimeError":
            return RuntimeError(reason)
        # Unreachable — from_file validates the name.
        raise _MalformedScript(  # pragma: no cover
            f"unknown exception class {cls_name!r}"
        )


@main.command("stage1-dryrun")
@click.option(
    "--job-id",
    "job_id",
    required=True,
    help="UUID of a job already at state 'created' (created via the "
    "intake module).",
)
@click.option(
    "--script",
    "script_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a JSON list of scripted responses.",
)
@click.option(
    "--user-context",
    "user_context",
    default=None,
    help="Optional free-text user context forwarded to every agent.",
)
@click.option(
    "--db-path",
    "db_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Override SQLite path. Defaults to ~/.ans-tool/data.db.",
)
def stage1_dryrun(
    job_id: str,
    script_path: Path,
    user_context: str | None,
    db_path: Path | None,
) -> None:
    """Run the full Stage 1 chain against a scripted fake client.

    No Anthropic API call is made. No Keychain is read. The command
    exists for end-to-end smoke testing the orchestrator + storage +
    state-machine path against a deterministic input.
    """
    # Build the scripted client first so a malformed script is
    # rejected before any DB or filesystem side effects.
    client = _ScriptedCloudClient.from_file(script_path)

    # Resolve DB path. We honour the CLI override so tests can point
    # at a tmp SQLite without monkeypatching $HOME.
    resolved_db = db_path or (Path.home() / ".ans-tool" / "data.db")
    if not resolved_db.exists():
        raise click.ClickException(
            f"database not found at {resolved_db}; "
            "run 'ans-tool db upgrade' first"
        )

    # Local imports so the fence test sees a narrow top-of-module
    # surface. Each import is justified inline.
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from backend.cost_control.config_loader import models as load_models
    from backend.jobs.storage import (
        JobNotFound,
        job_folder,
        read_state,
    )
    from backend.orchestrator import Orchestrator

    engine = sa.create_engine(
        f"sqlite:///{resolved_db}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False
    )

    orch = Orchestrator(
        client=client,
        models=load_models(),
        db_session_factory=session_factory,
    )

    try:
        asyncio.run(
            orch.run_stage1(job_id=job_id, user_context=user_context)
        )
    except Exception as exc:
        # The orchestrator has already written ``failed`` to state.json
        # and the DB by the time control reaches here (its failure
        # block runs before re-raising). We surface a short, secret-safe
        # summary on stderr and exit non-zero. We never log the
        # exception's repr — that can leak constructor state.
        cls_name = exc.__class__.__name__
        reason = getattr(exc, "reason", None) or str(exc) or cls_name
        click.echo(f"stage 1 failed: {cls_name}: {reason}", err=True)

        # Best-effort: pull last_error from state.json so the operator
        # can see what category the orchestrator labelled the failure.
        try:
            state = read_state(job_id)
            if state.last_error is not None:
                click.echo(
                    "last_error: "
                    f"category={state.last_error.get('category')!r} "
                    f"details={state.last_error.get('details')!r}",
                    err=True,
                )
        except JobNotFound:
            pass
        except Exception:  # pragma: no cover — defensive
            pass

        # List partial artefacts so the operator can see how far the
        # chain reached. We do not crash if the folder is missing.
        try:
            folder = job_folder(job_id)
            if folder.exists():
                present = sorted(p.name for p in folder.iterdir())
                click.echo(
                    f"artefacts present: {', '.join(present)}",
                    err=True,
                )
        except Exception:  # pragma: no cover — defensive
            pass

        raise click.exceptions.Exit(code=1)
    finally:
        engine.dispose()

    # Success path: print the final state and the four artefact paths.
    try:
        state = read_state(job_id)
        click.echo(f"state: {state.current_state.value}")
    except Exception:  # pragma: no cover — defensive
        pass

    folder = job_folder(job_id)
    for name in (
        "research_dossier.json",
        "contacts.json",
        "needs_assessment.json",
        "briefing.json",
    ):
        path = folder / name
        if path.exists():
            size = path.stat().st_size
            click.echo(f"{name}: {path} ({size} bytes)")
        else:
            click.echo(f"{name}: MISSING")


# ---------------------------------------------------------------------------
# Placeholders — implemented in step 14
# ---------------------------------------------------------------------------
@main.command("audit")
@click.argument("period", default="week")
def audit(period: str) -> None:
    """Show spend and trap summary. [not yet implemented]"""
    raise click.ClickException("audit command not yet implemented (step 5)")


@main.command("delete-contact")
@click.argument("contact_id", type=int)
def delete_contact(contact_id: int) -> None:
    """GDPR-compliant contact deletion. [not yet implemented]"""
    raise click.ClickException(
        "delete-contact not yet implemented (step 14)"
    )


@main.command("delete-company")
@click.argument("company_id", type=int)
def delete_company(company_id: int) -> None:
    """GDPR cascade company delete. [not yet implemented]"""
    raise click.ClickException(
        "delete-company not yet implemented (step 14)"
    )


@main.command("export-person")
@click.argument("name")
def export_person(name: str) -> None:
    """SAR export for a named individual. [not yet implemented]"""
    raise click.ClickException(
        "export-person not yet implemented (step 14)"
    )
