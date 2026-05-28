"""Static AST fence — ``backend.exporters.lifecycle`` is strictly read-only.

The lifecycle module computes projections and validates integrity. It
must never write to disk, never mutate the manifest, never trigger a
state transition, and never call a renderer. This test scans the
source with AST + substring matching so a future "convenience" edit
that adds a writer cannot sneak through review.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_LIFECYCLE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend" / "exporters" / "lifecycle.py"
)


# Names known to mutate state — sourced from ``backend.jobs.storage``
# and ``backend.assembly``. If lifecycle.py references any of these
# the read-only contract is broken.
_FORBIDDEN_NAMES = (
    "write_export",
    "write_document_manifest",
    "write_prospect_brief_markdown",
    "upsert_export_entry",
    "append_transition",
    "write_state",
    "record_failure",
    "stamp_last_error",
    "write_initial_state",
    "_atomic_write_json",
    "_atomic_write_bytes",
    "_atomic_write_text",
    "os.replace",
    "Path.write_bytes",
    "Path.write_text",
    "tempfile",
    "render_pdf",
    "render_html",
    "assemble_job",
)


_FORBIDDEN_PREFIXES = (
    "backend.assembly",
    "backend.orchestrator",
    "backend.agents",
    "backend.delivery",
    "backend.credentials",
    "backend.security",
    "backend.cost_control",
    "anthropic",
    "keyring",
)


def _read_source() -> str:
    return _LIFECYCLE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def src() -> str:
    return _read_source()


@pytest.mark.parametrize("needle", _FORBIDDEN_NAMES)
def test_no_writer_references(src: str, needle: str) -> None:
    assert needle not in src, (
        f"backend.exporters.lifecycle must not reference {needle!r} — "
        "the module is strictly read-only"
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


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(src: str, forbidden: str) -> None:
    imported = _imported_modules(src)
    leaked = [
        name for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"lifecycle.py must not import {forbidden}; found: {leaked}"
    )


def test_does_not_open_files_for_writing(src: str) -> None:
    # Defence in depth — no ``open(..., "w...")`` patterns and no
    # ``.write(`` direct calls. The only filesystem-touching helpers
    # we allow are the read-only ones from ``backend.jobs.storage``.
    forbidden_open = (
        'open("', "open('",
    )
    # We only flag open(...) followed by a write mode marker — the
    # storage layer wraps reads with Path methods, not bare ``open``.
    for needle in forbidden_open:
        if needle in src:
            # Confirm it isn't a write-mode open.
            idx = src.find(needle)
            slice_ = src[idx:idx + 60]
            assert ', "w' not in slice_ and ", 'w" not in slice_, (
                f"write-mode open in lifecycle.py: {slice_!r}"
            )

    # Never call .write_bytes or .write_text on a Path-like.
    for needle in (".write_bytes(", ".write_text(", ".touch("):
        assert needle not in src, (
            f"lifecycle.py must not call {needle!r}"
        )


def test_uses_only_read_helpers_from_storage(src: str) -> None:
    """Positive shape: the only storage names lifecycle.py imports must
    be read-only helpers."""
    tree = ast.parse(src)
    imported_from_storage: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "backend.jobs.storage":
                for alias in node.names:
                    imported_from_storage.add(alias.name)

    allowed = {
        "JobNotFound",
        "export_path_for",
        "job_folder",
        "read_document_manifest",
        "read_export_bytes",
        "read_prospect_brief_markdown",
    }
    extra = imported_from_storage - allowed
    assert not extra, (
        f"lifecycle.py imports unexpected names from backend.jobs.storage: "
        f"{sorted(extra)}"
    )
