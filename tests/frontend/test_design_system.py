"""Step 46 — design-system CSS layer (frontend/styles/design.css).

Content scans only — no CSS parser, no browser. Pins the required
CSS custom properties and class names that must be present in
design.css so future edits cannot silently remove tokens relied on
by screen-level JS.

Coverage:
* All 17 required CSS custom properties are declared
* All required class names are declared
* No external URL references (no CDN, no external fonts)
* No @import (all styles must live in this file)
* No forbidden delivery/system strings
* @media print block present (A4/export isolation)
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESIGN_CSS = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def css_src() -> str:
    assert _DESIGN_CSS.exists(), (
        "frontend/styles/design.css must exist — Step 46 has not been implemented"
    )
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. CSS custom properties (design tokens)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    "--bg",
    "--surface",
    "--surface-muted",
    "--text",
    "--text-muted",
    "--border",
    "--primary",
    "--primary-dark",
    "--success",
    "--warning",
    "--danger",
    "--accent",
    "--teal",
    "--radius",
    "--radius-sm",
    "--shadow",
    "--shadow-md",
])
def test_token_declared(css_src: str, token: str) -> None:
    """Each required CSS variable must be declared in the :root block."""
    assert token in css_src, (
        f"design.css must declare CSS custom property {token!r}"
    )


# ---------------------------------------------------------------------------
# 2. Required class names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    # App shell
    "app-header",
    "app-logo",
    "app-logo-icon",
    "app-logo-name",
    "app-logo-sub",
    "app-stepper",
    "app-step",
    "app-step--active",
    "app-step--complete",
    "app-active-job",
    "app-mode-chip",
    "app-operator",
    "app-shell-right",
    # Page layout
    "page",
    "page-header",
    "page-title",
    "page-subtitle",
    # Cards
    "card",
    "card-header",
    "card-body",
    # Buttons
    "btn",
    "btn-primary",
    "btn-secondary",
    "btn-ghost",
    # Badges
    "badge",
    "badge-success",
    "badge-warning",
    "badge-muted",
    # Alerts
    "alert",
    "alert-info",
    "alert-warning",
    "alert-error",
])
def test_class_declared(css_src: str, cls: str) -> None:
    """Each required class must appear as a CSS selector in design.css."""
    assert f".{cls}" in css_src, (
        f"design.css must declare CSS class .{cls!r}"
    )


# ---------------------------------------------------------------------------
# 3. No external resources
# ---------------------------------------------------------------------------

def test_no_external_url(css_src: str) -> None:
    """No url() pointing to an external domain — no external fonts or CDN."""
    matches = re.findall(r"url\(['\"]?(https?://[^'\")]+)", css_src)
    assert not matches, (
        f"design.css must not load external URLs; found: {matches}"
    )


def test_no_at_import(css_src: str) -> None:
    """No @import — all styles must live directly in design.css."""
    assert "@import" not in css_src, (
        "design.css must not use @import — embed all rules directly"
    )


# ---------------------------------------------------------------------------
# 4. No forbidden delivery / system references
# ---------------------------------------------------------------------------

_FORBIDDEN = (
    "M365",
    "m365",
    "Graph",
    "gmail",
    "Gmail",
    "smtp",
    "SMTP",
    "sendMail",
    "send_mail",
    "backend.delivery",
    "delivery_tracking",
    "anthropic",
    "Anthropic",
    "keyring",
    "keychain",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_in_css(css_src: str, needle: str) -> None:
    """design.css must not reference any external delivery or credential system."""
    assert needle not in css_src, (
        f"design.css must not reference {needle!r}"
    )


# ---------------------------------------------------------------------------
# 5. Print isolation
# ---------------------------------------------------------------------------

def test_print_media_query_present(css_src: str) -> None:
    """A @media print block must be present to protect A4/export layout."""
    assert "@media print" in css_src, (
        "design.css must include a @media print block to isolate "
        "A4/export output from the design-system shell styles"
    )
