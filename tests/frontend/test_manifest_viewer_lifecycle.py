"""Step 37 — manifest viewer renders the lifecycle projection.

Content scans over ``frontend/js/screens/manifest_viewer.js`` pin the
documented UX:

  * a prominent error-severity banner for ``source_markdown_drift``
    (amendment 1) — class ``manifest-banner manifest-banner-error
    source-markdown-drift``, surfaced ABOVE every other panel,
  * a per-export stale badge with class ``export-stale-badge`` rendered
    next to the Download link when an entry is stale,
  * the Generate button label flips to "Regenerate PDF" when the entry
    is stale,
  * the summary block shows BOTH the manifest's recorded markdown
    SHA-256 AND the on-disk SHA-256, paired so an operator can compare
    them by eye, with a ``.sha-mismatch`` class when they diverge.

We assert by content scan (the repo has no JS test runner).
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VIEWER_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "manifest_viewer.js"


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source Markdown drift banner (amendment 1 — error severity)
# ---------------------------------------------------------------------------


def test_viewer_renders_source_markdown_drift_banner_class(
    viewer_src: str,
) -> None:
    """Class string ``manifest-banner-error`` MUST be present."""
    assert "manifest-banner-error" in viewer_src


def test_drift_banner_carries_source_markdown_drift_class_token(
    viewer_src: str,
) -> None:
    """The banner element carries a ``source-markdown-drift`` class so
    operators (and tests) can locate it without depending on the message."""
    assert "source-markdown-drift" in viewer_src


def test_drift_banner_uses_alert_role(viewer_src: str) -> None:
    """Error-severity banners must be announced — role=alert."""
    # Match the alert role on the banner element.
    m = re.search(
        r"manifest-banner-error.*?role:\s*[\"']alert[\"']",
        viewer_src,
        flags=re.DOTALL,
    )
    assert m, "drift banner must carry role=alert"


def test_drift_banner_references_lifecycle_source_markdown_drift_field(
    viewer_src: str,
) -> None:
    """Sanity check — the viewer reads the field from the lifecycle dict."""
    assert "source_markdown_drift" in viewer_src


# ---------------------------------------------------------------------------
# Per-export stale badge
# ---------------------------------------------------------------------------


def test_viewer_renders_export_stale_badge_class(viewer_src: str) -> None:
    assert "export-stale-badge" in viewer_src


def test_stale_badge_renders_only_when_entry_is_stale(viewer_src: str) -> None:
    """The stale badge sits inside an ``entryIsStale`` branch — assert the
    branch token is present so a future refactor that removes the guard
    fails this test."""
    assert "entryIsStale" in viewer_src


# ---------------------------------------------------------------------------
# Regenerate label
# ---------------------------------------------------------------------------


def test_generate_label_flips_to_regenerate_when_stale(viewer_src: str) -> None:
    """The Generate button label becomes "Regenerate PDF" when stale."""
    assert "Regenerate PDF" in viewer_src
    assert "Generate PDF" in viewer_src


def test_generate_and_regenerate_share_a_single_button_class(
    viewer_src: str,
) -> None:
    """Only the label changes — the button class stays ``btn-generate-pdf``."""
    # Find every ``class: "..."`` literal that mentions either label
    # and assert ``btn-generate-pdf`` is the only relevant class token.
    # The label flip lives on the text/textContent assignments, not the
    # class string.
    pattern = re.compile(
        r'class:\s*"([^"]*btn-(?:re)?generate-pdf[^"]*)"'
    )
    for match in pattern.finditer(viewer_src):
        literal = match.group(1)
        assert "btn-regenerate-pdf" not in literal, (
            f"Regenerate must not introduce a new button class; got {literal!r}"
        )


# ---------------------------------------------------------------------------
# Summary block — paired SHA rows
# ---------------------------------------------------------------------------


def test_summary_renders_both_manifest_and_source_sha_rows(
    viewer_src: str,
) -> None:
    assert "Manifest Markdown SHA-256" in viewer_src
    assert "Source Markdown SHA-256" in viewer_src


def test_summary_tags_diverging_shas_with_mismatch_class(viewer_src: str) -> None:
    """When the two SHAs diverge, both rows carry a ``.sha-mismatch`` token."""
    assert "sha-mismatch" in viewer_src


def test_summary_reads_on_disk_sha_from_lifecycle_manifest(
    viewer_src: str,
) -> None:
    """The on-disk SHA value comes from
    ``lifecycle.manifest.on_disk_markdown_sha256``."""
    assert "on_disk_markdown_sha256" in viewer_src


# ---------------------------------------------------------------------------
# Read-only contract — no auto-regenerate
# ---------------------------------------------------------------------------


def test_viewer_never_auto_regenerates_on_stale_state(viewer_src: str) -> None:
    """A stale signal must not auto-trigger a regenerate.

    The Generate button calls ``api.runPdfExport`` from a click handler;
    that token must not appear inside any non-handler scope. We assert
    the token only appears once — inside ``addEventListener("click", ...)``.
    """
    occurrences = viewer_src.count("api.runPdfExport")
    assert occurrences == 1, (
        f"api.runPdfExport must be called exactly once (from the click "
        f"handler); found {occurrences} occurrences"
    )


def test_viewer_does_not_swap_download_into_regenerate_path(
    viewer_src: str,
) -> None:
    """A stale entry is STILL downloadable — the Download anchor stays put."""
    assert "btn-download-pdf" in viewer_src
    # The download href stays pointed at the GET retrieval path.
    m = re.search(
        r'class:\s*"btn-download-pdf".*?href:\s*`([^`]+)`',
        viewer_src,
        flags=re.DOTALL,
    )
    assert m, "download anchor must remain on the stale path too"
    href = m.group(1)
    assert "/exports/pdf" in href


# ---------------------------------------------------------------------------
# Read-only — viewer does not POST to /exports/validate or /brief/assemble
# ---------------------------------------------------------------------------


def test_viewer_does_not_post_to_validate_or_assemble(viewer_src: str) -> None:
    """The viewer reads the lifecycle field off the manifest envelope; it
    never calls ``api.getExportValidation`` or ``api.assembleBrief``.

    The docstring at the top of the file references ``/brief/assemble``
    as the route the viewer must NOT call; we therefore look for the JS
    API-client wrappers by name rather than scanning for the path
    string itself.
    """
    assert "api.getExportValidation" not in viewer_src
    assert "api.assembleBrief" not in viewer_src
