"""Transitional compatibility shim for the relocated structured-data exporter.

Step 35 moves export responsibilities out of ``backend.assembly`` and
into a new top-level package, ``backend.exporters``. This file used
to contain a "TODO: implement in step 11" stub for an XLSX builder
covering objections/contacts tables.

Structured-data (XLSX) exports are a *separate* concern from the
canonical-Markdown → document pipeline that drives PDF/DOCX, and
they are deliberately deferred past the initial Step 35–37 export
architecture rollout. There is no XLSX implementation in
``backend.exporters`` yet; this shim re-exports the public
provenance constants from the new home so callers that imported
from here keep resolving::

    from backend.assembly.xlsx_builder import EXPORTER_VERSION

…to the same value as the canonical import path::

    from backend.exporters import EXPORTER_VERSION

There is no XLSX rendering code here. The shim does NOT pull in
openpyxl or any renderer module — importing it is cheap and
side-effect-free. Once all transitional callers have been migrated
to ``backend.exporters`` and the XLSX rollout is scoped, this shim
is safe to delete.
"""

from __future__ import annotations

# Re-export the public provenance constants only. The concrete renderer
# is intentionally NOT imported here so this shim stays import-cheap and
# does not couple ``backend.assembly`` to openpyxl or any renderer
# dependency.
from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION


__all__ = [
    "EXPORTER_VERSION",
    "TEMPLATE_VERSION",
]
