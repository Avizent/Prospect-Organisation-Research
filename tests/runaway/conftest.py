"""Shared fixtures for tests/runaway/.

Every test gets:
  * ``ANS_HOME`` redirected into ``tmp_path`` (no writes to ``~/.ans-tool``)
  * an in-memory keyring (no writes to the real macOS Keychain)
  * notifications disabled (``ANS_DISABLE_NOTIFICATIONS=1``) **and**
    :func:`backend.cost_control.notify.notify` patched to a recorder
  * a fresh SQLite database under ``tmp_path`` with the production
    schema migrated to head
  * a tear-down hook that resets the global concurrency semaphore so
    one test's leaks cannot poison the next test

No test in this suite is allowed to touch the real Anthropic SDK
client. The fence test additionally proves that no production module
outside ``cloud_client.py`` even *imports* ``anthropic``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import keyring
import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from sqlalchemy.orm import Session, sessionmaker

from backend.cost_control import audit as audit_mod
from backend.cost_control import concurrency as concurrency_mod
from backend.cost_control import config_loader, notify as notify_mod
from backend.cost_control.budget_state import BudgetState
from backend.cost_control.config_loader import Budgets


# ---------------------------------------------------------------------------
# In-memory keyring (same shape as tests/admin/conftest.py)
# ---------------------------------------------------------------------------

class _InMemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def _fake_keyring() -> Iterator[_InMemoryKeyring]:
    original = keyring.get_keyring()
    backend = _InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


# ---------------------------------------------------------------------------
# ANS_HOME redirect — every disk write goes here, not to real home.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ans_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ans-tool"
    home.mkdir()
    # audit.py is the single source of truth for ANS_HOME; budget_state
    # reads it via ``audit_mod.ANS_HOME`` so a single monkeypatch covers
    # both modules.
    monkeypatch.setattr(audit_mod, "ANS_HOME", home)
    return home


# ---------------------------------------------------------------------------
# Notification capture — never let osascript run in tests.
# ---------------------------------------------------------------------------

@dataclass
class _NotificationCall:
    title: str
    body: str


class _NotifyRecorder:
    """Records calls to ``notify.notify`` for assertions."""

    def __init__(self) -> None:
        self.calls: list[_NotificationCall] = []

    def __call__(self, title: str, body: str) -> bool:
        self.calls.append(_NotificationCall(title=title, body=body))
        return True


@pytest.fixture(autouse=True)
def notify_recorder(monkeypatch: pytest.MonkeyPatch) -> _NotifyRecorder:
    monkeypatch.setenv("ANS_DISABLE_NOTIFICATIONS", "1")
    recorder = _NotifyRecorder()
    monkeypatch.setattr(notify_mod, "notify", recorder)
    return recorder


# ---------------------------------------------------------------------------
# Concurrency semaphore reset between tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_semaphore() -> Iterator[None]:
    concurrency_mod._semaphore = None
    concurrency_mod._configured_max = None
    yield
    concurrency_mod._semaphore = None
    concurrency_mod._configured_max = None


# ---------------------------------------------------------------------------
# Config-loader cache reset between tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_config_cache() -> Iterator[None]:
    config_loader.reset_cache()
    yield
    config_loader.reset_cache()


# ---------------------------------------------------------------------------
# Cap fixtures — predictable values, not whatever's in the repo's config.yaml.
# ---------------------------------------------------------------------------

@pytest.fixture()
def caps() -> Budgets:
    """Mid-range caps used by most trap tests.

    Values chosen so a 10¢ call comfortably fits but a $3 call breaks
    the per-job cap, a $11 day breaks the daily hard, and a $51 month
    breaks the monthly hard.
    """
    return Budgets(
        per_job_usd=2.00,
        per_job_max_via_ui_usd=10.00,
        daily_soft_usd=5.00,
        daily_hard_usd=10.00,
        monthly_hard_usd=50.00,
    )


@pytest.fixture()
def budget_state(caps: Budgets) -> BudgetState:
    return BudgetState(caps)


# ---------------------------------------------------------------------------
# SQLite — migrated fresh per test.
# ---------------------------------------------------------------------------

def _alembic_cfg(db_path: Path) -> Config:
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "runaway.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Job-row helper — most trap tests need a real row to reference.
# ---------------------------------------------------------------------------

@pytest.fixture()
def job_id(db_session: Session) -> str:
    """Insert a minimal job row and return its id."""
    import uuid
    from datetime import datetime, timezone

    jid = str(uuid.uuid4())
    db_session.execute(
        sa.text(
            "INSERT INTO jobs (id, started_at, status, cost_usd) "
            "VALUES (:id, :started_at, :status, 0.0)"
        ),
        {
            "id": jid,
            "started_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "status": "created",
        },
    )
    db_session.commit()
    return jid


# ---------------------------------------------------------------------------
# Fake SDK clients — the only Claude "API" any test in this suite touches.
# ---------------------------------------------------------------------------

@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeResponse:
    usage: FakeUsage
    content: list[dict[str, Any]]


class RecordingFakeSDK:
    """Minimal SDK fake with a ``messages.create`` recording call args.

    Constructed empty; ``set_next_response`` queues responses. Test
    code can also pass ``raise_error`` to make the next call raise.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: list[Any] = []
        self._errors: list[BaseException | None] = []
        # Sub-object so production code calling ``sdk.messages.create``
        # is satisfied.
        self.messages = _FakeMessages(self)

    def set_next_response(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        tool_uses: list[str] | None = None,
    ) -> None:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "ok"}
        ]
        if tool_uses:
            for name in tool_uses:
                content.append({"type": "tool_use", "name": name})
        self._responses.append(
            FakeResponse(
                usage=FakeUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
                content=content,
            )
        )
        self._errors.append(None)

    def set_next_error(self, exc: BaseException) -> None:
        self._responses.append(None)
        self._errors.append(exc)


class _FakeMessages:
    def __init__(self, parent: RecordingFakeSDK) -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> Any:
        self._parent.calls.append(kwargs)
        if not self._parent._errors:
            raise AssertionError(
                "RecordingFakeSDK was not primed with a response or error; "
                "call set_next_response()/set_next_error() before invoking."
            )
        err = self._parent._errors.pop(0)
        resp = self._parent._responses.pop(0)
        if err is not None:
            raise err
        return resp


@pytest.fixture()
def fake_sdk() -> RecordingFakeSDK:
    return RecordingFakeSDK()
