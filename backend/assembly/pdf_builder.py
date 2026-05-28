"""Transitional compatibility shim for the relocated PDF exporter.

Step 35 moves export responsibilities out of ``backend.assembly`` and
into a new top-level package, ``backend.exporters``, to reflect the
clean separation between *assembly* (write canonical Markdown +
manifest) and *export* (turn canonical Markdown into derivative
artefacts such as PDF/DOCX).

This file used to contain a "TODO: implement in step 11" stub for a
PDF builder. The implementation now lives at
``backend.exporters.pdf`` (populated in Step 36). To reduce the
chance of stale imports in transitional callers, this module
re-exports the public provenance constants from the new home so any
import line like::

    from backend.assembly.pdf_builder import EXPORTER_VERSION

resolves to the same value as the canonical import path::

    from backend.exporters import EXPORTER_VERSION

There is no PDF rendering code here. The shim does NOT pull in
WeasyPrint, Jinja2, or any renderer module — importing it is cheap
and side-effect-free. Once all transitional callers have been
migrated to ``backend.exporters``, this shim is safe to delete.
"""

from __future__ import annotations

# Re-export the public provenance constants only. The concrete renderer
# (``backend.exporters.pdf``) is intentionally NOT imported here so this
# shim stays import-cheap and does not couple ``backend.assembly`` to
# WeasyPrint or any renderer dependency.
from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION


__all__ = [
    "EXPORTER_VERSION",
    "TEMPLATE_VERSION",
]
