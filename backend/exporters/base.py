"""Common building blocks for the export layer (Step 35 scaffold).

This module exposes the two types every concrete renderer
(``backend.exporters.pdf``, ``backend.exporters.docx``) will use:

  - ``ExportResult`` — the frozen value object a renderer returns
    when it has produced bytes successfully. Atomic ``os.replace``
    in the storage layer turns this into an on-disk file in a single
    operation.
  - ``ExportError`` — the fatal failure path. Concrete renderers raise
    this with a ``reason`` string that the route layer (Step 36) will
    map to an HTTP detail.

Step 35 ships these as plumbing only — no renderer consumes them
yet. Tests in ``tests/exporters/test_package_scaffold.py`` and
``tests/exporters/test_determinism_helpers.py`` cover the contract.
"""

from __future__ import annotations

from dataclasses import dataclass


# Canonical reason strings the route layer (Step 36) will pattern-match
# against to surface meaningful 4xx/5xx responses. Each is documented
# here so the renderer authors and the route author do not drift on
# spelling.
#
#   markdown_drift          — ``source_markdown_sha256`` no longer
#                             matches the manifest's current
#                             ``markdown_sha256``. Caller should
#                             re-assemble before retrying.
#   missing_brand_asset     — the ANS logo (a mandatory brand asset)
#                             is missing on disk. Fatal — operator
#                             must restore the asset.
#   missing_logo            — the prospect-specific logo is missing.
#                             Concrete renderers should degrade
#                             gracefully and emit a warning rather
#                             than raise this; reserved here for the
#                             rare case the renderer cannot degrade.
#   template_render_failed  — Jinja2 (or equivalent) raised during
#                             template rendering. Suggests a template
#                             bug; not retryable.
#   renderer_failed         — the third-party engine
#                             (``weasyprint``, ``python-docx``) raised
#                             during the final byte-emission step.
_VALID_REASONS = frozenset({
    "markdown_drift",
    "missing_brand_asset",
    "missing_logo",
    "template_render_failed",
    "renderer_failed",
})


@dataclass(frozen=True)
class ExportResult:
    """The successful outcome of a single render pass.

    Fields
    ------
    format
        Short token identifying the output format (e.g. ``"pdf"``,
        ``"docx"``). Mirrors the ``exports[].format`` field in the
        Step 29 manifest.
    bytes
        Final on-disk bytes — already post-determinism-scrubbed by
        the renderer. The caller (storage layer) writes these
        verbatim and computes the manifest ``sha256`` over the same
        buffer.
    sha256
        Hex-encoded SHA-256 of ``bytes``. Renderers compute this
        once so the storage layer does not have to re-hash. 64 chars.
    warnings
        Tuple of human-readable warning strings the renderer
        accumulated (e.g. "prospect logo missing — rendered without
        co-brand"). Surfaces in ``exports[].warnings`` on the
        manifest.
    """

    format: str
    bytes: bytes
    sha256: str
    warnings: tuple[str, ...]


class ExportError(Exception):
    """Fatal renderer failure.

    Raised by concrete renderers when they cannot produce valid
    bytes. Carries a ``reason`` string drawn from a small fixed
    vocabulary (see ``_VALID_REASONS``) so the Step 36 route layer
    can map each cleanly to an HTTP detail. Unknown reasons raise
    ``ValueError`` at construction time — we want a typo to fail
    loudly in tests, not silently at the HTTP boundary.
    """

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        if reason not in _VALID_REASONS:
            raise ValueError(
                f"ExportError reason {reason!r} is not a known reason; "
                f"valid reasons: {sorted(_VALID_REASONS)}"
            )
        self.reason = reason
        self.detail = detail
        # ``super().__init__`` builds the Exception's ``args`` tuple
        # — pass a human-readable message so ``str(err)`` is useful
        # in logs.
        message = f"{reason}" if detail is None else f"{reason}: {detail}"
        super().__init__(message)


__all__ = [
    "ExportResult",
    "ExportError",
]
