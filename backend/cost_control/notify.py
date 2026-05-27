"""macOS desktop notifications, best-effort and never fatal.

A trap firing should surface immediately to the user. On macOS the
cheapest path is ``osascript -e 'display notification ...'``; it
requires no entitlements and works without an installed app bundle.

This module is intentionally narrow:
  * Exactly one public function: :func:`notify`.
  * Never raises. Failure is logged to stderr (one short line) and
    swallowed; the trap path must still complete.
  * Never receives secret material — the trap-firing helper only
    forwards short, sanitised reason strings.

Disable in tests/CI by setting ``ANS_DISABLE_NOTIFICATIONS=1`` in the
environment, or by monkey-patching :func:`notify` directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


# Hard caps so we never feed osascript an arbitrarily large string,
# even by accident. AppleScript truncates long notifications anyway.
_MAX_TITLE = 80
_MAX_BODY = 140


def _escape_for_applescript(value: str) -> str:
    """Escape a Python string for safe embedding in an AppleScript literal.

    The only metacharacters we need to neutralise inside a double-quoted
    AppleScript string are the backslash and the double-quote. We do
    *not* perform full shell quoting because we invoke osascript with
    argv, not via a shell.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, body: str) -> bool:
    """Fire a macOS notification. Returns True on success, False otherwise.

    Safe to call from any thread. Never raises.

    Both ``title`` and ``body`` are truncated to short hard caps. The
    caller is responsible for ensuring the strings contain no secret
    material; this function does not inspect them.
    """
    if os.environ.get("ANS_DISABLE_NOTIFICATIONS") == "1":
        return False

    osascript = shutil.which("osascript")
    if osascript is None:
        # Not on macOS, or osascript not on PATH. Silently noop.
        return False

    safe_title = _escape_for_applescript(str(title)[:_MAX_TITLE])
    safe_body = _escape_for_applescript(str(body)[:_MAX_BODY])
    script = f'display notification "{safe_body}" with title "{safe_title}"'

    try:
        result = subprocess.run(
            [osascript, "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            # AppleScript errors are short; pass through without echoing
            # body/title in case the user redirected stderr to a file.
            print(
                "warning: osascript notification failed "
                f"(rc={result.returncode})",
                file=sys.stderr,
            )
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"warning: notify() suppressed exception: {exc!r}", file=sys.stderr)
        return False
