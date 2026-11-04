from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


@pytest.fixture
def env(tmp_path):
    """Isolated plugin-data root + workspace for one test."""
    data = tmp_path / "plugin_data"
    ws = tmp_path / "workspace"
    data.mkdir()
    ws.mkdir()
    return {"data": data, "ws": ws, "root": ROOT}


@pytest.fixture(autouse=True)
def _cc_version(monkeypatch):
    """Hermetic SessionStart version check: tests never shell out to `claude --version`."""
    monkeypatch.setenv("CLAUDE_CODE_VERSION", "2.1.296")
