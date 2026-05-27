"""SQLAlchemy ORM models.

Tables: companies, contacts, jobs, settings, password_resets,
        login_attempts, sessions.

Schema matches the SQL in the handover document exactly.
Database location: ~/.ans-tool/data.db

IMPORTANT — settings table usage:
  Permitted keys: auth.username, auth.password_hash, auth.recovery_email,
                  auth.setup_completed_at, app.* (non-secret config).
  Prohibited keys: any API key, M365 client secret, Anthropic key,
                   refresh token, or access token. Those belong in macOS
                   Keychain only (via keyring), never in the database.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enable FK enforcement for every SQLite connection.
# SQLite disables foreign keys by default; this pragma turns them on.
# ---------------------------------------------------------------------------
@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ---------------------------------------------------------------------------
# companies
# ---------------------------------------------------------------------------
class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    website_url = Column(Text)
    sector = Column(Text)
    sub_sector = Column(Text)
    headcount = Column(Integer)
    headcount_source = Column(Text)
    headcount_as_of = Column(Date)
    revenue_band = Column(Text)
    hq_country = Column(Text)
    hq_city = Column(Text)
    ownership = Column(Text)
    parent_company = Column(Text)
    one_line_desc = Column(Text)
    lab_maturity = Column(Text)
    first_researched_at = Column(DateTime, nullable=False)
    last_researched_at = Column(DateTime, nullable=False)
    research_count = Column(Integer, default=1)
    last_job_id = Column(Text)
    notes = Column(Text)

    contacts = relationship(
        "Contact",
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    jobs = relationship("Job", back_populates="company")

    __table_args__ = (UniqueConstraint("name", "website_url"),)


# ---------------------------------------------------------------------------
# contacts
# ---------------------------------------------------------------------------
class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(Text, nullable=False)
    job_title = Column(Text)
    country = Column(Text)
    linkedin_url = Column(Text)
    email = Column(Text)
    phone = Column(Text)
    mobile = Column(Text)
    seniority = Column(Text)
    function = Column(Text)
    source = Column(Text)
    confidence = Column(Text)
    notes = Column(Text)
    do_not_contact = Column(Boolean, default=False)
    dnc_set_at = Column(DateTime)
    dnc_reason = Column(Text)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    company = relationship("Company", back_populates="contacts")

    __table_args__ = (UniqueConstraint("company_id", "name", "job_title"),)


# ---------------------------------------------------------------------------
# jobs
# trap_triggers: TEXT storing a JSON array of trigger descriptions.
# ---------------------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id = Column(Text, primary_key=True)  # UUID string
    company_id = Column(Integer, ForeignKey("companies.id"))
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    status = Column(Text, nullable=False)
    depth = Column(Text)
    cost_usd = Column(Float, default=0.0)
    trap_triggers = Column(Text)  # JSON array string; NULL if no traps fired
    delivery_status = Column(Text)
    delivered_at = Column(DateTime)
    folder_path = Column(Text)

    company = relationship("Company", back_populates="jobs")


# ---------------------------------------------------------------------------
# settings
# Key-value store for non-secret app configuration.
# Permitted keys: auth.username, auth.password_hash, auth.recovery_email,
#                 auth.setup_completed_at, app.* (theme, density, etc.)
# Prohibited: any external credential — use macOS Keychain instead.
# ---------------------------------------------------------------------------
class Setting(Base):
    __tablename__ = "settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False)


# ---------------------------------------------------------------------------
# password_resets
# token_hash: SHA-256 hex digest of the single-use reset token.
# The raw token is emailed; only its hash is persisted here.
# ---------------------------------------------------------------------------
class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)  # SHA-256 hex
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False)
    created_ip = Column(Text)


# ---------------------------------------------------------------------------
# login_attempts
# ---------------------------------------------------------------------------
class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text)
    ip = Column(Text, nullable=False)
    success = Column(Boolean, nullable=False)
    attempted_at = Column(DateTime, nullable=False)


# ---------------------------------------------------------------------------
# sessions
# id: 32-byte urlsafe random token (the session cookie value).
# ---------------------------------------------------------------------------
class Session(Base):
    __tablename__ = "sessions"

    id = Column(Text, primary_key=True)  # 32-byte urlsafe random token
    username = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_activity = Column(DateTime, nullable=False)
    ip = Column(Text)
    user_agent = Column(Text)
