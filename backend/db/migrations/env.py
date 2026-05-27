"""Alembic environment configuration.

Database URL resolution order:
  1. If alembic.ini sqlalchemy.url is set to something other than the
     sentinel value "placeholder", use it as-is (allows test overrides
     via alembic_cfg.set_main_option("sqlalchemy.url", ...)).
  2. Otherwise, default to sqlite:///~/.ans-tool/data.db.

target_metadata is set to Base.metadata so that future
`alembic revision --autogenerate` commands work correctly.
"""

from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import models so that Base.metadata is fully populated.
from backend.db.models import Base  # noqa: F401

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    """Return the SQLite URL, honouring test overrides."""
    url = config.get_main_option("sqlalchemy.url", "placeholder")
    if url and url != "placeholder":
        return url
    db_path = Path.home() / ".ans-tool" / "data.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (SQL script mode)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
