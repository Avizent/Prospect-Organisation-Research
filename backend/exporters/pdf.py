"""Deterministic PDF renderer (Step 36).

Turns the canonical ``prospect_brief.md`` into a byte-reproducible PDF
file. The renderer is a pure function:

  - Inputs: Markdown text + the markdown SHA-256 the caller already
    knows + a company name + the two logo byte blobs + an optional
    fixed-time sentinel (only used inside the template, never
    embedded in the PDF metadata).
  - Output: an :class:`backend.exporters.base.ExportResult` with the
    final PDF bytes, their SHA-256, and any warnings.

No I/O happens here — the caller (the route layer) writes the bytes
to disk atomically. Keeping the renderer I/O-free makes determinism
testable in pure unit tests and keeps the storage layout decoupled
from the rendering pipeline.

Pipeline
--------

  1. Convert Markdown → HTML fragment via
     :mod:`backend.exporters.markdown_to_html`. This is the
     ``markdown_renderer`` provenance axis.
  2. Render the brand-chrome HTML wrapper via Jinja2 against
     ``backend/exporters/templates/export_pdf.html``. The logos are
     embedded as base64 ``data:`` URIs so there is no URL fetcher
     surface (no network, no file resolver).
  3. Run WeasyPrint over the composed HTML + ``print.css`` to produce
     raw PDF bytes. This is the ``renderer`` provenance axis.
  4. Pass the bytes through
     :func:`backend.exporters.determinism.strip_pdf_dates` to
     scrub ``/CreationDate`` / ``/ModDate``, pin ``/Producer`` /
     ``/Creator``, drop the XMP packet, and set a deterministic
     ``/ID`` derived from ``markdown_sha256``.

Determinism contract
--------------------

Two runs of :func:`render_pdf` with identical inputs MUST produce
byte-equal output. This is pinned by
``tests/exporters/test_pdf_renderer.py`` (unit-level), and by
``tests/exporters/test_pdf_determinism_integration.py`` (end-to-end
through the route layer). Sources of drift the renderer rules out:

  - clock-derived metadata (stripped in step 4)
  - clock-derived PDF /ID (replaced in step 4)
  - host-font selection (CSS pins ``DejaVu Sans`` only)
  - Jinja2 environment timestamps (we use ``StrictUndefined`` + no
    extensions, and the template emits no clock-derived content)

Failure surface
---------------

``ExportError`` is raised with one of the five reasons documented in
:mod:`backend.exporters.base`:

  - ``missing_brand_asset`` — the ANS logo bytes are empty. Fatal:
    the brief cannot ship unbranded.
  - ``missing_logo``        — reserved for the future "no degraded
    fallback" code path. The current renderer degrades to "no
    prospect logo, emit warning" instead, so this is not raised in
    Step 36.
  - ``template_render_failed`` — Jinja2 raised. Suggests a template
    or data-shape bug.
  - ``renderer_failed``     — WeasyPrint or pikepdf raised.
  - ``markdown_drift``      — reserved for the route layer (the
    renderer is given the markdown text directly so it has no way
    to detect drift; the route compares manifest vs. on-disk
    sha256 before calling the renderer).
"""

from __future__ import annotations

import base64
import pathlib
from dataclasses import dataclass
from datetime import datetime

import jinja2

from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.exporters.base import ExportError, ExportResult
from backend.exporters.determinism import sha256_hex, strip_pdf_dates
from backend.exporters.markdown_to_html import (
    MARKDOWN_RENDERER_NAME,
    MARKDOWN_RENDERER_VERSION,
    markdown_to_html,
)


# ---------------------------------------------------------------------------
# Renderer provenance — read live so a WeasyPrint upgrade surfaces in the
# manifest's ``exports[].renderer.version`` field on the next render.
# ---------------------------------------------------------------------------

def _renderer_version() -> str:
    """Return the WeasyPrint package version string.

    Imported lazily so this module's import-time cost stays light;
    the static fence in ``tests/exporters/test_no_forbidden_imports``
    parametrises the scan but ``weasyprint`` is not on the forbidden
    list."""
    import weasyprint as _weasyprint  # noqa: WPS433 — lazy import
    return _weasyprint.__version__


RENDERER_NAME: str = "weasyprint"


# ---------------------------------------------------------------------------
# Template loader — pinned to this package's templates/ directory so a
# future repo-relative path change does not silently resolve a different
# file.
# ---------------------------------------------------------------------------

_TEMPLATES_DIR: pathlib.Path = (
    pathlib.Path(__file__).resolve().parent / "templates"
)
_TEMPLATE_NAME: str = "export_pdf.html"
_STYLESHEET_NAME: str = "print.css"


def _jinja_env() -> jinja2.Environment:
    """Build the Jinja2 environment used by the PDF template.

    ``StrictUndefined`` raises rather than silently emitting empty
    strings — a missing variable is a template bug, not "render
    blank". ``autoescape`` is on for HTML files; ``body_html`` is
    explicitly marked ``| safe`` in the template because it is
    already escaped HTML from the Markdown converter.
    """
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
        undefined=jinja2.StrictUndefined,
        # No extensions — extensions are syntax-expanding plugins
        # that could introduce non-determinism (e.g. ``i18n``).
        extensions=[],
    )


# ---------------------------------------------------------------------------
# Public render entry-point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RenderInputs:
    """Internal collapse of the public render kwargs — lets the
    pipeline pass a single value object between helpers instead of
    six positional kwargs."""

    job_id: str
    markdown_text: str
    markdown_sha256: str
    company_name: str
    ans_logo_bytes: bytes
    prospect_logo_bytes: bytes
    now: datetime


def render_pdf(
    *,
    job_id: str,
    markdown_text: str,
    markdown_sha256: str,
    company_name: str,
    ans_logo_bytes: bytes,
    prospect_logo_bytes: bytes,
    now: datetime,
) -> ExportResult:
    """Render ``markdown_text`` into a deterministic PDF.

    See module docstring for the full pipeline and determinism
    contract. The function is I/O-free: the caller writes the
    returned bytes to disk.
    """
    inputs = _RenderInputs(
        job_id=job_id,
        markdown_text=markdown_text,
        markdown_sha256=markdown_sha256,
        company_name=company_name,
        ans_logo_bytes=ans_logo_bytes,
        prospect_logo_bytes=prospect_logo_bytes,
        now=now,
    )

    warnings: list[str] = []

    # 1. Validate brand assets.
    if not inputs.ans_logo_bytes:
        raise ExportError(
            "missing_brand_asset",
            detail="ANS logo bytes are empty; cannot render without brand",
        )
    if not inputs.prospect_logo_bytes:
        warnings.append(
            "prospect logo missing — rendered without co-brand"
        )

    # 2. Markdown → HTML fragment.
    body_html = markdown_to_html(inputs.markdown_text)

    # 3. Template render.
    try:
        full_html = _render_template(inputs, body_html=body_html)
    except jinja2.TemplateError as exc:
        raise ExportError(
            "template_render_failed", detail=str(exc),
        ) from exc

    # 4. WeasyPrint → raw PDF bytes.
    try:
        raw_pdf = _weasyprint_html_to_pdf(full_html)
    except Exception as exc:  # noqa: BLE001 — WeasyPrint raises many shapes
        raise ExportError(
            "renderer_failed",
            detail=f"weasyprint: {exc}",
        ) from exc

    # 5. Strip dates + pin /Producer + deterministic /ID.
    try:
        final_pdf = strip_pdf_dates(
            raw_pdf,
            id_seed=bytes.fromhex(inputs.markdown_sha256),
        )
    except Exception as exc:  # noqa: BLE001 — pikepdf raises many shapes
        raise ExportError(
            "renderer_failed",
            detail=f"pikepdf: {exc}",
        ) from exc

    return ExportResult(
        format="pdf",
        bytes=final_pdf,
        sha256=sha256_hex(final_pdf),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _render_template(
    inputs: _RenderInputs, *, body_html: str,
) -> str:
    """Render the brand-chrome wrapper template against ``body_html``."""
    env = _jinja_env()
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(
        company_name=inputs.company_name,
        ans_logo_b64=base64.b64encode(inputs.ans_logo_bytes).decode("ascii"),
        prospect_logo_b64=(
            base64.b64encode(inputs.prospect_logo_bytes).decode("ascii")
            if inputs.prospect_logo_bytes
            else ""
        ),
        body_html=body_html,
    )


def _weasyprint_html_to_pdf(html_text: str) -> bytes:
    """Run WeasyPrint over ``html_text`` + the pinned print stylesheet.

    ``base_url`` is left unset and ``string`` is used for both the
    HTML and the CSS — neither carries any external reference, so
    there is no URL fetcher activity (no network, no filesystem
    walk). Tested in ``test_no_subprocess_calls`` /
    ``test_no_url_literals`` in the exporter package.
    """
    import weasyprint  # noqa: WPS433 — lazy import (heavy native dep)

    css_text = (_TEMPLATES_DIR / _STYLESHEET_NAME).read_text(encoding="utf-8")
    html_obj = weasyprint.HTML(string=html_text)
    css_obj = weasyprint.CSS(string=css_text)
    return html_obj.write_pdf(stylesheets=[css_obj])


# ---------------------------------------------------------------------------
# Provenance projection
# ---------------------------------------------------------------------------

def renderer_provenance() -> dict[str, str]:
    """Return the ``renderer`` provenance dict for the manifest entry."""
    return {"name": RENDERER_NAME, "version": _renderer_version()}


def markdown_renderer_provenance() -> dict[str, str]:
    """Return the ``markdown_renderer`` provenance dict for the manifest."""
    return {
        "name": MARKDOWN_RENDERER_NAME,
        "version": MARKDOWN_RENDERER_VERSION,
    }


__all__ = [
    "EXPORTER_VERSION",
    "TEMPLATE_VERSION",
    "RENDERER_NAME",
    "render_pdf",
    "renderer_provenance",
    "markdown_renderer_provenance",
]
