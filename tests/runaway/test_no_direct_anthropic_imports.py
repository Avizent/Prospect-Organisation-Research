"""Import fence — only ``cloud_client.py`` may import the Anthropic SDK.

Walks every ``.py`` file under ``backend/`` and asserts that the only
file containing an ``import anthropic`` or ``from anthropic`` statement
is :mod:`backend.cost_control.cloud_client`.

This test enforces a hard rule from the handover: every Claude API
call goes through the guarded :class:`CloudClient` wrapper. A new
import elsewhere would make it possible to bypass the seven runaway
traps, so we fail the build if anyone tries.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"

# The single permitted file (relative to repo root) that may import
# the Anthropic SDK. Listed as a tuple to make adding/removing rare
# legitimate sites an explicit, reviewed change.
_ALLOWED = {
    Path("backend/cost_control/cloud_client.py"),
}


def _iter_backend_py_files() -> list[Path]:
    return sorted(p for p in _BACKEND.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_anthropic(source: str) -> bool:
    """True if *source* contains a top-level or nested anthropic import."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fall back to a regex if the file is somehow unparseable.
        return bool(re.search(r"^\s*(?:from\s+anthropic|import\s+anthropic)\b",
                              source, flags=re.MULTILINE))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "anthropic"
                or node.module.startswith("anthropic.")
            ):
                return True
    return False


@pytest.mark.parametrize(
    "py_file",
    _iter_backend_py_files(),
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_only_cloud_client_imports_anthropic(py_file: Path) -> None:
    rel = py_file.relative_to(_REPO_ROOT)
    source = py_file.read_text(encoding="utf-8")
    has_import = _imports_anthropic(source)
    if rel in _ALLOWED:
        # We require the allowed file to actually import anthropic, so
        # an accidental rename of the import site is also flagged.
        assert has_import, (
            f"{rel} is on the allow-list but does not import anthropic; "
            "did the SDK call site move?"
        )
    else:
        assert not has_import, (
            f"{rel} imports the Anthropic SDK directly. "
            "All Claude API calls must go through "
            "backend.cost_control.cloud_client.CloudClient."
        )
