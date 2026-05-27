"""CLI entry point for ans-tool.

Commands:
    ans-tool db upgrade          Run Alembic migrations to head
    ans-tool audit week          Spend + trap summary, exports CSV  [step 5]
    ans-tool delete-contact <id> GDPR contact deletion              [step 14]
    ans-tool delete-company <id> GDPR company cascade delete        [step 14]
    ans-tool export-person <name> SAR export for a named individual [step 14]

Entry point registered in pyproject.toml: ans-tool = "backend.cli:main"
"""

from pathlib import Path

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
