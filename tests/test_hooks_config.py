"""OQ-4 spike S1/S2: a crashed or timed-out hook is treated as allow unless onFailure=block."""
from __future__ import annotations

import json
import re


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


def test_hooks_run_isolated_absolute_python(env):
    # a planted python3 earlier on PATH must not replace the interpreter of every hook
    for event, h in _entries(env["root"]):
        assert h["command"].startswith("/usr/bin/python3 -I "), event


def test_post_tool_use_records_every_file_write(env):
    cfg = json.loads((env["root"] / "hooks" / "hooks.json").read_text())
    groups = cfg["hooks"]["PostToolUse"]
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        hits = [g for g in groups if re.fullmatch(g.get("matcher", ".*"), tool)]
        assert any(h["command"].endswith('harness-hook" PostToolUse') for g in hits for h in g["hooks"]), tool
