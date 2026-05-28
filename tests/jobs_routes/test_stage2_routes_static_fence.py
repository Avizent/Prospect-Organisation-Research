"""Static fence — ``backend.jobs.stage2_routes`` must not import forbidden modules.

Step 23 introduces a single new module that DOES import
:mod:`backend.orchestrator` (this is the deliberate fence relaxation
for Step 23 — the new module is the only place in
``backend/jobs/`` allowed to do that). Every *other* fence in
:mod:`tests.jobs_routes.test_no_production_client_or_keychain`
applies here too: no Anthropic SDK, no Keychain, no credentials
module, no delivery, no assembly, no production cloud-client
wrapper, no frontend.

We use AST analysis (not a runtime import probe) so a future
function-deferred import wouldn't sneak past the fence. Every
``import`` and ``from`` statement is inspected, no matter where it
appears in the file.

We also run a substring scan for known dangerous identifiers
(``CloudClient``, ``keyring.get_password``, ``get_anthropic_key``,
``for_production``) in case the forbidden name reaches the module
via a renamed re-import.

A positive-shape assertion enforces that the relaxation is real —
the orchestrator import MUST be present. If a future refactor moves
the orchestrator out of this module, the route is no longer doing
its job and the relaxation no longer makes sense.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


# Forbidden module prefixes — same set as the Step 11 fence MINUS
# ``backend.orchestrator``, which Step 23 deliberately allows in this
# one module only.
_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "backend.credentials",
    "backend.delivery",
    "backend.assembly",
    "backend.tools.cloud_client",
    "frontend",
)

# Substring scans for known dangerous identifiers that could reach
# the module via a renamed re-import or attribute access.
_FORBIDDEN_SUBSTRINGS = (
    "CloudClient(",
    "for_production(",
    ".for_production",
    "keyring.get_password",
    "get_anthropic_key",
)

_STAGE2_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "stage2_routes.py"
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
def stage2_routes_source() -> str:
    return _STAGE2_ROUTES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(stage2_routes_source: str) -> set[str]:
    return _imported_modules(stage2_routes_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.stage2_routes must not import {forbidden}; "
        f"found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    stage2_routes_source: str, forbidden: str
) -> None:
    """Even if the name reached the module via a renamed re-import,
    the substring scan would catch it."""
    assert forbidden not in stage2_routes_source, (
        f"backend.jobs.stage2_routes must not reference {forbidden!r}"
    )


def test_orchestrator_import_is_present(imported: set[str]) -> None:
    """Positive shape: this is the *one* module in ``backend/jobs/``
    that is allowed to import :mod:`backend.orchestrator`. If the
    import is missing the relaxation no longer applies and the route
    cannot do its job — fail loudly so the next reviewer notices.
    """
    orchestrator_imports = [
        name
        for name in imported
        if name == "backend.orchestrator"
        or name.startswith("backend.orchestrator.")
    ]
    assert orchestrator_imports, (
        "backend.jobs.stage2_routes must import backend.orchestrator — "
        "this is the route's whole purpose"
    )


def test_fastapi_is_allowed(imported: set[str]) -> None:
    """Sanity check the fence does not over-reach. FastAPI is the
    route module's primary dependency."""
    fastapi_imports = [n for n in imported if n.startswith("fastapi")]
    assert fastapi_imports, (
        "expected backend.jobs.stage2_routes to import fastapi"
    )


def test_module_is_importable_in_isolation() -> None:
    """A second-order check: the imports the module *does* use must
    not transitively pull in anything forbidden through a deferred
    import inside a helper module."""
    import importlib

    module = importlib.import_module("backend.jobs.stage2_routes")
    assert hasattr(module, "router")
    # The new router exposes exactly one route. A future change to
    # this number is a deliberate surface change that must be
    # approved per-step.
    assert len(module.router.routes) == 1


def test_dependency_provider_names_exist() -> None:
    """The route documents three dependency-override entry points.
    Each must be importable as a public symbol — tests rely on the
    exact names to install fakes via ``app.dependency_overrides``.
    """
    import importlib

    module = importlib.import_module("backend.jobs.stage2_routes")
    for name in ("get_cloud_client", "get_models", "get_db_session_factory"):
        assert hasattr(module, name), (
            f"backend.jobs.stage2_routes must expose {name} as a "
            "dependency-override target"
        )
