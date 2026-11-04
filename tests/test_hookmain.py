from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from harness_core import hookmain

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "bin" / "harness-hook"


@pytest.fixture
def henv(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    (data / "sessions" / "ok").mkdir(parents=True)
    # a passing SessionStart for session "ok" (the end-to-end tests produce this through hookmain itself)
    (data / "sessions" / "ok" / "status.json").write_text(json.dumps({"ok": True, "reasons": [], "ts": time.time()}))
    return {"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_ROOT": str(ROOT), "HOME": str(tmp_path / "home"),
            "CLAUDE_PROJECT_DIR": str(ws), "LOOP_CONTRACT_ROOT": str(tmp_path / "lc")}


def bash(cmd, ws, sid="ok"):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": ws,
            "session_id": sid}


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


def test_ctx_loads_registry_and_session_state(henv, tmp_path):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "business").mkdir()
    (data / "business" / "connector_registry.json").write_text(json.dumps({"mcp__gmail__search": "read"}))
    (data / "business" / "protected_terms.json").write_text(json.dumps({"terms": ["Acme"]}))
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1.json").write_text(json.dumps({"active_task": "j1"}))
    (data / "sessions" / "s1" / "written.json").write_text(json.dumps(["/x/y.py"]))
    evt = {"session_id": "s1", "cwd": henv["CLAUDE_PROJECT_DIR"], "agent_type": "harness:web-researcher"}
    ctx = hookmain.build_ctx(evt, henv)
    assert ctx.connector_registry == {"mcp__gmail__search": "read"}
    assert ctx.protected_terms == ["Acme"]
    assert ctx.job == "j1"
    assert ctx.written_this_task == {"/x/y.py"}
    assert ctx.agent_type == "harness:web-researcher"
    assert str(ctx.workspace) == henv["CLAUDE_PROJECT_DIR"]
    assert ctx.log is not None and ctx.session_ok is False  # no SessionStart status for s1


def grant_env(henv):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "sessions").mkdir(exist_ok=True)
    (data / "sessions" / "s1.json").write_text(json.dumps({"active_task": "j1"}))
    run = data / "runs" / "j1"
    run.mkdir(parents=True)
    raw = json.dumps({"domains": ["a.example"], "active": True}).encode()
    (run / "egress_grant.json").write_bytes(raw)
    return data, run, raw


def test_grant_without_grant_event_is_absent(henv):
    grant_env(henv)  # no grant writer exists yet (P3): a grant file alone is never honoured
    assert hookmain.build_ctx({"session_id": "s1"}, henv).grant is None


def test_grant_honoured_only_with_matching_grant_event(henv):
    from harness_core import canon
    from harness_core.events import EventLog

    data, run, raw = grant_env(henv)
    log = EventLog(run, data / "heads" / "j1")
    log.append("GRANT", {}, file="egress_grant.json", file_sha=canon.sha256_bytes(raw))
    assert hookmain.build_ctx({"session_id": "s1"}, henv).grant == {"domains": ["a.example"], "active": True}
    (run / "egress_grant.json").write_bytes(raw.replace(b"a.example", b"evil.com"))
    assert hookmain.build_ctx({"session_id": "s1"}, henv).grant is None
    log.append("NOTE", {}, file="egress_grant.json", file_sha=canon.sha256_bytes((run / "egress_grant.json").read_bytes()))
    assert hookmain.build_ctx({"session_id": "s1"}, henv).grant is None  # recorded, but not by a GRANT event


@pytest.mark.parametrize("rel", ["sessions/s1.json", "runs/j1/egress_grant.json", "runs/j1/tier.json",
                                 "sessions/s1/status.json"])
def test_symlinked_state_file_fails_closed(henv, tmp_path, rel):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "runs" / "j1").mkdir(parents=True)
    if rel != "sessions/s1.json":
        (data / "sessions" / "s1.json").write_text(json.dumps({"active_task": "j1"}))
    (tmp_path / "planted.json").write_text(json.dumps({"active_task": "j1", "ok": True, "ts": time.time()}))
    (data / rel).symlink_to(tmp_path / "planted.json")
    evt = {**bash("ls", henv["CLAUDE_PROJECT_DIR"]), "session_id": "s1"}
    assert hookmain.main("PreToolUse", evt, henv)[0] == 2


def test_symlinked_written_record_falls_back_to_strict(henv, tmp_path):
    data, ws = Path(henv["CLAUDE_PLUGIN_DATA"]), Path(henv["CLAUDE_PROJECT_DIR"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (tmp_path / "w.json").write_text("[]")
    (data / "sessions" / "s1" / "written.json").symlink_to(tmp_path / "w.json")
    (ws / "old.py").write_text("print(1)\n")
    ctx = hookmain.build_ctx({"session_id": "s1"}, henv)
    assert os.path.realpath(ws / "old.py") in ctx.written_this_task


@pytest.mark.parametrize("drop", ["CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT"])
def test_missing_plugin_env_denies_everything(henv, drop):
    env = {k: v for k, v in henv.items() if k != drop}
    assert hookmain.main("PreToolUse", bash("ls", henv["CLAUDE_PROJECT_DIR"]), env)[0] == 2


def write_status(henv, sid="s1", **st):
    sdir = Path(henv["CLAUDE_PLUGIN_DATA"]) / "sessions" / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "status.json").write_text(json.dumps({"ok": True, "reasons": [], "ts": time.time(), **st}))


def test_workspace_is_project_dir_not_event_cwd(henv):
    ctx = hookmain.build_ctx({"cwd": "/", "session_id": "s1"}, henv)
    assert str(ctx.workspace) == henv["CLAUDE_PROJECT_DIR"]


def test_workspace_falls_back_to_session_start_cwd(henv, tmp_path):
    env = {k: v for k, v in henv.items() if k != "CLAUDE_PROJECT_DIR"}
    write_status(henv, cwd=str(tmp_path / "recorded"))
    assert str(hookmain.build_ctx({"cwd": "/", "session_id": "s1"}, env).workspace) == str(tmp_path / "recorded")
    with pytest.raises(ValueError):
        hookmain.build_ctx({"cwd": "/", "session_id": "s2"}, env)  # nothing recorded: never the event cwd
    assert str(hookmain.build_ctx({"cwd": "/w", "session_id": "s2"}, env, "SessionStart").workspace) == "/w"


def test_session_ok_needs_a_fresh_ok_status(henv):
    lc = {"LOOP_CONTRACT_ROOT": "/lc"}
    write_status(henv, "a")
    write_status(henv, "b", ok=False, reasons=["tamper"])
    write_status(henv, "c", ts=time.time() - hookmain.STATUS_MAX_AGE - 1)
    write_status(henv, "d", ts="yesterday")
    assert hookmain.build_ctx({"session_id": "a"}, {**henv, **lc}).session_ok is True
    for sid in "bcd":
        assert hookmain.build_ctx({"session_id": sid}, {**henv, **lc}).session_ok is False, sid
    assert "tamper" in hookmain.build_ctx({"session_id": "b"}, {**henv, **lc}).session_reason
    assert hookmain.build_ctx({}, {**henv, **lc}).session_ok is False
    # ${LOOP_CONTRACT_ROOT} unset: its protected pattern cannot match, so only reads pass
    ctx = hookmain.build_ctx({"session_id": "a"}, {k: v for k, v in henv.items() if k != "LOOP_CONTRACT_ROOT"})
    assert ctx.session_ok is False and "loop-contract" in ctx.session_reason


def test_loop_contract_root_from_config_log_first(henv, tmp_path):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "config_log.jsonl").write_text(json.dumps({"loop_contract_root": "/from/setup"}) + "\n{junk\n")
    assert str(hookmain.build_ctx({}, {**henv, "LOOP_CONTRACT_ROOT": "/from/env"}).loop_contract_root) == "/from/setup"
    (data / "config_log.jsonl").unlink()
    assert str(hookmain.build_ctx({}, {**henv, "LOOP_CONTRACT_ROOT": "/from/env"}).loop_contract_root) == "/from/env"


def test_post_tool_use_records_writes(henv):
    data, ws = Path(henv["CLAUDE_PLUGIN_DATA"]), Path(henv["CLAUDE_PROJECT_DIR"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text("[]")
    for tool, key, path in (("Write", "file_path", str(ws / "a.py")), ("Edit", "file_path", "b.py"),
                            ("NotebookEdit", "notebook_path", str(ws / "n.ipynb")), ("Read", "file_path", str(ws / "r"))):
        evt = {"session_id": "s1", "tool_name": tool, "tool_input": {key: path}, "cwd": "/"}
        assert hookmain.main("PostToolUse", evt, henv) == (0, None)
    rec = json.loads((data / "sessions" / "s1" / "written.json").read_text())
    assert rec == [os.path.realpath(ws / n) for n in ("a.py", "b.py", "n.ipynb")]


def test_post_tool_use_failure_drops_the_record(henv):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text('{"not": "a list"}')
    evt = {"session_id": "s1", "tool_name": "Write", "tool_input": {"file_path": "/x"}}
    assert hookmain.main("PostToolUse", evt, henv)[0] == 2
    assert not (data / "sessions" / "s1" / "written.json").exists()  # readers now use the strict fallback


def test_loops_job_files_count_as_self_written(henv):
    from harness_core import written

    ws, data = Path(henv["CLAUDE_PROJECT_DIR"]), Path(henv["CLAUDE_PLUGIN_DATA"])
    for d in ("work", "requests"):
        (ws / ".claude" / "loops" / "j1" / d).mkdir(parents=True)
        (ws / ".claude" / "loops" / "j1" / d / "x.py").write_text("")
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text("[]")
    got = written.self_written(data, "s1", ws)
    assert {os.path.realpath(ws / ".claude" / "loops" / "j1" / d / "x.py") for d in ("work", "requests")} <= got


def test_fallback_trusts_only_unchanged_tracked_files(henv):
    from harness_core import written

    ws, data = Path(henv["CLAUDE_PROJECT_DIR"]), Path(henv["CLAUDE_PLUGIN_DATA"])
    for n in ("tracked.py", "edited.py", "untracked.py"):
        (ws / n).write_text("")
    fake_git = lambda argv, **kw: types.SimpleNamespace(returncode=0, stdout=b"tracked.py\0edited.py\0")
    written.start(data, "s1", ws, run=fake_git)
    (data / "sessions" / "s1" / "written.json").unlink()  # record lost
    time.sleep(0.01)
    (ws / "edited.py").write_text("changed")
    os.utime(ws / "edited.py", (0, 0))  # an old mtime does not hide the change (ctime moved)
    got = written.self_written(data, "s1", ws)
    r = os.path.realpath
    assert r(ws / "tracked.py") not in got and {r(ws / "edited.py"), r(ws / "untracked.py")} <= got


def test_tracked_files_disables_fsmonitor(tmp_path):
    from harness_core import written

    seen = []
    written.tracked_files(tmp_path, run=lambda argv, **kw: seen.append(argv) or types.SimpleNamespace(returncode=1, stdout=b""))
    assert seen[0][0] == "/usr/bin/git" and "core.fsmonitor=false" in seen[0]


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
    assert rows[1]["input"]["tool_name"] == "Bash" and rows[1]["reason"]


def test_debug_log_redacts_inputs_and_transcript(henv, tmp_path):
    log = tmp_path / "debug.jsonl"
    evt = {**bash("echo s3cret-token", henv["CLAUDE_PROJECT_DIR"]), "transcript_path": "/Users/x/secret-transcript.jsonl",
           "tool_response": {"stdout": "s3cret-token"}}
    hookmain.main("PreToolUse", evt, dict(henv, HARNESS_DEBUG_LOG=str(log)))
    text = log.read_text()
    assert "s3cret" not in text and "secret-transcript" not in text
    assert json.loads(text)["input"]["tool_input"]["command"].startswith("<redacted")


def test_debug_log_failure_never_changes_the_decision(henv, tmp_path):
    env = dict(henv, HARNESS_DEBUG_LOG=str(tmp_path / "no" / "such" / "dir" / "x.jsonl"))
    assert hookmain.main("PreToolUse", bash("git push", henv["CLAUDE_PROJECT_DIR"]), env) == (2, None)
    assert hookmain.main("PreToolUse", bash("ls", henv["CLAUDE_PROJECT_DIR"]), env) == (0, None)


def test_debug_log_failure_is_reported(henv, tmp_path, capsys):
    env = dict(henv, HARNESS_DEBUG_LOG=str(tmp_path / "no" / "such" / "dir" / "x.jsonl"))
    hookmain.main("PreToolUse", bash("ls", henv["CLAUDE_PROJECT_DIR"]), env)
    assert "debug log" in capsys.readouterr().err


def test_watchdog_docstring_matches_values():
    assert "minus 5 s" in hookmain.__doc__ and "minus 1 s" in hookmain.__doc__
    assert hookmain.watchdog("gate") == 1795 and hookmain.watchdog("fetch") == 55


def test_post_tool_use_records_bash_writes(henv):
    # automated commit review: a script written through Bash must count as self-written too
    data, ws = Path(henv["CLAUDE_PLUGIN_DATA"]), Path(henv["CLAUDE_PROJECT_DIR"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text("[]")
    evt = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": f"printf 'x' > {ws / 'run.sh'}"}}
    assert hookmain.main("PostToolUse", evt, henv) == (0, None)
    rec = json.loads((data / "sessions" / "s1" / "written.json").read_text())
    assert os.path.realpath(ws / "run.sh") in rec


def test_post_tool_use_unresolvable_bash_write_drops_the_record(henv):
    data = Path(henv["CLAUDE_PLUGIN_DATA"])
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text("[]")
    evt = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": 'printf x > "$(mktemp)"'}}
    hookmain.main("PostToolUse", evt, henv)
    assert not (data / "sessions" / "s1" / "written.json").exists()  # strict fallback from now on


def test_post_tool_use_hook_matches_bash(env):
    cfg = json.loads((env["root"] / "hooks" / "hooks.json").read_text())
    import re as _re
    assert any(_re.fullmatch(g.get("matcher", ".*"), "Bash") for g in cfg["hooks"]["PostToolUse"])


def test_ctx_cwd_is_the_event_cwd_but_workspace_stays_project_dir(henv, tmp_path):
    sub = Path(henv["CLAUDE_PROJECT_DIR"]) / "sub"
    sub.mkdir()
    ctx = hookmain.build_ctx({"session_id": "a", "cwd": str(sub)}, henv)
    assert str(ctx.cwd) == str(sub) and str(ctx.workspace) == henv["CLAUDE_PROJECT_DIR"]
