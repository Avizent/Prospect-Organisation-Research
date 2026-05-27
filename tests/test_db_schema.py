"""Step 2 verification: database schema and migration tests.

Confirms:
  1. ~/.ans-tool/ directory can be created (via a temp path equivalent).
  2. Alembic migration 001 applies cleanly to a fresh SQLite database.
  3. All seven expected tables exist after migration.
  4. All expected indexes exist.
  5. The settings table does not define any column that would encourage
     storing external API credentials (structural check only — runtime
     enforcement is in application code).
  6. downgrade() removes all tables cleanly.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config


EXPECTED_TABLES = {
    "companies",
    "contacts",
    "jobs",
    "settings",
    "password_resets",
    "login_attempts",
    "sessions",
}

EXPECTED_INDEXES = {
    "companies": {"idx_companies_name", "idx_companies_last_researched"},
    "contacts": {
        "idx_contacts_company",
        "idx_contacts_name",
        "idx_contacts_linkedin",
    },
    "jobs": {"idx_jobs_company", "idx_jobs_started"},
    "password_resets": {
        "idx_password_resets_token",
        "idx_password_resets_expires",
    },
    "login_attempts": {"idx_login_attempts_ip_time"},
    "sessions": {"idx_sessions_username", "idx_sessions_expires"},
}


def _make_alembic_cfg(db_path: Path) -> Config:
    """Build an Alembic config pointing at a test database."""
    ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def migrated_db(tmp_path):
    """Yield (engine, db_path) after applying migrations to a temp DB."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    alembic_command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    yield engine, db_path
    engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_db_directory_is_writable(tmp_path):
    """A SQLite database can be created and written to in a new directory."""
    db_dir = tmp_path / ".ans-tool"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "data.db"

    engine = sa.create_engine(f"sqlite:///{db_path}")
    # A committed write transaction forces SQLite to flush the file to disk.
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE _write_test (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("DROP TABLE _write_test"))
    engine.dispose()

    assert db_path.exists(), "DB file was not created"
    assert db_path.stat().st_size > 0, "DB file has no content after write"


def test_migration_applies_cleanly(tmp_path):
    """Alembic upgrade head runs without errors on a fresh database."""
    db_path = tmp_path / "migration_test.db"
    cfg = _make_alembic_cfg(db_path)
    # Should not raise
    alembic_command.upgrade(cfg, "head")
    assert db_path.exists()


def test_all_tables_exist(migrated_db):
    """All seven expected tables are present after migration."""
    engine, _ = migrated_db
    inspector = sa.inspect(engine)
    actual_tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - actual_tables
    assert not missing, f"Missing tables after migration: {missing}"


def test_no_extra_tables(migrated_db):
    """No unexpected tables appear (catches alembic_version + our 7)."""
    engine, _ = migrated_db
    inspector = sa.inspect(engine)
    actual_tables = set(inspector.get_table_names())
    # alembic_version is expected; subtract it and our 7 tables
    unexpected = actual_tables - EXPECTED_TABLES - {"alembic_version"}
    assert not unexpected, f"Unexpected tables: {unexpected}"


def test_all_indexes_exist(migrated_db):
    """All specified indexes are present on their respective tables."""
    engine, _ = migrated_db
    inspector = sa.inspect(engine)
    missing = {}
    for table, expected_idx in EXPECTED_INDEXES.items():
        actual_idx = {i["name"] for i in inspector.get_indexes(table)}
        absent = expected_idx - actual_idx
        if absent:
            missing[table] = absent
    assert not missing, f"Missing indexes: {missing}"


def test_companies_unique_constraint(migrated_db):
    """companies(name, website_url) unique constraint is enforced."""
    engine, _ = migrated_db
    from datetime import datetime
    now = datetime(2026, 1, 1, 12, 0, 0)
    row = {
        "name": "Acme Corp",
        "website_url": "https://acme.example",
        "first_researched_at": now,
        "last_researched_at": now,
    }
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO companies (name, website_url, first_researched_at, "
            "last_researched_at) VALUES (:name, :website_url, "
            ":first_researched_at, :last_researched_at)"
        ), row)
        with pytest.raises(Exception):
            conn.execute(sa.text(
                "INSERT INTO companies (name, website_url, first_researched_at,"
                " last_researched_at) VALUES (:name, :website_url, "
                ":first_researched_at, :last_researched_at)"
            ), row)


def test_contacts_cascade_delete(migrated_db):
    """Deleting a company cascades to its contacts (PRAGMA foreign_keys=ON)."""
    engine, _ = migrated_db
    from datetime import datetime
    now = datetime(2026, 1, 1, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        conn.execute(sa.text(
            "INSERT INTO companies (id, name, first_researched_at, "
            "last_researched_at) VALUES (1, 'TestCo', :now, :now)"
        ), {"now": now})
        conn.execute(sa.text(
            "INSERT INTO contacts (company_id, name, created_at, updated_at) "
            "VALUES (1, 'Jane Smith', :now, :now)"
        ), {"now": now})

    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        conn.execute(sa.text("DELETE FROM companies WHERE id = 1"))

    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT COUNT(*) FROM contacts WHERE company_id = 1")
        ).scalar()
    assert result == 0, "Contact not deleted when company was removed"


def test_downgrade_removes_all_tables(tmp_path):
    """downgrade() to base removes all application tables."""
    db_path = tmp_path / "downgrade_test.db"
    cfg = _make_alembic_cfg(db_path)
    alembic_command.upgrade(cfg, "head")
    alembic_command.downgrade(cfg, "base")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    engine.dispose()
    assert not tables, f"Tables remain after downgrade: {tables}"


def test_settings_columns_do_not_include_secret_fields(migrated_db):
    """settings table columns are limited to key/value/updated_at.

    This is a structural guard: the schema must not grow columns that
    would encourage storing external credentials in the database.
    Permitted runtime keys (auth.password_hash etc.) are enforced in
    application code, not schema.
    """
    engine, _ = migrated_db
    inspector = sa.inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("settings")}
    assert cols == {"key", "value", "updated_at"}, (
        f"Unexpected settings columns: {cols}"
    )
