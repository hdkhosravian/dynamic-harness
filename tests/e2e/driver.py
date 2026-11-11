"""Helpers for the live runs. Each one works without the `claude` binary; only `_run` starts it."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

from harness_core import gate, session
from harness_core.events import EventLog
from harness_core.gatehook import load_key
from harness_core.paths import job_paths
from harness_core.setup import PREFIX

ROOT = Path(__file__).resolve().parents[2]  # the plugin under test
DATA = Path(__file__).resolve().parent / "data"  # sample files; the rows are the ones the in-process tests use
MAX_TURNS = 8  # request calls per pack: the request, then at most 7 replies
SETUP_CALLS = 2  # setup plan and setup confirm, so a pack makes at most SETUP_CALLS + MAX_TURNS calls
CALL_TIMEOUT = 900  # seconds per claude call
MAX_BUDGET_USD = "2"  # a soft cap per claude call; nothing caps the sum over calls or packs
REPLY = "Yes, that is right. Please go ahead."  # answers the card approval, a definition, an assumption sheet, a plan
# Allowed without a prompt (nobody can answer one). Every pack runs harness-* commands (Bash: skills/task-card/SKILL.md:45,
# :89,:100; skills/<pack>/SKILL.md), reads its input (Read: task-card SKILL.md:9) and writes the card draft and work/report.md
# (Write: task-card SKILL.md:11-14,:95). Only research searches the web (WebSearch: agents/web-researcher.md:4,
# skills/research/SKILL.md:15) and starts subagents (Agent: skills/research/SKILL.md:15,:19). Page fetches go through
# `harness-claims fetch` (Bash), so no fetch tool. Plain Bash: no pattern set could be proved to cover every command
# without a live run. The optional second card draft (task-card SKILL.md:41) needs Agent and is skipped by the other packs.
TOOLS = {p: ("Bash", "Read", "Write") for p in ("finance", "model", "operations")}
TOOLS["research"] = ("Bash", "Read", "Write", "WebSearch", "Agent")
KEEP = ("PATH", "LANG", "ANTHROPIC_API_KEY")  # the only variables the child inherits
FILES = {"finance": ("sales.csv",), "model": ("bank.csv", "sales_history.csv"), "operations": ("client.txt",),
         "research": ()}
REQUESTS = {
    "finance": "What were our total sales in the month that sales.csv covers? Use sales.csv.",
    "model": "Forecast our cash for the next 6 months from bank.csv and sales_history.csv, and tell me when we would "
             "run out of cash if sales fell.",
    "operations": "Draft a short proposal for the client described in client.txt and save it as proposal.md. It must "
                  "state the price and the delivery time.",
    "research": "Research which Python 3 release is the latest stable one and when it was released. Use at least two "
                "independent sources and give me a short report.",
}


def preflight(environ):
    """(claude path, loop-contract root), or an AssertionError that says what the owner must install or set."""
    claude = shutil.which("claude", path=environ.get("PATH", ""))
    if not claude:
        raise AssertionError("HARNESS_E2E=1 but no 'claude' binary on PATH: install Claude Code first")
    lc = environ.get("HARNESS_E2E_LOOP_CONTRACT")
    problem = session.loop_contract_problem(lc) if lc else "HARNESS_E2E_LOOP_CONTRACT is not set: point it at a " \
        "loop-contract checkout or plugin folder of version 1.2.0 or later"
    if problem:
        raise AssertionError(problem)
    return claude, lc


def make_workspace(tmp, pack):
    ws = Path(tmp) / "ws"
    ws.mkdir()
    for name in FILES[pack]:
        shutil.copy(DATA / name, ws / name)
    return ws


def build_env(tmp, lc_root, environ):
    """The child's whole environment: a home and a Claude config folder inside tmp, so the data folder is too."""
    env = {k: environ[k] for k in KEEP if k in environ}
    for key, name in (("HOME", "home"), ("CLAUDE_CONFIG_DIR", "config"), ("TMPDIR", "tmp")):
        (Path(tmp) / name).mkdir(exist_ok=True)
        env[key] = str(Path(tmp) / name)
    return {**env, "LOOP_CONTRACT_ROOT": str(lc_root)}


def setup_prompt(code=None):
    return PREFIX if code is None else f"{PREFIX} confirm {code}"  # never the word that changes global settings


class ClaudeExit(RuntimeError):
    """A claude call exited non-zero."""


def claude_cmd(claude, prompt, sid, resume, lc_root, pack):
    """One `claude -p` call as an argument list (no shell)."""
    return [claude, "-p", prompt, "--output-format", "json",
            "--resume" if resume else "--session-id", sid,  # ASSUMPTION: --session-id starts a session, --resume continues it
            "--plugin-dir", str(ROOT), "--plugin-dir", str(lc_root),  # ASSUMPTION: repeatable, and hooks of both run
            "--max-budget-usd", MAX_BUDGET_USD,  # ASSUMPTION: a per-call cap that also holds for --resume
            "--permission-mode", "acceptEdits", "--permission-prompts", "none",  # ASSUMPTION: no prompt can stall a call
            "--allowedTools", ",".join(TOOLS[pack])]  # ASSUMPTION: the name for the subagent tool is Agent


def parse_setup_code(text):
    """The folder-only code in the setup plan (the line without the word that also changes global settings)."""
    m = re.search(r"setup confirm (\d{6})(?!\d)(?! global)", text)
    if not m:
        raise ValueError("no one-time setup code for this folder in the setup output: " + text[-400:])
    return m.group(1)


def _run(cmd, cwd, env):
    """Run one call in its own process group; on a timeout kill the whole group, not only the direct child."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        try:
            p.communicate(timeout=10)  # reap; a process that left the group could hold the pipes
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _planned_in(tmp, sid):
    """True when the plan call left setup/pending.json for this session anywhere under tmp."""
    for f in Path(tmp).glob("**/setup/pending.json"):
        try:
            if json.loads(f.read_text("utf-8")).get("sid") == sid:
                return True
        except (OSError, ValueError, AttributeError):
            pass
    return False


def setup_folder(claude, pack, ws, env, lc_root, run=_run):
    """Plan, check where the harness wrote, then confirm in the same session (the code is bound to it)."""
    sid = str(uuid.uuid4())
    plan = run(claude_cmd(claude, setup_prompt(), sid, False, lc_root, pack), ws, env)
    tmp = Path(env["HOME"]).parent
    if not _planned_in(tmp, sid):  # the plan call is blocked by the hook, so it cost nothing and changed nothing
        raise RuntimeError(f"the data folder is not under the temporary directory {tmp}: setup left no plan file "
                           "for this session there (the binary may ignore HOME and CLAUDE_CONFIG_DIR, or the plugin "
                           "did not load); no confirm and no request was sent. Output: "
                           + (plan.stdout + plan.stderr)[-300:])
    code = parse_setup_code(plan.stdout + plan.stderr)  # ASSUMPTION: the hook's block reason reaches claude's output
    done = run(claude_cmd(claude, setup_prompt(code), sid, True, lc_root, pack), ws, env)  # ASSUMPTION: a blocked prompt leaves a resumable session
    if "Applied" not in done.stdout + done.stderr:
        raise RuntimeError("setup was not applied: " + (done.stdout + done.stderr)[-400:])


def data_dir(root):
    """The harness's data folder, found by its signing key (Claude Code, not the harness, picks where it is)."""
    keys = sorted(Path(root).glob("**/keys/hmac.key"))
    assert keys, f"no data folder (keys/hmac.key) under {root}: setup did not write inside the temporary directory"
    assert len(keys) == 1, f"more than one data folder under {root}: {keys}"
    return keys[0].parent.parent


def find_job(data):
    runs = Path(data) / "runs"
    jobs = sorted((d for d in runs.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime) if runs.is_dir() else []
    return jobs[-1].name if jobs else None


def verify_job(data, ws, job):
    """The Stop hook's own receipt check (gate.verify_receipt): {"ok", "state"} or {"ok": False, "reason"}."""
    p = job_paths(data, ws, job)
    return gate.verify_receipt(p, EventLog(p.run, p.heads), load_key(data))


def passes(v):
    return bool(v["ok"]) and (v["state"] == "Checked" or v["state"].startswith("Done, not fully checked"))


def verdict(env, ws):
    data = data_dir(Path(env["HOME"]).parent)
    job = find_job(data)
    return verify_job(data, ws, job) if job else None


def converse(claude, pack, ws, env, lc_root, run=_run):
    """Send the request, then REPLY, until a receipt verifies or MAX_TURNS calls are used. Returns the last verdict."""
    sid, v = str(uuid.uuid4()), None
    for turn in range(MAX_TURNS):
        r = run(claude_cmd(claude, REQUESTS[pack] if turn == 0 else REPLY, sid, turn > 0, lc_root, pack), ws, env)
        if r.returncode:
            raise ClaudeExit(f"claude exited {r.returncode} on turn {turn + 1}: {r.stderr[-400:]}")
        v = verdict(env, ws)
        if v and v["ok"]:
            return v
    return v or {"ok": False, "reason": f"no receipt after {MAX_TURNS} turns"}


def result_state(stage, exc=None, v=None):
    """One state word per outcome, for the E2E_RESULT line: a receipt state, or what went wrong and where."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"TIMEOUT_{stage}"
    if isinstance(exc, ClaudeExit):
        return "CLAUDE_EXIT"
    if exc is not None:
        return f"{stage}_ERROR"
    return v["state"] if v.get("ok") else f"NO_RECEIPT({v['reason']})"
