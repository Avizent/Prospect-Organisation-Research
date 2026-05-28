"""Static fence — ``backend.exporters`` must not import forbidden modules.

The export layer is a strict *lifecycle* boundary:

  * It must NOT pull in any agent, LLM client, or credential code —
    exports are pure functions of canonical files on disk.
  * It must NOT import ``backend.assembly`` — assembly and export are
    separate responsibilities. Exporters consume the assembler's
    artefacts via the storage layer; they never call into the
    assembler.
  * It must NOT import the M365 delivery layer or any HTTP/network
    library — exports are local-only artefact production.

We use AST analysis (not a runtime import probe) so a deferred or
function-scoped import cannot sneak past the fence.

This module also asserts the *reverse* boundary: ``backend.assembly``
(except for the two transitional shims at ``pdf_builder.py`` /
``xlsx_builder.py``) must not import ``backend.exporters``. The two
shims are a Step 35-only transitional aid and are exempted by name.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "backend.agents",
    "backend.cost_control.cloud_client",
    "backend.delivery",
    "backend.security",
    "backend.credentials",
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


_EXPORTERS_PKG = (
    pathlib.Path(__file__).resolve().parents[2] / "backend" / "exporters"
)

_ASSEMBLY_PKG = (
    pathlib.Path(__file__).resolve().parents[2] / "backend" / "assembly"
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


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py"))


# ---------------------------------------------------------------------------
# backend.exporters → forbidden imports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("py_file", _python_files(_EXPORTERS_PKG))
@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_exporters_files_do_not_import_forbidden_modules(
    py_file: pathlib.Path, forbidden: str
) -> None:
    source = py_file.read_text(encoding="utf-8")
    imported = _imported_modules(source)
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"{py_file} must not import {forbidden}; found: {leaked}"
    )


@pytest.mark.parametrize("py_file", _python_files(_EXPORTERS_PKG))
@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_exporters_files_do_not_reference_forbidden_substrings(
    py_file: pathlib.Path, forbidden: str
) -> None:
    source = py_file.read_text(encoding="utf-8")
    assert forbidden not in source, (
        f"{py_file} must not reference {forbidden!r}"
    )


def test_exporters_files_contain_no_url_literals() -> None:
    """``backend.exporters`` is a local-only renderer. No HTTP, no
    HTTPS, no URL schemes anywhere in the package source."""
    for py_file in _python_files(_EXPORTERS_PKG):
        source = py_file.read_text(encoding="utf-8")
        for needle in ("http://", "https://", "://"):
            assert needle not in source, (
                f"{py_file} must not contain {needle!r}"
            )


# ---------------------------------------------------------------------------
# Reverse fence: assembly may not import exporters (except shims)
# ---------------------------------------------------------------------------


# The two transitional compatibility shims are explicitly allowed to
# re-export from ``backend.exporters``. They are Step 35-only and will
# be removed once transitional callers have been migrated.
_SHIM_FILES = {"pdf_builder.py", "xlsx_builder.py"}


def test_assembly_does_not_import_exporters_except_shims() -> None:
    """Lifecycle separation: ``backend.assembly`` consumes nothing
    from ``backend.exporters`` apart from the two transitional shims
    that exist solely to re-export the public provenance constants."""
    leaks: list[tuple[pathlib.Path, set[str]]] = []
    for py_file in _python_files(_ASSEMBLY_PKG):
        if py_file.name in _SHIM_FILES:
            continue
        source = py_file.read_text(encoding="utf-8")
        imported = _imported_modules(source)
        exporters_imports = {
            name
            for name in imported
            if name == "backend.exporters"
            or name.startswith("backend.exporters.")
        }
        if exporters_imports:
            leaks.append((py_file, exporters_imports))
    assert not leaks, (
        f"backend.assembly must not import backend.exporters "
        f"(except the transitional shims {sorted(_SHIM_FILES)}); "
        f"found: {leaks}"
    )


def test_shims_only_re_export_public_constants() -> None:
    """The two shims must do nothing except re-export
    ``EXPORTER_VERSION`` and ``TEMPLATE_VERSION`` from
    ``backend.exporters``. We inspect the AST (not source substrings)
    so docstring prose mentioning renderer names is not flagged."""
    for shim_name in _SHIM_FILES:
        path = _ASSEMBLY_PKG / shim_name
        source = path.read_text(encoding="utf-8")
        # Sanity-check the documented entry point is present.
        assert "from backend.exporters import" in source

        imported = _imported_modules(source)
        for forbidden in (
            "weasyprint",
            "openpyxl",
            "docx",
            "jinja2",
            "PIL",
            "cairosvg",
            "pikepdf",
        ):
            leaked = [
                name
                for name in imported
                if name == forbidden or name.startswith(forbidden + ".")
            ]
            assert not leaked, (
                f"shim {shim_name} must not import renderer dep "
                f"{forbidden!r}; found: {leaked}"
            )

        # The shims must import ONLY from ``backend.exporters``.
        # Any other module (besides ``__future__``) is a smell.
        allowed = {"backend.exporters", "__future__"}
        actual = imported - allowed
        assert not actual, (
            f"shim {shim_name} must import only from {sorted(allowed)}; "
            f"found extra imports: {sorted(actual)}"
        )
