"""Step 35 — the two transitional compatibility shims must keep working.

``backend/assembly/pdf_builder.py`` and
``backend/assembly/xlsx_builder.py`` are Step 35-only shims that
re-export the public provenance constants from
``backend.exporters``. They exist to reduce import-site churn while
the architecture migrates; this test pins their contract so a future
edit that quietly broke a transitional import would fail here, not
in a downstream caller.
"""

from __future__ import annotations

import importlib


def test_pdf_builder_shim_re_exports_versions_from_backend_exporters() -> None:
    import backend.exporters as exporters
    shim = importlib.import_module("backend.assembly.pdf_builder")

    assert shim.EXPORTER_VERSION == exporters.EXPORTER_VERSION
    assert shim.TEMPLATE_VERSION == exporters.TEMPLATE_VERSION


def test_xlsx_builder_shim_re_exports_versions_from_backend_exporters() -> None:
    import backend.exporters as exporters
    shim = importlib.import_module("backend.assembly.xlsx_builder")

    assert shim.EXPORTER_VERSION == exporters.EXPORTER_VERSION
    assert shim.TEMPLATE_VERSION == exporters.TEMPLATE_VERSION


def test_shims_do_not_re_export_renderer_modules() -> None:
    """The shims must NOT expose anything renderer-shaped.

    A future contributor might be tempted to broaden the re-export
    surface ("just re-export the whole package, easy"). That would
    couple ``backend.assembly`` back to ``backend.exporters`` and
    defeat the lifecycle separation Step 35 is establishing.
    """
    for shim_name in ("pdf_builder", "xlsx_builder"):
        shim = importlib.import_module(f"backend.assembly.{shim_name}")
        for forbidden in (
            "ExportResult",
            "ExportError",
            "canonicalize_zip",
            "strip_pdf_dates",
            "render_pdf",
            "render_docx",
        ):
            assert not hasattr(shim, forbidden), (
                f"shim {shim_name!r} must not re-export {forbidden!r}"
            )
