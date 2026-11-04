"""Gate hook: PostToolUse on a lone `harness-gate request` (spec §Gate adapter; build phase P4)."""
from __future__ import annotations

import json
import os
import stat

from harness_core import hookmain, sandbox_exec
from harness_core.events import EventLog
from harness_core.paths import job_paths
from test_gate import FakeRunner
from test_p3_hooks import ROOT, SID, compiled, h  # noqa: F401  (fixture h)

GATE_OUT = {"stdout": json.dumps({"tool": "gate", "action": "request"})}


def gate_post(h, command="harness-gate request", response=GATE_OUT):
    evt = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": command}, "tool_response": response,
           "cwd": str(h["ws"]), "tool_use_id": "tu-gate"}
    return hookmain.main("gate", evt, h["env"])


def test_other_bash_commands_return_at_once(h):
    assert gate_post(h, "ls -la", {"stdout": ""}) == (0, None)


def test_the_gate_writes_a_receipt_and_reports_the_state(h, monkeypatch):
    job = compiled(h)
    runner = FakeRunner()
    monkeypatch.setattr(sandbox_exec, "run", runner)
    code, out = gate_post(h)
    assert code == 0, out
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "known mode" in msg and "Partly done: 0 of 1" in msg
    p = job_paths(h["data"], h["ws"], job)
    assert (p.run / "receipt.json").is_file()
    assert runner.calls[0][2].endswith(os.path.join("skills", "loop-contract", "scripts", "fold_ledger.py"))
    key = h["data"] / "keys" / "hmac.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and len(key.read_bytes()) == 32


def test_the_key_is_reused(h, monkeypatch):
    compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    gate_post(h)
    k1 = (h["data"] / "keys" / "hmac.key").read_bytes()
    gate_post(h)
    assert (h["data"] / "keys" / "hmac.key").read_bytes() == k1


def test_no_open_task_is_refused(h):
    assert gate_post(h)[0] == 2


def test_a_stopped_task_is_refused(h, monkeypatch):
    job = compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    p = job_paths(h["data"], h["ws"], job)
    EventLog(p.run, p.heads).append("STOPPED", {"state": "STOPPED_STALL", "reason": "x"})
    assert gate_post(h)[0] == 2 and not (p.run / "receipt.json").exists()


def test_a_failed_gate_child_blocks(h, monkeypatch):
    compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner(code=1))
    assert gate_post(h)[0] == 2


def test_the_request_must_be_the_cli_output(h, monkeypatch):
    compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    assert gate_post(h, response={"stdout": json.dumps({"tool": "gate", "action": "other"})})[0] == 2
    assert gate_post(h, "harness-gate request; true")[0] == 0  # not a lone call: not the gate


def test_hooks_json_has_the_gate_entry():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    (g,) = [g for g in cfg["hooks"]["PostToolUse"] if g["hooks"][0]["command"].endswith("--gate")]
    assert g["matcher"] == "Bash" and g["hooks"][0]["timeout"] == 1800
    assert hookmain.watchdog("gate") == 1795


def test_the_main_post_tool_use_ignores_the_gate_cli(h):
    compiled(h)
    evt = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "harness-gate request"},
           "tool_response": GATE_OUT, "cwd": str(h["ws"]), "tool_use_id": "tu-g"}
    assert hookmain.main("PostToolUse", evt, h["env"])[0] == 0


def test_pre_tool_use_lets_the_gate_cli_run_in_a_task(h):
    from test_p3_hooks import pre
    compiled(h)
    assert pre(h, "Bash", command=str(ROOT / "bin" / "harness-gate") + " request") == 0
