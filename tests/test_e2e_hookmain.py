"""End to end through ``hookmain.main`` with a real data dir.

No hook-state file is written by hand: status.json, written.json, tracked.json,
settings_baseline.json and the session event log all come from the hooks. The
only setup artefact is ``config_log.jsonl``, which /dynamic-harness:setup writes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core import setup, hookmain, sandbox_exec, session

ROOT = Path(__file__).resolve().parents[1]
SID = "e2e-1"


@pytest.fixture
def h(tmp_path):
    ws, data, home, plugin, lc = (tmp_path / n for n in ("ws", "data", "home", "plugin", "lc"))
    for d in (ws, data, home, plugin / "bin", lc / ".claude-plugin"):
        d.mkdir(parents=True)
    (plugin / "bin" / "harness-hook").write_text("#!/usr/bin/python3 -I\n")
    (lc / ".claude-plugin" / "plugin.json").write_text('{"name": "loop-contract", "version": "1.1.0"}')
    (ws / "old.py").write_text("print('pre-existing')\n")
    env = {"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_ROOT": str(plugin), "HOME": str(home),
           "CLAUDE_PROJECT_DIR": str(ws)}
    return {"ws": ws, "data": data, "home": home, "lc": lc, "env": env}


def _setup(h):
    """What /dynamic-harness:setup leaves behind: the release hashes and the loop-contract root."""
    ctx = hookmain.build_ctx({}, {**h["env"], "LOOP_CONTRACT_ROOT": str(h["lc"])})
    setup._apply(ctx, setup.plan(ctx))


CARD = {"task_type": "data_question", "deliverable": {"kind": "answer", "description": "orders this week"},
        "oracle": {"type": "finance.reconcile", "executable": True, "criteria": ["ties out"]},
        "reliance": "operations", "decision_class": "none",
        "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}},
        "risk": {"level": "medium"}, "units": 1, "unit_definition": "the answer", "domain": "sales",
        "provenance": {"risk": "user", "reliance": "user"}}


def _in_task(h):
    """A compiled T1 task, through the real prompt hook and compile writer (the T0 tripwire is then off)."""
    from harness_core import compile as compile_
    hookmain.main("UserPromptSubmit", {"session_id": SID, "prompt_id": "p1", "prompt": "how many orders this week?",
                                       "cwd": str(h["ws"])}, h["env"])
    ctx = hookmain.build_ctx({"session_id": SID}, h["env"])
    return compile_.apply({"tool": "compile", "card": CARD}, ctx, {"session_id": SID})["job_id"]


def start(h, env=None):
    return hookmain.main("SessionStart", {"session_id": SID, "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, env or h["env"])


def pre(h, tool, env=None, cwd=None, **inp):
    evt = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp,
           "cwd": cwd or str(h["ws"])}
    return hookmain.main("PreToolUse", evt, env or h["env"])[0]


def post(h, tool, **inp):
    evt = {"session_id": SID, "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": inp,
           "tool_response": {}, "cwd": str(h["ws"])}
    return hookmain.main("PostToolUse", evt, h["env"])[0]


def events(h):
    f = h["data"] / "sessions" / SID / "events.jsonl"
    return [json.loads(x)["type"] for x in f.read_text().splitlines()] if f.exists() else []


def test_running_a_file_written_this_session_is_denied(h, capsys):
    _setup(h)
    assert start(h)[0] == 0
    _in_task(h)
    assert pre(h, "Bash", command="python3 old.py") == 0  # pre-existing file
    (h["ws"] / "x.py").write_text("print(1)\n")
    assert post(h, "Write", file_path=str(h["ws"] / "x.py"), content="print(1)\n") == 0
    capsys.readouterr()
    assert pre(h, "Bash", command="python3 x.py") == 2
    assert "written this task" in capsys.readouterr().err
    assert pre(h, "Bash", command=f"python3 {h['ws'] / 'x.py'}") == 2
    assert pre(h, "Bash", command="python3 old.py") == 0


def test_lost_record_falls_back_to_strict(h):
    _setup(h)
    start(h)
    (h["data"] / "sessions" / SID / "written.json").unlink()
    assert pre(h, "Bash", command="python3 old.py") == 2  # untracked, so unknown => treated as self-written


def test_job_work_files_are_self_written(h):
    _setup(h)
    start(h)
    work = h["ws"] / ".claude" / "loops" / "j1" / "work"
    work.mkdir(parents=True)
    (work / "run.py").write_text("print(1)\n")
    assert pre(h, "Bash", command="python3 .claude/loops/j1/work/run.py") == 2


def test_failed_session_start_allows_only_reads(h):
    # no setup: no config_log baseline, no loop-contract root => refused
    assert start(h)[0] == 2
    st = json.loads((h["data"] / "sessions" / SID / "status.json").read_text())
    assert st["ok"] is False and st["reasons"]
    assert pre(h, "Bash", command="touch a") == 2
    assert pre(h, "Bash", command="ls") == 0
    assert pre(h, "Read", file_path=str(h["ws"] / "old.py")) == 0
    assert events(h) == ["REFUSED", "SAFETY_DENY"]


def test_no_session_start_at_all_denies_non_reads(h):
    _setup(h)
    assert pre(h, "Bash", command="touch a") == 2
    assert pre(h, "Bash", command="ls") == 0


def test_passing_session_allows_reversible_work(h):
    _setup(h)
    assert start(h)[0] == 0
    assert pre(h, "Bash", command="touch a") == 0
    assert events(h) == []


@pytest.mark.parametrize("drop", ["CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT"])
def test_missing_plugin_env_denies(h, drop):
    _setup(h)
    start(h)
    env = {k: v for k, v in h["env"].items() if k != drop}
    assert pre(h, "Bash", command="ls", env=env) == 2


def test_config_change_removing_a_deny_rule_is_blocked(h):
    f = h["ws"] / ".claude" / "settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"permissions": {"deny": ["Read(./secrets/**)"]}}))
    _setup(h)
    start(h)

    def change(settings):
        f.write_text(json.dumps(settings))
        return hookmain.main("ConfigChange", {"session_id": SID, "source": "project_settings", "file_path": str(f)},
                             h["env"])[0]

    assert change({"permissions": {"deny": ["Read(./secrets/**)", "Read(./keys/**)"]}}) == 0  # tightening
    assert change({"permissions": {"deny": []}}) == 2


def test_cd_does_not_move_the_workspace(h):
    _setup(h)
    start(h)
    ws = h["ws"]
    assert pre(h, "Write", cwd="/", file_path=str(ws / "a.txt"), content="x") == 0
    assert pre(h, "Write", cwd="/", file_path=str(ws / ".claude" / "settings.json"), content="{}") == 2
    assert pre(h, "Write", cwd="/", file_path="/a.txt", content="x") == 2
    # without CLAUDE_PROJECT_DIR the boundary is the cwd SessionStart recorded, not the event's cwd
    env = {k: v for k, v in h["env"].items() if k != "CLAUDE_PROJECT_DIR"}
    assert pre(h, "Write", env=env, cwd="/", file_path="/a.txt", content="x") == 2
    assert pre(h, "Write", env=env, cwd="/", file_path=str(ws / "a.txt"), content="x") == 0


# ---- bin/harness-sandbox-exec: fixed roots, protected cwd, symlinked job dirs (finding 9, A) ----

sbx = pytest.mark.skipif(sandbox_exec.probe() is not None, reason="no working OS sandbox")


def launch(h, cwd, code):
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_DATA": str(h["data"]), "CLAUDE_PROJECT_DIR": str(h["ws"]),
           "HOME": str(h["home"])}
    return subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-sandbox-exec"), "--cwd", str(cwd), "--",
                           "/usr/bin/python3", "-I", "-c", code], capture_output=True, env=env, timeout=60)


@sbx
@pytest.mark.parametrize("rel", [".git", ".git/hooks", ".claude", ".claude/agents"])
def test_launcher_refuses_cwd_in_protected_workspace_path(h, rel):
    (h["ws"] / rel).mkdir(parents=True)
    r = launch(h, h["ws"] / rel, "print(1)")
    assert r.returncode == 2 and b"protected" in r.stderr


@sbx
def test_launcher_allows_cwd_in_job_work(h):
    work = h["ws"] / ".claude" / "loops" / "j1" / "work"
    work.mkdir(parents=True)
    r = launch(h, work, "open('out.txt','w').write('ok'); print(1)")
    assert r.returncode == 0, r.stderr
    assert (work / "out.txt").read_text() == "ok"


@sbx
@pytest.mark.parametrize("which", ["work", "requests", "loops"])
def test_launcher_rejects_symlinked_job_dirs(h, tmp_path, which):
    (tmp_path / "elsewhere").mkdir()
    job = h["ws"] / ".claude" / "loops" / "j1"
    if which == "loops":
        (h["ws"] / ".claude").mkdir()
        (h["ws"] / ".claude" / "loops").symlink_to(tmp_path / "elsewhere")
    else:
        job.mkdir(parents=True)
        (job / which).symlink_to(tmp_path / "elsewhere")
    r = launch(h, h["ws"], "print(1)")
    assert r.returncode == 2 and b"symlink" in r.stderr


@sbx
def test_launcher_denies_protected_paths_from_a_subdir(h):
    sub = h["ws"] / "src"
    sub.mkdir()
    for d in (".git", ".claude"):
        (h["ws"] / d).mkdir()
    (sub / "CLAUDE.md").write_text("nested\n")
    code = ("import sys\n"
            "for p in sys.argv[1:]:\n"
            "    try:\n        open(p, 'a').write('x'); print('WROTE', p)\n"
            "    except OSError: print('denied', p)\n")
    targets = [sub / "CLAUDE.md", sub / "CLAUDE.local.md", sub / "ok.txt"]
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_DATA": str(h["data"]), "CLAUDE_PROJECT_DIR": str(h["ws"]),
           "HOME": str(h["home"])}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-sandbox-exec"), "--cwd", str(sub), "--",
                        "/usr/bin/python3", "-I", "-c", code, *map(str, targets)], capture_output=True, env=env, timeout=60)
    if sandbox_exec.backend() == "bwrap":
        # bwrap cannot refuse creating a new CLAUDE*.md: the launcher removes it and fails the call
        assert r.returncode == sandbox_exec.INSTRUCTION_CODE and b"CLAUDE.local.md" in r.stderr, r.stderr
        assert not (sub / "CLAUDE.local.md").exists() and (sub / "CLAUDE.md").read_text() == "nested\n"
        assert b"denied " + str(sub / "CLAUDE.md").encode() in r.stdout and (sub / "ok.txt").exists()
        return
    assert r.returncode == 0, r.stderr
    assert b"WROTE" in r.stdout and r.stdout.count(b"WROTE") == 1 and b"ok.txt" in r.stdout.split(b"WROTE")[1]


@sbx
def test_launcher_denies_writes_through_a_symlinked_claude_md(h):
    (h["ws"] / "AGENTS.md").write_text("rules\n")
    (h["ws"] / "CLAUDE.md").symlink_to(h["ws"] / "AGENTS.md")
    r = launch(h, h["ws"], "open('AGENTS.md', 'a').write('evil')")
    assert r.returncode != 0
    assert (h["ws"] / "AGENTS.md").read_text() == "rules\n"


def test_bash_write_is_recorded_before_the_command_runs(h):
    # PostToolUse may never fire (the command fails, is interrupted, or the hook is skipped): PreToolUse records it
    _setup(h)
    assert start(h)[0] == 0
    sub = h["ws"] / "sub"
    sub.mkdir()
    assert pre(h, "Bash", cwd=str(sub), command="printf 'print(1)' > y.py") == 0
    (sub / "y.py").write_text("print(1)\n")  # the command ran; no PostToolUse follows
    assert pre(h, "Bash", command="python3 sub/y.py") == 2


def test_compact_keeps_the_written_record(h):
    _setup(h)
    start(h)
    _in_task(h)
    (h["ws"] / "x.py").write_text("print(1)\n")
    assert post(h, "Write", file_path=str(h["ws"] / "x.py")) == 0
    evt = {"session_id": SID, "cwd": str(h["ws"]), "source": "compact", "permission_mode": "default"}
    assert hookmain.main("SessionStart", evt, h["env"])[0] == 0
    assert pre(h, "Bash", command="python3 x.py") == 2


def test_files_extracted_into_a_recorded_directory_count_as_self_written(tmp_path):
    # pathtrack marks copy/extract destinations as directories; the written set must cover their files
    from harness_core import pathtrack, written
    from harness_core.safety import Ctx

    ws, data = tmp_path / "ws", tmp_path / "data"
    (ws / "out").mkdir(parents=True)
    data.mkdir()
    ctx = Ctx(workspace=ws, data_root=data, plugin_root=tmp_path / "root", home=tmp_path, job="j1")
    cmd = f"tar -xf a.tar -C {ws / 'out'}"
    assert pathtrack.bash_write_targets(cmd, ctx)  # a directory marker, not "nothing"
    sdir = written.session_dir(data, "s1")
    sdir.mkdir(parents=True)
    (sdir / "written.json").write_text("[]")
    written.record_bash({"session_id": "s1", "tool_input": {"command": cmd}}, ctx)  # the real recording path
    (ws / "out" / "conftest.py").write_text("x = 1\n")  # what the archive contained
    assert os.path.realpath(ws / "out" / "conftest.py") in written.self_written(data, "s1", ws)
