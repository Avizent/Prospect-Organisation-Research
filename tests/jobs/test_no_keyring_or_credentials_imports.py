"""Static fence around the ``backend/jobs/`` package.

Three companion fences already exist:

* ``tests/runaway/test_no_direct_anthropic_imports.py`` — bans the
  Anthropic SDK everywhere under ``backend/`` except the lone allowed
  importer.
* ``tests/agents/test_no_direct_cloud_client_construction.py`` —
  ban list for the agent layer.
* (This file.)

The jobs layer (Step 7a) is intake + state + storage only. It has
**no legitimate reason** to touch:

* the Anthropic SDK
* the macOS Keychain (``keyring``)
* the credentials module (``backend.credentials``)
* the CloudClient module (``backend.cost_control.cloud_client``)

This test AST-walks every file under ``backend/jobs/`` and rejects
any of the above. It is intentionally stricter than the agent fence
— the agent layer borrows a CloudClient at construction, the jobs
layer does not borrow anything from cost_control at all in Step 7a.

Step 7b (orchestrator wiring) will introduce a new module that *does*
borrow CloudClient; that step will add its own narrower fence, and
this one will continue to protect the jobs layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JOBS_DIR = _REPO_ROOT / "backend" / "jobs"


# Modules an intake/state/storage file must not import (full names or
# known dotted-prefix).
_BANNED_IMPORTS: frozenset[str] = frozenset({
    "anthropic",
    "keyring",
})

# Module prefixes (dotted) an intake/state/storage file must not import
# from.
_BANNED_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.credentials",
    "backend.cost_control.cloud_client",
)

# Attribute names that, when accessed on ``CloudClient``, indicate
# production credential resolution.
_BANNED_CLOUDCLIENT_ATTRS: frozenset[str] = frozenset({
    "for_production",
})


def _iter_jobs_files() -> list[Path]:
    return sorted(
        p for p in _JOBS_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


def _import_offences(tree: ast.Module) -> list[str]:
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                root = name.split(".", 1)[0]
                if root in _BANNED_IMPORTS:
                    offences.append(f"import {name}")
                for prefix in _BANNED_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(prefix + "."):
                        offences.append(f"import {name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in _BANNED_IMPORTS:
                offences.append(f"from {module} import ...")
            for prefix in _BANNED_IMPORT_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offences.append(f"from {module} import ...")
    return offences


def _cloudclient_offences(tree: ast.Module) -> list[str]:
    """Detect any reference to ``CloudClient`` — construction or
    attribute access. Defence-in-depth: even if a hypothetical future
    import slipped past the import-prefix ban, the name would still be
    forbidden at the call site."""
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "CloudClient":
                offences.append("CloudClient(...) construction")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "CloudClient"
                and func.attr in _BANNED_CLOUDCLIENT_ATTRS
            ):
                offences.append(f"CloudClient.{func.attr}(...) call")
        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "CloudClient"
                and node.attr in _BANNED_CLOUDCLIENT_ATTRS
            ):
                offences.append(f"CloudClient.{node.attr} reference")
    return offences


@pytest.mark.parametrize(
    "py_file",
    _iter_jobs_files(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_jobs_file_has_no_banned_imports_or_constructions(
    py_file: Path,
) -> None:
    rel = py_file.relative_to(_REPO_ROOT)
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))

    all_off = _import_offences(tree) + _cloudclient_offences(tree)

    assert not all_off, (
        f"{rel} contains forbidden patterns: {all_off}. "
        "The jobs layer must not import the Anthropic SDK, the "
        "Keychain, backend.credentials, or backend.cost_control."
        "cloud_client, and it must not reference CloudClient by name."
    )
