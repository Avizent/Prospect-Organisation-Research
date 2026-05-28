"""Deterministic export layer for canonical artefacts (Step 35 scaffold).

This package is the home for renderers that turn ``prospect_brief.md``
(the canonical Markdown source produced by ``backend.assembly``) into
derivative artefacts such as PDF and DOCX. Exports are a separate
lifecycle responsibility from assembly:

  - assembly writes ``prospect_brief.md`` + ``document_manifest.json``
  - exporters read those files and emit derivative artefacts under
    ``~/.ans-tool/jobs/{job_id}/exports/``

Step 35 ships **scaffolding only** — no renderers, no routes, no
generation. The package exposes three frozen provenance constants
plus the empty ``base``/``determinism`` building blocks that future
renderers will share. Generation lands atomically with retrieval in
Step 36 (PDF) and Step 37 (DOCX).

Provenance axes
---------------

Three independent provenance axes are recorded on every export entry
in ``document_manifest.json`` (``schema_version`` 2 ``exports[]``):

  - ``exporter_version`` — our wrapper code in this package.
  - ``template_version`` — our HTML/CSS/templates under
    ``backend/exporters/templates/``.
  - ``renderer.{name,version}`` — the third-party engine
    (``weasyprint``, ``python-docx``) read live at export time.

A change to any axis correctly invalidates the byte-equal determinism
guarantee for the next render — that is the point. Operators triaging
"why did this PDF change?" can look at each axis independently.

Hard fences (enforced in ``tests/exporters/test_no_forbidden_imports.py``)
-------------------------------------------------------------------------

  - ``backend.exporters.*`` may NOT import ``backend.agents``,
    ``backend.cost_control.cloud_client``, ``backend.delivery``,
    ``backend.security.keychain``, ``backend.assembly`` (lifecycle
    separation), ``anthropic``, ``keyring``, ``requests``, ``urllib``,
    ``httpx``, ``socket``.
  - ``backend.exporters`` may NOT import ``backend.assembly`` — and
    vice versa. Exporters consume the assembler's artefact via the
    storage layer, not by calling into the assembler.

Lifecycle separation note
-------------------------

This package deliberately lives at ``backend.exporters`` (top-level),
NOT under ``backend.assembly``. Assembly and export have different
inputs, different failure modes, different dependency sets, and
different review surfaces. The two transitional shims at
``backend/assembly/pdf_builder.py`` and
``backend/assembly/xlsx_builder.py`` (Step 35 only) re-export from
this package to reduce import-site churn during migration; they are
NOT a permanent home for export code.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------
#
# These three constants are written verbatim into every ``exports[]``
# manifest entry. They are deliberately pinned at the package level so
# a single import site (``from backend.exporters import EXPORTER_VERSION``)
# can read them, and so a future ``tests/exporters/test_package_scaffold.py``
# can assert their shape.
#
# ``EXPORTER_VERSION`` covers the Python code in this package.
# ``TEMPLATE_VERSION`` covers the HTML/CSS templates under
# ``backend/exporters/templates/`` (currently empty — populated in Step 36).
#
# Both follow semver. Bumping either is a deliberate determinism re-baseline.

EXPORTER_VERSION: str = "0.2.0"
TEMPLATE_VERSION: str = "0.1.0"


__all__ = [
    "EXPORTER_VERSION",
    "TEMPLATE_VERSION",
]
