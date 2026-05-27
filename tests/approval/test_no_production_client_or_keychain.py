"""Static fence — ``backend.jobs.approval`` must not import forbidden modules.

The approval-gate module is a *boundary* — it sits between Stage 1
agents and Stage 2 generation, and the easiest way for that
boundary to drift into something it isn't is for a future PR to
add an import that pulls in:

* the Anthropic SDK (``anthropic`` or ``backend.tools.cloud_client``)
  — approval is a state-machine module, not an agent caller;
* Keychain access (``keyring``) — approval has no business reading
  secrets;
* FastAPI (``fastapi``) — approval is a pure backend module; the
  HTTP surface lives elsewhere;
* M365 delivery (``backend.delivery``) — recipient-lock surface is
  separate;
* Document assembly (``backend.assembly``) — that's Stage 2;
* the frontend (no submodule should import a sibling routes
  module, but if such a thing were to appear, we'd catch it).

We use AST analysis rather than a runtime check so the test doesn't
need to actually execute the production module. A future refactor
that defers an import behind a function call would not slip past
this fence — every ``import`` and ``from`` statement is inspected,
no matter where it appears in the file.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Forbidden import roots
# ---------------------------------------------------------------------------
#
# Each entry is a prefix; ``backend.delivery.m365`` is forbidden via
# the prefix ``backend.delivery``. We deliberately don't list
# ``backend.tools`` outright — there are non-cloud helpers under
# that package — but ``cloud_client`` is the only one that wraps
# the Anthropic SDK, so we forbid it explicitly.

_FORBIDDEN_PREFIXES = (
    "anthropic",
    "keyring",
    "fastapi",
    "backend.delivery",
    "backend.assembly",
    "backend.tools.cloud_client",
    # Frontend is not a Python package, but if any "frontend"
    # module ever appears as a top-level Python import we catch it.
    "frontend",
)

_APPROVAL_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend"
    / "jobs"
    / "approval.py"
)


def _imported_modules(source: str) -> set[str]:
    """Return every fully-qualified module name imported in ``source``.

    Handles both ``import X.Y`` and ``from X.Y import Z`` forms.
    ``from . import x`` (relative) yields no module name — we don't
    care about intra-package relatives for this fence.
    """
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
def imported() -> set[str]:
    source = _APPROVAL_PATH.read_text(encoding="utf-8")
    return _imported_modules(source)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
def test_no_forbidden_imports(imported: set[str], forbidden: str) -> None:
    leaked = [
        name
        for name in imported
        if name == forbidden or name.startswith(forbidden + ".")
    ]
    assert not leaked, (
        f"backend.jobs.approval must not import {forbidden}; "
        f"found: {leaked}"
    )


def test_no_cloud_client_protocol_imported(imported: set[str]) -> None:
    """Defence-in-depth: even the protocol class is forbidden — if
    approval.py needed to call a model, the whole module would need
    a redesign, not a sneaky import."""
    leaked = [n for n in imported if "cloud_client" in n.lower()]
    assert not leaked, (
        f"backend.jobs.approval must not reference cloud_client; "
        f"found: {leaked}"
    )


def test_approval_module_is_importable_in_isolation() -> None:
    """A second-order check: the imports the module *does* use must
    not transitively depend on something forbidden. Importing the
    module from this test (which runs without the Anthropic SDK
    installed in CI for runaway tests, etc.) would crash if it did."""
    import importlib

    module = importlib.import_module("backend.jobs.approval")
    # Spot-check the public surface so this isn't just an import test.
    assert hasattr(module, "approve")
    assert hasattr(module, "apply_briefing_edit")
    assert hasattr(module, "open_for_editing")
    assert hasattr(module, "request_regeneration")
    assert hasattr(module, "complete_regeneration")
    assert hasattr(module, "fail_regeneration")
    assert hasattr(module, "BriefingPatch")
    assert hasattr(module, "BriefingSection")
