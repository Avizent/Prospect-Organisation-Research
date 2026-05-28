"""Shared fixtures for ``tests/exporters/``.

The export layer is a pure renderer of canonical files on disk, so
the only cross-cutting concern is the jobs root: tests that exercise
the storage helpers (``test_export_isolation.py``) need an isolated
``ANS_JOBS_ROOT`` to avoid writing under the user's real
``~/.ans-tool/jobs/``. The pure-renderer tests (``test_pdf_renderer``,
``test_markdown_to_html``, ``test_determinism_helpers``) do not
touch disk, but autouse here is cheap and protects against drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory.

    Mirrors the fixture in ``tests/jobs/conftest.py`` so the export
    storage helpers (``write_export``, ``upsert_export_entry``) land
    under a controlled tmp path during isolated tests.
    """
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root
