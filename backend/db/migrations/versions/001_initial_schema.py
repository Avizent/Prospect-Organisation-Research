"""Initial schema: all seven tables and indexes.

Revision ID: 001
Revises: None
Create Date: 2026-05-27

Schema matches the SQL specified in ans-prospect-tool-handover.md exactly.
SQLite-specific indexes (COLLATE NOCASE, DESC, partial WHERE) are created
via op.execute() since Alembic's create_index does not natively support all
these variants for SQLite.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # companies
    # ------------------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website_url", sa.Text()),
        sa.Column("sector", sa.Text()),
        sa.Column("sub_sector", sa.Text()),
        sa.Column("headcount", sa.Integer()),
        sa.Column("headcount_source", sa.Text()),
        sa.Column("headcount_as_of", sa.Date()),
        sa.Column("revenue_band", sa.Text()),
        sa.Column("hq_country", sa.Text()),
        sa.Column("hq_city", sa.Text()),
        sa.Column("ownership", sa.Text()),
        sa.Column("parent_company", sa.Text()),
        sa.Column("one_line_desc", sa.Text()),
        sa.Column("lab_maturity", sa.Text()),
        sa.Column("first_researched_at", sa.DateTime(), nullable=False),
        sa.Column("last_researched_at", sa.DateTime(), nullable=False),
        sa.Column("research_count", sa.Integer(), server_default="1"),
        sa.Column("last_job_id", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("name", "website_url"),
    )
    # COLLATE NOCASE requires raw SQL on SQLite
    op.execute(
        "CREATE INDEX idx_companies_name "
        "ON companies(name COLLATE NOCASE)"
    )
    # Descending index requires raw SQL on SQLite
    op.execute(
        "CREATE INDEX idx_companies_last_researched "
        "ON companies(last_researched_at DESC)"
    )

    # ------------------------------------------------------------------
    # contacts
    # ------------------------------------------------------------------
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("job_title", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("linkedin_url", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("mobile", sa.Text()),
        sa.Column("seniority", sa.Text()),
        sa.Column("function", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.Column("confidence", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("do_not_contact", sa.Boolean(), server_default="0"),
        sa.Column("dnc_set_at", sa.DateTime()),
        sa.Column("dnc_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "name", "job_title"),
    )
    op.create_index("idx_contacts_company", "contacts", ["company_id"])
    op.execute(
        "CREATE INDEX idx_contacts_name "
        "ON contacts(name COLLATE NOCASE)"
    )
    # Partial index requires raw SQL on SQLite
    op.execute(
        "CREATE INDEX idx_contacts_linkedin "
        "ON contacts(linkedin_url) "
        "WHERE linkedin_url IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # jobs
    # trap_triggers: TEXT storing a JSON array; NULL if no traps fired.
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id")),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("depth", sa.Text()),
        sa.Column("cost_usd", sa.Float(), server_default="0"),
        sa.Column("trap_triggers", sa.Text()),
        sa.Column("delivery_status", sa.Text()),
        sa.Column("delivered_at", sa.DateTime()),
        sa.Column("folder_path", sa.Text()),
    )
    op.create_index("idx_jobs_company", "jobs", ["company_id"])
    op.execute(
        "CREATE INDEX idx_jobs_started ON jobs(started_at DESC)"
    )

    # ------------------------------------------------------------------
    # settings
    # Key-value store for non-secret app configuration only.
    # Permitted: auth.username, auth.password_hash, auth.recovery_email,
    #            auth.setup_completed_at, app.* settings.
    # Prohibited: any external API credential — use macOS Keychain instead.
    # ------------------------------------------------------------------
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ------------------------------------------------------------------
    # password_resets
    # token_hash: SHA-256 hex of the single-use reset token.
    # The raw token is emailed; only its hash is stored here.
    # ------------------------------------------------------------------
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_ip", sa.Text()),
    )
    op.create_index(
        "idx_password_resets_token", "password_resets", ["token_hash"]
    )
    op.create_index(
        "idx_password_resets_expires", "password_resets", ["expires_at"]
    )

    # ------------------------------------------------------------------
    # login_attempts
    # ------------------------------------------------------------------
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text()),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "idx_login_attempts_ip_time",
        "login_attempts",
        ["ip", "attempted_at"],
    )

    # ------------------------------------------------------------------
    # sessions
    # id: 32-byte urlsafe random token (the session cookie value).
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity", sa.DateTime(), nullable=False),
        sa.Column("ip", sa.Text()),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("idx_sessions_username", "sessions", ["username"])
    op.create_index("idx_sessions_expires", "sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("login_attempts")
    op.drop_table("password_resets")
    op.drop_table("settings")
    op.drop_table("jobs")
    op.drop_table("contacts")
    op.drop_table("companies")
