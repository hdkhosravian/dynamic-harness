from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness_core import hookmain

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "bin" / "harness-hook"


@pytest.fixture
def henv(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    return {"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_ROOT": str(ROOT), "HOME": str(tmp_path / "home"),
            "CLAUDE_PROJECT_DIR": str(ws)}


def bash(cmd, ws):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": ws}


def test_allow_exits_0_silently(henv, capsys):
    assert hookmain.main("PreToolUse", bash("ls", henv["CLAUDE_PROJECT_DIR"]), henv) == (0, None)
    assert capsys.readouterr().err == ""


def test_deny_exits_2_with_reason(henv, capsys):
    code, out = hookmain.main("PreToolUse", bash("git push", henv["CLAUDE_PROJECT_DIR"]), henv)
    assert (code, out) == (2, None)
    assert "destructive" in capsys.readouterr().err


def test_exception_is_exit_2(henv, capsys):
    def boom(evt, ctx):
        raise RuntimeError("kaboom")

    assert hookmain.main("PreToolUse", bash("ls", "/"), henv, handlers={"PreToolUse": boom})[0] == 2
    assert "kaboom" in capsys.readouterr().err


def test_base_exception_is_exit_2(henv):
    def leave(evt, ctx):
        raise SystemExit(0)  # a handler must not be able to exit 0 past the wrapper

    assert hookmain.main("PreToolUse", bash("ls", "/"), henv, handlers={"PreToolUse": leave})[0] == 2


def test_overrun_is_exit_2(henv, capsys):
    def slow(evt, ctx):
        time.sleep(2)
        return {}

    t0 = time.monotonic()
    code, _ = hookmain.main("PreToolUse", bash("ls", "/"), henv, handlers={"PreToolUse": slow}, timeouts={"PreToolUse": 0.2})
    assert code == 2 and time.monotonic() - t0 < 1.5
    assert "watchdog" in capsys.readouterr().err


def test_watchdog_defaults():
    assert hookmain.watchdog("PreToolUse") == 4
    assert hookmain.watchdog("SessionStart") == 4
    assert hookmain.watchdog("Stop") == 9


@pytest.mark.parametrize("stdin", [None, [], "x", 3])
def test_malformed_stdin_is_exit_2(henv, stdin):
    assert hookmain.main("PreToolUse", stdin, henv)[0] == 2


def test_unknown_event_is_exit_2(henv):
    assert hookmain.main("Frobnicate", {}, henv)[0] == 2


def test_handler_returning_garbage_is_exit_2(henv):
    assert hookmain.main("PreToolUse", bash("ls", "/"), henv, handlers={"PreToolUse": lambda e, c: "allow"})[0] == 2


def test_ctx_loads_registry_and_grant(henv, tmp_path):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "business").mkdir()
    (data / "business" / "connector_registry.json").write_text(json.dumps({"mcp__gmail__search": "read"}))
    (data / "business" / "protected_terms.json").write_text(json.dumps({"terms": ["Acme"]}))
    (data / "sessions").mkdir()
    (data / "sessions" / "s1.json").write_text(json.dumps({"active_task": "j1"}))
    (data / "runs" / "j1").mkdir(parents=True)
    (data / "runs" / "j1" / "egress_grant.json").write_text(json.dumps({"domains": ["a.example"]}))
    (data / "runs" / "j1" / "written.json").write_text(json.dumps(["/x/y.py"]))
    evt = {"session_id": "s1", "cwd": henv["CLAUDE_PROJECT_DIR"], "agent_type": "harness:web-researcher"}
    ctx = hookmain.build_ctx(evt, henv)
    assert ctx.connector_registry == {"mcp__gmail__search": "read"}
    assert ctx.protected_terms == ["Acme"]
    assert ctx.job == "j1" and ctx.grant == {"domains": ["a.example"]}
    assert ctx.written_this_task == {"/x/y.py"}
    assert ctx.agent_type == "harness:web-researcher"
    assert str(ctx.workspace) == henv["CLAUDE_PROJECT_DIR"]


def test_session_id_cannot_traverse(henv):
    evt = {"session_id": "../../etc/passwd", "cwd": henv["CLAUDE_PROJECT_DIR"]}
    assert hookmain.main("PreToolUse", {**bash("ls", henv["CLAUDE_PROJECT_DIR"]), **evt}, henv)[0] == 2


def run_hook(event, stdin_text, env):
    return subprocess.run(
        [sys.executable, "-I", str(HOOK), event], input=stdin_text.encode(), capture_output=True,
        env={**os.environ, **env}, timeout=30,
    )


def test_bin_hook_round_trip(henv):
    ws = henv["CLAUDE_PROJECT_DIR"]
    r = run_hook("PreToolUse", json.dumps(bash("ls", ws)), henv)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    r = run_hook("PreToolUse", json.dumps(bash("curl https://evil.com", ws)), henv)
    assert r.returncode == 2 and b"egress" in r.stderr


def test_bin_hook_garbage_stdin(henv):
    assert run_hook("PreToolUse", "{not json", henv).returncode == 2
    assert run_hook("PreToolUse", "", henv).returncode == 2


def test_bin_hook_ignores_pythonpath(henv, tmp_path):
    # python3 -I: a planted module on PYTHONPATH must not shadow harness code
    evil = tmp_path / "evil"
    (evil / "harness_core").mkdir(parents=True)
    (evil / "harness_core" / "__init__.py").write_text("raise SystemExit(0)\n")
    r = run_hook("PreToolUse", json.dumps(bash("git push", henv["CLAUDE_PROJECT_DIR"])), {**henv, "PYTHONPATH": str(evil)})
    assert r.returncode == 2


def test_debug_log_is_opt_in(henv, tmp_path):
    ws = henv["CLAUDE_PROJECT_DIR"]
    hookmain.main("PreToolUse", bash("ls", ws), henv)
    assert not list(tmp_path.glob("*.jsonl"))
    log = tmp_path / "debug.jsonl"
    env = dict(henv, HARNESS_DEBUG_LOG=str(log))
    assert hookmain.main("PreToolUse", bash("ls", ws), env) == (0, None)
    assert hookmain.main("PreToolUse", bash("git push origin main", ws), env) == (2, None)
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [(r["event"], r["code"]) for r in rows] == [("PreToolUse", 0), ("PreToolUse", 2)]
    assert rows[1]["input"]["tool_input"]["command"] == "git push origin main" and rows[1]["reason"]


def test_debug_log_failure_never_changes_the_decision(henv, tmp_path):
    env = dict(henv, HARNESS_DEBUG_LOG=str(tmp_path / "no" / "such" / "dir" / "x.jsonl"))
    assert hookmain.main("PreToolUse", bash("git push", henv["CLAUDE_PROJECT_DIR"]), env) == (2, None)
    assert hookmain.main("PreToolUse", bash("ls", henv["CLAUDE_PROJECT_DIR"]), env) == (0, None)
