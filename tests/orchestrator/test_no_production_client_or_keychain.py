"""Static fence around :mod:`backend.orchestrator`.

The orchestrator's contract (handover §2 + ``backend/agents/base.py``
design rule §1) is that :class:`CloudClient` is constructed
**outside** this module and injected. The route layer (Step 9+) is
the one place where production credentials enter the system.

This fence proves that ``backend/orchestrator.py``:

* does not import the Anthropic SDK
* does not import :mod:`keyring` or :mod:`backend.credentials`
* does not construct ``CloudClient(...)`` or call
  ``CloudClient.for_production(...)``
* does not import :mod:`backend.delivery` (M365 / SMTP is a Step 13
  concern; the orchestrator must not even *see* the email layer)
* does not import :mod:`fastapi` or the frontend (the orchestrator
  is the business-logic seam; HTTP routing belongs above it)

Companion fences:

* ``tests/runaway/test_no_direct_anthropic_imports.py`` — already
  rules out the SDK across the whole backend.
* ``tests/agents/test_no_direct_cloud_client_construction.py`` —
  agent-layer ban list.
* ``tests/jobs/test_no_keyring_or_credentials_imports.py`` —
  jobs-layer ban list.
* (This file.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ORCHESTRATOR_FILE = _REPO_ROOT / "backend" / "orchestrator.py"


_BANNED_IMPORTS: frozenset[str] = frozenset({
    "anthropic",
    "keyring",
    "fastapi",
})

_BANNED_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.credentials",
    "backend.delivery",
    "frontend",
)

_BANNED_CLOUDCLIENT_ATTRS: frozenset[str] = frozenset({
    "for_production",
})


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
    """Detect any direct ``CloudClient`` reference — construction or
    attribute access. Defence-in-depth: even if a hypothetical future
    import slipped past the prefix ban, the name would still be
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


def test_orchestrator_file_has_no_banned_imports_or_constructions() -> None:
    assert _ORCHESTRATOR_FILE.exists(), (
        f"expected {_ORCHESTRATOR_FILE} to exist — has it been moved?"
    )
    source = _ORCHESTRATOR_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_ORCHESTRATOR_FILE))
    offences = _import_offences(tree) + _cloudclient_offences(tree)
    assert not offences, (
        f"backend/orchestrator.py contains forbidden patterns: {offences}. "
        "The orchestrator must receive CloudClient via injection; it must "
        "not import the Anthropic SDK, the Keychain, backend.credentials, "
        "backend.delivery, frontend, or fastapi, and it must not "
        "construct CloudClient or call CloudClient.for_production."
    )
