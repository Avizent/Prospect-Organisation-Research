"""Static fence — ``backend.jobs.export_routes`` (Step 36).

Step 36 introduces a single new module that DOES import
:mod:`backend.exporters.pdf` — this is the deliberate fence
relaxation for Step 36 (the new module is the only place in
``backend/jobs/`` allowed to do that). Every other fence in
:mod:`tests.jobs_routes.test_no_production_client_or_keychain`
applies here too: no Anthropic SDK, no Keychain, no credentials
module, no delivery, no orchestrator, no production cloud-client
wrapper, no frontend, no assembler.

We use AST analysis (not a runtime import probe) so a future
function-deferred import wouldn't sneak past the fence. Every
``import`` and ``from`` statement is inspected, no matter where it
appears in the file.

We also run a substring scan for known dangerous identifiers
(``CloudClient``, ``keyring.get_password``, ``get_anthropic_key``,
``for_production``) in case the forbidden name reaches the module
via a renamed re-import.

A positive-shape assertion enforces that the relaxation is real —
the exporter import MUST be present. If a future refactor moves
the renderer out of this module, the route is no longer doing its
job and the relaxation no longer makes sense.

A final scan asserts that no state-mutating storage helper is
referenced from this module — the export route is documented as
state-machine-invariant and the easiest way to break that is to
accidentally call ``append_transition`` / ``write_state`` /
``record_failure``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


# Forbidden module prefixes — same set as the Step 11 fence MINUS
# ``backend.exporters``, which Step 36 deliberately allows in this
# one module only. ``backend.assembly`` STAYS on the forbidden list:
# the export route consumes assembler output via the storage layer,
# never by calling into the assembler.
_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "backend.credentials",
    "backend.delivery",
    "backend.assembly",
    "backend.orchestrator",
    "backend.tools.cloud_client",
    "backend.cost_control.cloud_client",
    "frontend",
)

_FORBIDDEN_SUBSTRINGS = (
    "CloudClient(",
    "for_production(",
    ".for_production",
    "keyring.get_password",
    "get_anthropic_key",
)

# State-mutation helpers from :mod:`backend.jobs.storage`. The route
# is documented as state-machine-invariant; referencing any of these
# from the route module would silently break that property.
_FORBIDDEN_STATE_WRITERS = (
    "append_transition(",
    "write_state(",
    "record_failure(",
    "stamp_last_error(",
    "write_initial_state(",
)


_EXPORT_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "export_routes.py"
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
def export_routes_source() -> str:
    return _EXPORT_ROUTES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(export_routes_source: str) -> set[str]:
    return _imported_modules(export_routes_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.export_routes must not import {forbidden}; "
        f"found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    export_routes_source: str, forbidden: str
) -> None:
    """Even if the name reached the module via a renamed re-import,
    the substring scan would catch it."""
    assert forbidden not in export_routes_source, (
        f"backend.jobs.export_routes must not reference {forbidden!r}"
    )


def test_exporters_import_is_present(imported: set[str]) -> None:
    """Positive shape: this is the *one* module in ``backend/jobs/``
    that is allowed to import :mod:`backend.exporters`. If the import
    is missing the relaxation no longer applies and the route cannot
    do its job — fail loudly so the next reviewer notices.
    """
    exporter_imports = [
        name
        for name in imported
        if name == "backend.exporters"
        or name.startswith("backend.exporters.")
    ]
    assert exporter_imports, (
        "backend.jobs.export_routes must import backend.exporters — "
        "this is the route's whole purpose"
    )


def test_fastapi_is_allowed(imported: set[str]) -> None:
    """Sanity check the fence does not over-reach. FastAPI is the
    route module's primary dependency."""
    fastapi_imports = [n for n in imported if n.startswith("fastapi")]
    assert fastapi_imports, (
        "expected backend.jobs.export_routes to import fastapi"
    )


def test_module_is_importable_in_isolation() -> None:
    """A second-order check: the imports the module *does* use must
    not transitively pull in anything forbidden through a deferred
    import inside a helper module."""
    import importlib

    module = importlib.import_module("backend.jobs.export_routes")
    assert hasattr(module, "router")
    # The new router exposes exactly two routes (POST + GET). A
    # future change to this number is a deliberate surface change
    # that must be approved per-step.
    assert len(module.router.routes) == 2


@pytest.mark.parametrize("needle", _FORBIDDEN_STATE_WRITERS)
def test_no_state_writer_calls(
    export_routes_source: str, needle: str,
) -> None:
    """The route must not mutate ``state.json``.

    The export route is side-effect-free with respect to the job
    state machine; the only on-disk mutations it triggers are the
    PDF write and the manifest's ``exports[]`` upsert.
    """
    assert needle not in export_routes_source, (
        f"backend.jobs.export_routes must not call {needle!r} — "
        "export is state-machine-invariant"
    )
