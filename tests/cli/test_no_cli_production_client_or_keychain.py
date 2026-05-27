"""Static fence around :mod:`backend.cli`.

The Step 9c amendment moved the scripted-replay client out of
``backend.cost_control`` and into ``backend.cli`` so it could never be
imported by a production route by accident. That choice is only
load-bearing if ``backend/cli.py`` itself stays clean of the
production-credential and external-network surface.

This fence proves that ``backend/cli.py``:

* does **not** import the Anthropic SDK
* does **not** import :mod:`keyring`
* does **not** import :mod:`backend.credentials`
* does **not** import :mod:`backend.delivery` (M365 / SMTP is Step 13)
* does **not** import :mod:`backend.assembly`
* does **not** import :mod:`fastapi`
* does **not** import any top-level ``frontend`` package
* does **not** reference ``CloudClient(...)`` (construction)
* does **not** reference ``CloudClient.for_production`` (the
  production-credentials classmethod)

Note on ``backend.cost_control.cloud_client``
---------------------------------------------

``backend/cli.py`` *does* import :class:`CloudCallResult` from
``backend.cost_control.cloud_client`` so the scripted client can
return the typed result the protocol promises. That is intentional
and aligned with the orchestrator fence
(``tests/orchestrator/test_no_production_client_or_keychain.py``):
the forbidden symbol is the *name* ``CloudClient``, not the module
that defines it. Importing a sibling symbol from the same module is
how the type system stays single-sourced.

Companion fences:
* ``tests/runaway/test_no_direct_anthropic_imports.py`` — global
  ban on the Anthropic SDK outside ``cloud_client.py``.
* ``tests/orchestrator/test_no_production_client_or_keychain.py`` —
  same shape, against ``backend/orchestrator.py``.
* ``tests/agents/test_no_direct_cloud_client_construction.py`` —
  agent-layer ban.
* ``tests/jobs/test_no_keyring_or_credentials_imports.py`` —
  jobs-layer ban.
* (This file.)
"""

from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_FILE = _REPO_ROOT / "backend" / "cli.py"


_BANNED_IMPORTS: frozenset[str] = frozenset({
    "anthropic",
    "keyring",
    "fastapi",
})

_BANNED_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.credentials",
    "backend.delivery",
    "backend.assembly",
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
            # Also forbid importing the *name* ``CloudClient`` itself —
            # the amendment is explicit that the CLI must never import
            # or construct CloudClient. Importing CloudCallResult from
            # the same module is fine; importing the name CloudClient
            # is not.
            for alias in node.names:
                if alias.name == "CloudClient":
                    offences.append(
                        f"from {module} import CloudClient"
                    )
    return offences


def _cloudclient_offences(tree: ast.Module) -> list[str]:
    """Detect any direct ``CloudClient`` reference — construction or
    attribute access (e.g. ``CloudClient.for_production``)."""
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
        elif isinstance(node, ast.Name) and node.id == "CloudClient":
            # Belt-and-braces: any bare reference to the name CloudClient
            # is also banned. Imports of the name are caught in
            # _import_offences.
            offences.append("bare name 'CloudClient'")
    return offences


def test_cli_file_has_no_banned_imports_or_constructions() -> None:
    assert _CLI_FILE.exists(), (
        f"expected {_CLI_FILE} to exist — has it been moved?"
    )
    source = _CLI_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_CLI_FILE))
    offences = _import_offences(tree) + _cloudclient_offences(tree)
    assert not offences, (
        f"backend/cli.py contains forbidden patterns: {offences}. "
        "The CLI must not import the Anthropic SDK, the Keychain, "
        "backend.credentials, backend.delivery, backend.assembly, "
        "frontend, or fastapi, and must not reference CloudClient or "
        "CloudClient.for_production. The scripted dry-run client lives "
        "inside backend/cli.py and uses CloudCallResult only."
    )
