"""Tests for the opt-in fake Stage 2 runtime (Step 25).

Five layers:

1. Static fence on ``backend.jobs.fake_stage2_runtime``: no anthropic,
   no keyring, no credentials, no delivery, no assembly, no production
   cloud-client wrapper, no frontend.
2. Substring fence: no live-SDK / keychain identifiers reach the
   module via a renamed re-import.
3. Predicate strictness: only the literal string ``"1"`` enables the
   runtime — every other value (unset, empty, ``"0"``, ``"true"``,
   ``"yes"``, …) leaves it disabled.
4. Default OFF: with the env var unset, the route's three dependency
   providers remain unbound and the route returns 503. Step 23's
   safety property is preserved.
5. Opt-in ON: with the env var set and the install hook called
   against a real :class:`TestClient`, the route returns 201, writes
   the five Stage 2 artefacts to disk, and leaves the job state
   unchanged. No real Anthropic / Keychain / M365 / document
   generation occurs.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import sys
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.agents.benefits_models import BenefitsBrief
from backend.agents.critic_models import Stage2CriticReport
from backend.agents.faq_models import FAQDocument
from backend.agents.mapping_models import ProductMapping
from backend.agents.objections_models import ObjectionsRegister
from backend.jobs import fake_stage2_runtime as runtime_mod
from backend.jobs import stage2_routes
from backend.jobs.fake_stage2_runtime import (
    STARTUP_WARNING,
    FakeStage2CloudClient,
    fake_stage2_runtime_enabled,
    install_fake_stage2_runtime,
)
from backend.jobs.state import JobState
from backend.main import app


_KNOWLEDGE_BUNDLE = (
    "## products/emulators.md\n\n"
    "Netropy 100G — line-rate emulator. Marker: FAKE_RUNTIME_TEST_BUNDLE."
)


# ---------------------------------------------------------------------------
# Per-test job-semaphore reset
# ---------------------------------------------------------------------------
#
# The orchestrator acquires the module-global semaphore in each Stage 2
# entry point. Mirrors the pattern in ``test_stage2_run_route.py``.

@pytest.fixture(autouse=True)
def _reset_job_semaphore() -> Iterator[None]:
    from backend.cost_control import concurrency as _concurrency

    _concurrency._reset_for_tests()
    yield
    _concurrency._reset_for_tests()


# ---------------------------------------------------------------------------
# 1. Static fence — forbidden imports
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "backend.credentials",
    "backend.delivery",
    "backend.assembly",
    "backend.tools.cloud_client",
    "frontend",
)

_FORBIDDEN_SUBSTRINGS = (
    "import anthropic",
    "from anthropic",
    "keyring.get_password",
    "get_anthropic_key",
    "for_production(",
    ".for_production",
)


_RUNTIME_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "fake_stage2_runtime.py"
)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level != 0:
                continue
            names.add(node.module)
    return names


@pytest.fixture(scope="module")
def runtime_source() -> str:
    return _RUNTIME_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(runtime_source: str) -> set[str]:
    return _imported_modules(runtime_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.fake_stage2_runtime must not import {forbidden}; "
        f"found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    runtime_source: str, forbidden: str
) -> None:
    assert forbidden not in runtime_source, (
        f"backend.jobs.fake_stage2_runtime must not reference "
        f"{forbidden!r}"
    )


def test_does_not_import_from_tests_package(imported: set[str]) -> None:
    """No backend runtime code may import anything under ``tests/``."""
    leaked = [name for name in imported if name == "tests" or name.startswith("tests.")]
    assert not leaked, (
        f"backend.jobs.fake_stage2_runtime must not import from tests/; "
        f"found: {leaked}"
    )


# ---------------------------------------------------------------------------
# 2. Predicate strictness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["", "0", "true", "TRUE", "yes", "on", "false", "  1 ", "11", "1 "],
)
def test_predicate_false_for_non_exact_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the literal string ``"1"`` enables the runtime."""
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", value)
    assert fake_stage2_runtime_enabled() is False


def test_predicate_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", raising=False)
    assert fake_stage2_runtime_enabled() is False


def test_predicate_true_only_for_exact_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    assert fake_stage2_runtime_enabled() is True


# ---------------------------------------------------------------------------
# 3. Canned responses validate against the canonical schemas
# ---------------------------------------------------------------------------

def test_fake_client_dispatches_mapping_to_product_mapping_schema() -> None:
    fake = FakeStage2CloudClient()
    result = fake.messages_create(
        agent="mapping",
        model="fake-writer",
        job_id="job-x",
        messages=[],
        max_tokens=1024,
        input_tokens_estimate=10,
    )
    text = result.response["content"][0]["text"]
    parsed = ProductMapping.model_validate_json(text)
    assert parsed.matches  # non-empty list


def test_fake_client_dispatches_benefits_writer_to_benefits_brief_schema() -> None:
    fake = FakeStage2CloudClient()
    result = fake.messages_create(
        agent="benefits_writer",
        model="fake-writer",
        job_id="job-x",
        messages=[],
        max_tokens=1024,
        input_tokens_estimate=10,
    )
    parsed = BenefitsBrief.model_validate_json(
        result.response["content"][0]["text"]
    )
    assert parsed.executive_summary is not None


def test_fake_client_dispatches_faq_writer_to_faq_document_schema() -> None:
    fake = FakeStage2CloudClient()
    result = fake.messages_create(
        agent="faq_writer",
        model="fake-writer",
        job_id="job-x",
        messages=[],
        max_tokens=1024,
        input_tokens_estimate=10,
    )
    parsed = FAQDocument.model_validate_json(
        result.response["content"][0]["text"]
    )
    # FAQ schema floor is 12 entries.
    assert len(parsed.entries) >= 12


def test_fake_client_dispatches_objections_writer_to_register_schema() -> None:
    fake = FakeStage2CloudClient()
    result = fake.messages_create(
        agent="objections_writer",
        model="fake-writer",
        job_id="job-x",
        messages=[],
        max_tokens=1024,
        input_tokens_estimate=10,
    )
    parsed = ObjectionsRegister.model_validate_json(
        result.response["content"][0]["text"]
    )
    # ObjectionsRegister schema floor is 15 rows.
    assert len(parsed.rows) >= 15


def test_fake_client_dispatches_critic_to_critic_report_schema() -> None:
    fake = FakeStage2CloudClient()
    result = fake.messages_create(
        agent="critic",
        model="fake-critic",
        job_id="job-x",
        messages=[],
        max_tokens=1024,
        input_tokens_estimate=10,
    )
    parsed = Stage2CriticReport.model_validate_json(
        result.response["content"][0]["text"]
    )
    assert parsed.summary


def test_fake_client_unknown_agent_raises() -> None:
    """Belt-and-braces: an unexpected agent name must fail loudly so a
    future orchestrator wiring change is caught immediately."""
    fake = FakeStage2CloudClient()
    with pytest.raises(RuntimeError, match="no canned response"):
        fake.messages_create(
            agent="research",  # not a Stage 2 agent
            model="fake-writer",
            job_id="job-x",
            messages=[],
            max_tokens=1024,
            input_tokens_estimate=10,
        )


# ---------------------------------------------------------------------------
# 4. Default OFF — route returns 503 unchanged
# ---------------------------------------------------------------------------

def test_env_off_route_returns_503(
    authed_client: TestClient,
    approved_job: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No install call → the three Stage 2 dependency providers
    remain unbound → the route returns 503 with the documented
    reason. Step 23's safety property is preserved."""
    monkeypatch.delenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", raising=False)
    # Conftest's ``client`` fixture clears app.dependency_overrides
    # between tests, so no install hook is active here.
    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["detail"]["reason"] == "stage2_run_route_has_no_client_bound"


# ---------------------------------------------------------------------------
# 5. Opt-in ON — install + POST writes all five artefacts
# ---------------------------------------------------------------------------

def test_env_on_install_then_post_returns_201_envelope(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_fake_stage2_runtime + POST → 201 with the envelope
    shape Step 23 documents."""
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    install_fake_stage2_runtime(app, session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body == {
        "job_id": approved_job,
        "stages_completed": ["mapping", "writers", "critic"],
        "current_state": "approved",
    }


def test_env_on_writes_all_five_valid_artefacts(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After install + POST, all five Stage 2 artefacts are on disk
    AND each parses against its canonical schema."""
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    install_fake_stage2_runtime(app, session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text

    folder = isolated_jobs_root / approved_job
    pairs: list[tuple[str, type]] = [
        ("product_mapping.json", ProductMapping),
        ("benefits.json", BenefitsBrief),
        ("faq.json", FAQDocument),
        ("objections.json", ObjectionsRegister),
        ("critic_report.json", Stage2CriticReport),
    ]
    for name, model_cls in pairs:
        path = folder / name
        assert path.exists(), f"{name} should be on disk"
        # model_validate_json raises ValidationError if the file is
        # malformed; pytest surfaces that as a clear failure.
        model_cls.model_validate_json(path.read_text(encoding="utf-8"))


def test_env_on_does_not_transition_state(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    isolated_jobs_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-place semantics: state.json's transitions list and
    current_state are byte-identical before and after the run."""
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    install_fake_stage2_runtime(app, session_factory)

    state_path = isolated_jobs_root / approved_job / "state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text

    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["current_state"] == JobState.APPROVED.value
    assert after["transitions"] == before["transitions"]
    assert after["last_error"] is None


def test_env_on_no_anthropic_module_loaded(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake runtime path must not transitively import the
    Anthropic SDK. ``anthropic`` must not appear in ``sys.modules``
    after the request completes."""
    # Defensive: if a prior test or test harness leaked the import,
    # there is no point asserting we did not load it here.
    if "anthropic" in sys.modules:
        pytest.skip(
            "anthropic already imported earlier in this process"
        )

    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    install_fake_stage2_runtime(app, session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text
    assert "anthropic" not in sys.modules


def test_install_is_idempotent(
    authed_client: TestClient,
    approved_job: str,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling install twice must not raise or break subsequent
    requests. FastAPI's ``dependency_overrides`` is a dict — the
    second install simply re-writes the three keys."""
    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    install_fake_stage2_runtime(app, session_factory)
    install_fake_stage2_runtime(app, session_factory)

    r = authed_client.post(
        f"/api/jobs/{approved_job}/stage2/run",
        json={"knowledge_bundle": _KNOWLEDGE_BUNDLE},
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 6. Install hook surfaces the warning string
# ---------------------------------------------------------------------------

def test_install_logs_loud_startup_warning(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator looking at startup logs must see a clear warning
    when the fake runtime is wired. Tested verbatim so an
    accidental wording change does not silently weaken the
    foot-gun guard."""
    import logging

    monkeypatch.setenv("ANS_ENABLE_FAKE_STAGE2_RUNTIME", "1")
    # Alembic's ``fileConfig()`` (invoked by the per-test DB engine
    # fixture) calls ``logging.config.fileConfig`` with the default
    # ``disable_existing_loggers=True``, which sets ``disabled=True``
    # on every logger not listed in ``alembic.ini`` — including ours.
    # Re-enable it here so the warning can propagate to caplog.
    logger = logging.getLogger(runtime_mod.__name__)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", True)
    caplog.set_level(logging.WARNING)
    install_fake_stage2_runtime(app, session_factory)
    assert STARTUP_WARNING in caplog.text


def test_warning_string_is_explicit_about_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression guard on the warning wording — the substring
    ``do not use in production`` is the operational signal we depend
    on for the production foot-gun guard."""
    assert "do not use in production" in STARTUP_WARNING.lower()


# ---------------------------------------------------------------------------
# 7. Main.py install hook wiring
# ---------------------------------------------------------------------------

def test_main_module_imports_and_wires_install_hook() -> None:
    """``backend.main`` must reference the predicate + install hook
    by their public names so the lint surface stays observable. If a
    future refactor renames either symbol, this test catches it
    before the env-var-gated path silently breaks."""
    main_src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "backend"
        / "main.py"
    ).read_text(encoding="utf-8")
    assert "fake_stage2_runtime_enabled" in main_src
    assert "install_fake_stage2_runtime" in main_src
