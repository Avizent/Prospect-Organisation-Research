"""Static fence — the frontend must not reference forbidden modules,
APIs, or storage primitives.

Step 12 is an inspector UI for the fake-safe workflow. It must not
mention any of the production-only integrations (Anthropic, Cloud
client wrapper, macOS Keychain, Microsoft Graph email, document
assembly, orchestrator). It also must not use browser persistence
APIs for any data — sessions are server-side via an HttpOnly
cookie, and CLAUDE.md hard rule #6 forbids localStorage /
sessionStorage for sensitive material.

The scan walks every file under ``frontend/`` (HTML, JS, CSS,
JSON, SVG …). Substring matches are deliberately blunt — if a
future caller imports a forbidden module via an alias or
re-export, the alias is still likely to contain one of the
forbidden roots.
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _REPO_ROOT / "frontend"


# Files we don't scan (binary assets, image icons).
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
                  ".pdf", ".woff", ".woff2", ".ttf", ".eot"}
# Substrings of paths we never recurse into.
_SKIP_PATH_PARTS = {"icons"}


def _iter_frontend_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for path in _FRONTEND_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if any(part in _SKIP_PATH_PARTS for part in path.parts):
            continue
        out.append(path)
    return out


_FORBIDDEN_SUBSTRINGS = (
    # Production integrations the inspector must not touch.
    "anthropic",
    "Anthropic",
    "CloudClient",
    "cloud_client",
    "keyring",
    "keychain",
    "Keychain",
    "M365",
    "m365",
    "Microsoft Graph",
    "microsoft graph",
    "sk-ant",
    # Backend modules the inspector must not link to (the API is the
    # only legitimate boundary).
    "backend.delivery",
    "backend.assembly",
    "backend.orchestrator",
    "backend.credentials",
    # Credential admin UI is deliberately out of scope for step 12.
    "/admin/credentials",
    # Browser persistence APIs — see CLAUDE.md hard rule #6.
    "localStorage",
    "sessionStorage",
    "document.cookie",
    # Script-execution sinks.
    "eval(",
    "new Function(",
    "document.write(",
    "innerHTML",
)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_substring(forbidden: str) -> None:
    """No frontend file contains ``forbidden`` anywhere — code or comment."""
    leaks: list[tuple[str, int]] = []
    for path in _iter_frontend_files():
        text = path.read_text(encoding="utf-8")
        if forbidden in text:
            for idx, line in enumerate(text.splitlines(), start=1):
                if forbidden in line:
                    leaks.append((str(path.relative_to(_REPO_ROOT)), idx))
    assert not leaks, (
        f"frontend must not reference {forbidden!r}; "
        f"found in: {leaks}"
    )


_FETCH_CALL_RE = re.compile(r"""fetch\(\s*([`'"])([^`'"]*)([`'"])""")


def test_fetch_calls_are_same_origin_relative() -> None:
    """Every ``fetch(...)`` first-argument literal starts with ``/``.

    The inspector talks only to its own backend; a cross-origin
    fetch would be a contract change. Template literals are also
    matched — they're allowed only when the literal portion starts
    with ``/`` (the API client uses ``\\`/api/jobs/${id}/...\\``
    style).
    """
    bad: list[tuple[str, int, str]] = []
    for path in _iter_frontend_files():
        if path.suffix != ".js":
            continue
        text = path.read_text(encoding="utf-8")
        for idx, line in enumerate(text.splitlines(), start=1):
            for match in _FETCH_CALL_RE.finditer(line):
                literal = match.group(2)
                if not literal.startswith("/"):
                    bad.append(
                        (str(path.relative_to(_REPO_ROOT)), idx, literal)
                    )
    assert not bad, (
        f"fetch() calls must use same-origin paths starting with '/': {bad}"
    )


def test_no_inline_scripts() -> None:
    """``<script>`` tags in index.html must use a ``src`` attribute.

    Inline scripts would defeat the "no eval"-class checks above
    and would be the obvious place to attempt to smuggle a token
    into the JS heap from a server template.
    """
    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    script_tags = re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE)
    for tag in script_tags:
        assert " src=" in tag, f"inline script forbidden: {tag!r}"


def test_no_third_party_cdn_links() -> None:
    """The HTML shell must not load any cross-origin asset.

    All stylesheets and scripts come from our own static mount.
    """
    html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    # Any href/src/url with a scheme is a third-party reference.
    schemes = re.findall(
        r"""(?:href|src)\s*=\s*["']([a-zA-Z]+:[^"']+)["']""", html
    )
    assert not schemes, (
        f"index.html must not load any third-party asset: {schemes}"
    )


def test_no_storage_api_anywhere() -> None:
    """Belt-and-braces: even alternate spellings of storage APIs.

    The parametrized substring test catches ``localStorage`` and
    ``sessionStorage`` directly; this check catches
    ``window.localStorage`` style references that might one day
    appear with whitespace variations.
    """
    pattern = re.compile(
        r"(window|globalThis|self)\s*\.\s*(local|session)Storage"
    )
    bad: list[tuple[str, int, str]] = []
    for path in _iter_frontend_files():
        text = path.read_text(encoding="utf-8")
        for idx, line in enumerate(text.splitlines(), start=1):
            m = pattern.search(line)
            if m:
                bad.append(
                    (str(path.relative_to(_REPO_ROOT)), idx, m.group(0))
                )
    assert not bad, f"browser storage API forbidden: {bad}"
