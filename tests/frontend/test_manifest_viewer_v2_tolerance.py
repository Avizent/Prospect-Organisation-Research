"""Step 35 — the manifest viewer must remain version-agnostic.

The Step 34 viewer (``frontend/js/screens/manifest_viewer.js``) is a
schema-agnostic display: it reads ``manifest.schema_version`` and
echoes the value, but it never branches on the value. Step 35 bumps
the assembler's writer to ``schema_version: 2`` without touching the
viewer; this test pins the contract that the viewer must keep
rendering both v1 and v2 manifests without crashing.

Content-scan only — the repo has no JS test runner. We assert by
absence: the viewer source must not contain a hard-coded comparison
against ``1`` or ``2``.
"""

from __future__ import annotations

import pathlib

import pytest


_VIEWER_JS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "js" / "screens" / "manifest_viewer.js"
)


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


def test_viewer_does_not_hardcode_schema_version_value(viewer_src: str) -> None:
    """The viewer must not compare ``schema_version`` against a
    literal number. The user-facing rendering is value-agnostic so a
    future schema bump does not require a coordinated UI patch."""
    forbidden_comparisons = (
        "schema_version === 1",
        "schema_version === 2",
        "schema_version == 1",
        "schema_version == 2",
        "schema_version !== 1",
        "schema_version !== 2",
        "schema_version != 1",
        "schema_version != 2",
    )
    for needle in forbidden_comparisons:
        assert needle not in viewer_src, (
            f"manifest_viewer.js must not branch on schema_version; "
            f"found {needle!r}"
        )


def test_viewer_references_schema_version_field_name(viewer_src: str) -> None:
    """Sanity check — the viewer *does* display the field; it just
    does not branch on the value. This catches a regression where the
    field is dropped from the summary block entirely."""
    assert "schema_version" in viewer_src
