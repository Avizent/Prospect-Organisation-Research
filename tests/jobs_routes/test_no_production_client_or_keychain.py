"""Static fence — ``backend.jobs.routes`` must not import forbidden modules.

The route module is a *boundary* — it bridges the HTTP world and
the Step-7a/Step-9a/Step-10 backend primitives. Step 11 is
deliberately fake-safe: the routes must not pull in any of the
Anthropic SDK, the CloudClient wrapper, the Keychain layer, the
M365 delivery layer, the document assembly layer, the orchestrator
(which knows how to run Stage 1), or the frontend.

FastAPI is **allowed** here — this is the route module. Routes
under other prefixes (auth, admin) are explicitly out of scope.

We use AST analysis (not a runtime import probe) so a future
function-deferred import wouldn't sneak past the fence. Every
``import`` and ``from`` statement is inspected, no matter where it
appears in the file.

We also run a substring scan for known dangerous identifiers
(``CloudClient``, ``CloudClient.for_production``,
``keyring.get_password``, ``get_anthropic_key``) in case the
forbidden name reaches the module via a renamed re-import.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "backend.credentials",
    "backend.delivery",
    "backend.assembly",
    "backend.orchestrator",
    "backend.tools.cloud_client",
    "frontend",
)

_FORBIDDEN_SUBSTRINGS = (
    "CloudClient",
    "CloudClient.for_production",
    "keyring.get_password",
    "get_anthropic_key",
)

_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "routes.py"
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
def routes_source() -> str:
    return _ROUTES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(routes_source: str) -> set[str]:
    return _imported_modules(routes_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.routes must not import {forbidden}; "
        f"found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    routes_source: str, forbidden: str
) -> None:
    """Even if the name reached the module via a renamed re-import,
    the substring scan would catch it."""
    assert forbidden not in routes_source, (
        f"backend.jobs.routes must not reference {forbidden}"
    )


def test_fastapi_is_allowed(imported: set[str]) -> None:
    """Sanity check the fence does not over-reach. FastAPI is the
    route module's primary dependency."""
    fastapi_imports = [n for n in imported if n.startswith("fastapi")]
    assert fastapi_imports, "expected backend.jobs.routes to import fastapi"


def test_routes_module_is_importable_in_isolation() -> None:
    """A second-order check: the imports the routes module *does* use
    must not transitively pull in anything forbidden. Importing the
    module from this test runs every top-level statement and would
    surface any sneaky deferred import that wires the SDK in via a
    helper module."""
    import importlib

    module = importlib.import_module("backend.jobs.routes")
    assert hasattr(module, "router")
    # Spot-check that the route count matches the approved surface.
    # Step 21 bumped this from 12 → 17 by adding 5 read-only Stage 2
    # artefact GET routes (product-mapping, benefits, faq, objections,
    # critic-report). Step 31 bumps it to 18 by adding the read-only
    # Markdown viewer feed (``/brief/markdown``). Any future change to
    # this number is a deliberate surface change that must be approved
    # per-step.
    assert len(module.router.routes) == 18


def test_no_cloud_client_attribute_access(routes_source: str) -> None:
    """Defence-in-depth: no attribute access patterns that would suggest
    CloudClient is being constructed at runtime."""
    forbidden_calls = (
        "for_production(",
        ".for_production",
        "CloudClient(",
    )
    for needle in forbidden_calls:
        assert needle not in routes_source, (
            f"backend.jobs.routes must not contain {needle!r}"
        )
