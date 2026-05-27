"""Static fence around the agent layer.

Companion to ``tests/runaway/test_no_direct_anthropic_imports.py``,
which already prevents any file under ``backend/`` from importing the
Anthropic SDK directly. This test adds a second, agent-specific fence:

Agents must NOT
* import ``anthropic`` (defence-in-depth — also covered by the runaway
  test, but cheap to assert here too)
* import ``keyring`` or anything from ``backend.credentials``
* call ``CloudClient(...)`` or ``CloudClient.for_production(...)``

The reasoning: a runaway-spend bug can hide behind any of these. The
orchestrator (Step 9) is the one place where ``CloudClient`` is
constructed; every agent borrows the client by injection. If an agent
ever reaches for the Keychain or builds its own client, the per-job
budget guard the orchestrator wired up would be bypassed silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _REPO_ROOT / "backend" / "agents"

# Modules an agent file must not import (full names or known dotted-prefix).
_BANNED_IMPORTS: frozenset[str] = frozenset({
    "anthropic",
    "keyring",
})

# Module prefixes (dotted) an agent file must not import from.
_BANNED_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.credentials",
)

# Attribute names that, when accessed on ``CloudClient``, indicate
# production credential resolution. Constructing ``CloudClient(...)``
# itself is also banned (caught by name lookup).
_BANNED_CLOUDCLIENT_ATTRS: frozenset[str] = frozenset({
    "for_production",
})


def _iter_agent_files() -> list[Path]:
    """Return every .py under backend/agents/, excluding __pycache__."""
    return sorted(
        p for p in _AGENTS_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


def _import_offences(tree: ast.Module) -> list[str]:
    """Return a list of human-readable offences found in ``tree``.

    Empty list = clean.
    """
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                root = name.split(".", 1)[0]
                if root in _BANNED_IMPORTS:
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
    """Detect ``CloudClient(...)`` construction or banned-attribute use.

    We treat both ``CloudClient(args)`` and ``CloudClient.for_production(...)``
    as offences regardless of how the name was bound. The conservative
    behaviour: any reference to the bare name ``CloudClient`` followed
    by a call, or any attribute access on it that matches the banned
    set, is flagged.
    """
    offences: list[str] = []
    for node in ast.walk(tree):
        # Direct construction: CloudClient(...)
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
        # Attribute access without call (e.g. assigning the bound method).
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
    _iter_agent_files(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_agent_file_has_no_banned_imports_or_constructions(
    py_file: Path,
) -> None:
    rel = py_file.relative_to(_REPO_ROOT)
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))

    import_off = _import_offences(tree)
    cloud_off = _cloudclient_offences(tree)
    all_off = import_off + cloud_off

    assert not all_off, (
        f"{rel} contains forbidden patterns: {all_off}. "
        "Agents must receive CloudClient via injection; they must not "
        "import the Anthropic SDK, the Keychain, or backend.credentials, "
        "and they must not construct CloudClient or call "
        "CloudClient.for_production."
    )
