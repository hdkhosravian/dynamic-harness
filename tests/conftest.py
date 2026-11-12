from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def pytest_collection_modifyitems(config, items):
    """HARNESS_SHARD=k/n keeps the tests whose node id falls in shard k of n (tools/suite.py sets it)."""
    shard = os.environ.get("HARNESS_SHARD")
    if not shard:
        return
    k, n = (int(x) for x in shard.split("/"))
    keep, drop = [], []
    for item in items:
        (keep if zlib.crc32(item.nodeid.encode("utf-8", "backslashreplace")) % n == k else drop).append(item)
    config.hook.pytest_deselected(items=drop)
    items[:] = keep


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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """No test may read or write the real ~/.claude (setup writes settings there when ctx.home is unset)."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
