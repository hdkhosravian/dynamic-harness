"""OQ-4 spike S1/S2: a crashed or timed-out hook is treated as allow unless onFailure=block."""
from __future__ import annotations

import json


def _entries(root):
    cfg = json.loads((root / "hooks" / "hooks.json").read_text())
    for event, groups in cfg["hooks"].items():
        for g in groups:
            for h in g["hooks"]:
                yield event, h


def test_every_hook_fails_closed(env):
    for event, h in _entries(env["root"]):
        assert h.get("onFailure") == "block", f"{event} hook lacks onFailure=block"


def test_every_hook_has_explicit_timeout(env):
    for event, h in _entries(env["root"]):
        assert isinstance(h.get("timeout"), int) and h["timeout"] > 0, event


def test_hooks_run_isolated_python(env):
    for event, h in _entries(env["root"]):
        assert "python3 -I" in h["command"], event
