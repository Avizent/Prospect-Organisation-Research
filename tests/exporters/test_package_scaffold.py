"""Scaffold-level tests for the new ``backend.exporters`` package (Step 35).

Step 35 ships *only* scaffolding for the deterministic export layer:

  - the package exists and is importable
  - the three provenance constants are present and semver-shaped
  - ``ExportResult`` / ``ExportError`` exist with the documented shape
  - the templates directory exists (renderers populate it in Step 36+)

No renderer behaviour is covered here — there are no renderers yet.
The byte-equal determinism contract is tested where the renderers
themselves are tested (Step 36 for PDF, Step 37 for DOCX).
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


# ---------------------------------------------------------------------------
# Package import + version constants
# ---------------------------------------------------------------------------


def test_backend_exporters_is_importable() -> None:
    module = importlib.import_module("backend.exporters")
    assert module is not None


def test_exporter_version_is_semver_string() -> None:
    from backend.exporters import EXPORTER_VERSION

    assert isinstance(EXPORTER_VERSION, str)
    assert _SEMVER_RE.match(EXPORTER_VERSION), (
        f"EXPORTER_VERSION must be semver-shaped; got {EXPORTER_VERSION!r}"
    )


def test_template_version_is_semver_string() -> None:
    from backend.exporters import TEMPLATE_VERSION

    assert isinstance(TEMPLATE_VERSION, str)
    assert _SEMVER_RE.match(TEMPLATE_VERSION), (
        f"TEMPLATE_VERSION must be semver-shaped; got {TEMPLATE_VERSION!r}"
    )


def test_versions_are_separate_provenance_axes() -> None:
    """``EXPORTER_VERSION`` and ``TEMPLATE_VERSION`` are *independent*
    axes — they must be two distinct names exposed by the package, even
    if their values happen to coincide at any point in time."""
    import backend.exporters as exporters

    assert hasattr(exporters, "EXPORTER_VERSION")
    assert hasattr(exporters, "TEMPLATE_VERSION")


# ---------------------------------------------------------------------------
# ExportResult contract
# ---------------------------------------------------------------------------


def test_export_result_is_frozen_dataclass_with_expected_fields() -> None:
    from dataclasses import fields, is_dataclass

    from backend.exporters.base import ExportResult

    assert is_dataclass(ExportResult)
    field_names = {f.name for f in fields(ExportResult)}
    assert field_names == {"format", "bytes", "sha256", "warnings"}


def test_export_result_instances_are_immutable() -> None:
    from backend.exporters.base import ExportResult

    result = ExportResult(
        format="pdf",
        bytes=b"%PDF-1.7\n",
        sha256="a" * 64,
        warnings=(),
    )
    with pytest.raises(Exception):
        # ``frozen=True`` raises ``FrozenInstanceError`` (a subclass
        # of ``AttributeError``) on attribute assignment.
        result.format = "docx"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExportError contract
# ---------------------------------------------------------------------------


def test_export_error_accepts_documented_reasons() -> None:
    from backend.exporters.base import ExportError

    for reason in (
        "markdown_drift",
        "missing_brand_asset",
        "missing_logo",
        "template_render_failed",
        "renderer_failed",
    ):
        err = ExportError(reason)
        assert err.reason == reason
        assert reason in str(err)


def test_export_error_rejects_unknown_reason() -> None:
    from backend.exporters.base import ExportError

    with pytest.raises(ValueError):
        ExportError("not_a_real_reason")


def test_export_error_optional_detail_is_surfaced_in_message() -> None:
    from backend.exporters.base import ExportError

    err = ExportError("markdown_drift", detail="sha mismatch")
    assert err.detail == "sha mismatch"
    assert "sha mismatch" in str(err)


# ---------------------------------------------------------------------------
# Templates directory
# ---------------------------------------------------------------------------


def test_templates_directory_exists() -> None:
    """Step 35 reserves ``backend/exporters/templates/`` so renderers
    that land in Step 36+ have a stable location for their HTML/CSS."""
    pkg_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "backend"
        / "exporters"
        / "templates"
    )
    assert pkg_path.exists()
    assert pkg_path.is_dir()
