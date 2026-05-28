"""Static fence — ``backend.jobs.export_governance_routes`` (Step 37).

Step 37 introduces a single new module that imports
:mod:`backend.exporters.lifecycle` — this is the deliberate fence
relaxation for Step 37 (the new module is the only place in
``backend/jobs/`` allowed to do that besides the Step 36 export
routes, which import a different submodule of ``backend.exporters``).

Every other fence in
:mod:`tests.jobs_routes.test_no_production_client_or_keychain`
applies here too: no Anthropic SDK, no Keychain, no credentials
module, no delivery, no orchestrator, no production cloud-client
wrapper, no frontend, no assembler.

AST + substring scan + a positive-shape assertion (the lifecycle
import MUST be present) + a route-count assertion (the new router
exposes exactly one route).

A final scan asserts that no state-mutating storage helper is
referenced from this module — the governance route is documented as
state-machine-invariant and reads ``state.json`` only.
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


_FORBIDDEN_STATE_WRITERS = (
    "append_transition(",
    "write_state(",
    "record_failure(",
    "stamp_last_error(",
    "write_initial_state(",
    "write_export(",
    "upsert_export_entry(",
    "write_document_manifest(",
    "write_prospect_brief_markdown(",
)


_GOVERNANCE_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "export_governance_routes.py"
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
def governance_source() -> str:
    return _GOVERNANCE_ROUTES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def imported(governance_source: str) -> set[str]:
    return _imported_modules(governance_source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.export_governance_routes must not import "
        f"{forbidden}; found: {leaked}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substrings(
    governance_source: str, forbidden: str,
) -> None:
    assert forbidden not in governance_source, (
        f"backend.jobs.export_governance_routes must not reference "
        f"{forbidden!r}"
    )


def test_lifecycle_import_is_present(imported: set[str]) -> None:
    """Positive shape: the relaxation that this module imports
    :mod:`backend.exporters.lifecycle` is the whole point of the
    module. If the import is missing, the route can't do its job and
    the fence relaxation no longer applies.
    """
    lifecycle_imports = [
        name
        for name in imported
        if name == "backend.exporters.lifecycle"
        or name.startswith("backend.exporters.lifecycle.")
    ]
    assert lifecycle_imports, (
        "backend.jobs.export_governance_routes must import "
        "backend.exporters.lifecycle — this is the route's whole purpose"
    )


def test_fastapi_is_allowed(imported: set[str]) -> None:
    fastapi_imports = [n for n in imported if n.startswith("fastapi")]
    assert fastapi_imports, (
        "expected backend.jobs.export_governance_routes to import fastapi"
    )


def test_module_is_importable_and_exposes_single_route() -> None:
    """A second-order check: the new router exposes exactly ONE route
    (``GET /{job_id}/exports/validate``). A future change to this
    number is a deliberate surface change that must be approved
    per-step.
    """
    import importlib

    module = importlib.import_module(
        "backend.jobs.export_governance_routes"
    )
    assert hasattr(module, "router")
    assert len(module.router.routes) == 1
    route = module.router.routes[0]
    assert getattr(route, "path", "") == "/{job_id}/exports/validate"
    assert "GET" in (getattr(route, "methods", set()) or set())


@pytest.mark.parametrize("needle", _FORBIDDEN_STATE_WRITERS)
def test_no_state_writer_calls(
    governance_source: str, needle: str,
) -> None:
    """The governance route is state-machine-invariant and write-free.

    Beyond the standard state-writer fence, we also block any export-
    writing or manifest-writing helper — the validator is strictly
    read-only.
    """
    assert needle not in governance_source, (
        f"backend.jobs.export_governance_routes must not call {needle!r} "
        "— governance is strictly read-only"
    )


def test_only_read_helpers_imported_from_storage(
    governance_source: str,
) -> None:
    """Positive shape — the only names imported from
    :mod:`backend.jobs.storage` must be read-only helpers."""
    tree = ast.parse(governance_source)
    imported_from_storage: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "backend.jobs.storage":
                for alias in node.names:
                    imported_from_storage.add(alias.name)
    allowed = {
        "JobNotFound",
        "read_state",
        "read_document_manifest",
        "read_export_bytes",
        "read_prospect_brief_markdown",
        "export_path_for",
        "job_folder",
    }
    extra = imported_from_storage - allowed
    assert not extra, (
        f"backend.jobs.export_governance_routes imports unexpected names "
        f"from backend.jobs.storage: {sorted(extra)}"
    )
