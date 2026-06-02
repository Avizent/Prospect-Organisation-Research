"""Static fence — ``backend.jobs.stage1_routes`` must not import forbidden modules.

Step 52 introduces a new module that imports :mod:`backend.orchestrator`
(the deliberate fence relaxation for Step 52 — this is the only module
in ``backend/jobs/`` allowed to import the orchestrator after
``stage2_routes.py``). Every other fence that applies to
:mod:`tests.jobs_routes.test_no_production_client_or_keychain` applies
here too: no Anthropic SDK, no Keychain, no credentials module, no
delivery, no assembly, no production cloud-client wrapper, no frontend.

AST analysis (not a runtime import probe) is used so a future
function-deferred import would not sneak past the fence. Every
``import`` and ``from`` statement is inspected, no matter where it
appears in the file.

A substring scan covers known dangerous identifiers (``CloudClient``,
``keyring.get_password``, ``get_anthropic_key``, ``for_production``)
in case the forbidden name reaches the module via a renamed re-import.

A positive-shape assertion enforces that the :mod:`backend.orchestrator`
import MUST be present — if a future refactor moves it out, the route
no longer does its job and the relaxation no longer makes sense.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest


# Forbidden module prefixes — same set as the Step 11 fence MINUS
# ``backend.orchestrator``, which Step 52 deliberately allows here.
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
    "CloudClient(",
    "for_production(",
    ".for_production",
    "keyring.get_password",
    "get_anthropic_key",
)

_STAGE1_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "stage1_routes.py"
)


def _imported_modules(source: str) -> set[str]:
    """Return every fully-qualified module name imported in ``source``."""
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
def stage1_routes_source() -> str:
    return _STAGE1_ROUTES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(stage1_routes_source: str) -> set[str]:
    return _imported_modules(stage1_routes_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.stage1_routes must not import {forbidden}; "
        f"found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    stage1_routes_source: str, forbidden: str
) -> None:
    """Even if a forbidden name reached the module via a renamed re-import,
    the substring scan catches it."""
    assert forbidden not in stage1_routes_source, (
        f"backend.jobs.stage1_routes must not reference {forbidden!r}"
    )


def test_orchestrator_import_is_present(imported: set[str]) -> None:
    """Positive shape: this module MUST import :mod:`backend.orchestrator`.

    If the import is absent the relaxation no longer applies and the
    route cannot do its job — fail loudly so the reviewer notices.
    """
    orchestrator_imports = [
        name
        for name in imported
        if name == "backend.orchestrator"
        or name.startswith("backend.orchestrator.")
    ]
    assert orchestrator_imports, (
        "backend.jobs.stage1_routes must import backend.orchestrator"
    )


def test_fastapi_is_allowed(imported: set[str]) -> None:
    """Sanity check: FastAPI must be imported (this is a route module)."""
    fastapi_imports = [n for n in imported if n.startswith("fastapi")]
    assert fastapi_imports, (
        "expected backend.jobs.stage1_routes to import fastapi"
    )


def test_module_is_importable_in_isolation() -> None:
    """The module's imports must not transitively pull in anything forbidden."""
    module = importlib.import_module("backend.jobs.stage1_routes")
    assert hasattr(module, "router")
    # Exactly one route. A future surface change requires updating this.
    assert len(module.router.routes) == 1, (
        f"expected 1 route, found {len(module.router.routes)}"
    )


def test_dependency_provider_names_exist() -> None:
    """The route documents three dependency-override entry points.

    Each must be importable by name so tests can install fakes via
    ``app.dependency_overrides``.
    """
    module = importlib.import_module("backend.jobs.stage1_routes")
    for name in ("get_cloud_client", "get_models", "get_db_session_factory"):
        assert hasattr(module, name), (
            f"backend.jobs.stage1_routes must expose {name!r} "
            "as a dependency-override target"
        )
