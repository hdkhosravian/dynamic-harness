"""SessionStart checks (spec §Trust boundaries: "Session checks"; §Hooks SessionStart row).

Compares the control, business, gate, plugin-tree and loop-contract-tree hashes
against the latest entries in ``config_log.jsonl`` (written by setup and the
nonce-checked writers). It never re-baselines: it only reads the log. A
mismatch, an unreadable tree or a corrupt log is ``STOPPED_TAMPER``. It also
refuses ``bypassPermissions`` and Claude Code < 2.1.295 (no fail-closed hooks),
and runs an in-process PreToolUse canary. It needs the loop-contract plugin
root (``ctx.loop_contract_root``) at version >= 1.1.0.

With a ``session_id`` it also writes, under ``sessions/<sid>/``: the settings
baseline (for ConfigChange), the written-files record (``written.start``: fresh only
for ``source == "startup"``; compact/resume/clear/fork keep it) and
``status.json`` ``{ok, reasons, canary, ts, cwd}``. A failed status is written
before the checks run, so a crash never leaves an earlier ``ok`` behind.
PreToolUse allows only reads unless a fresh ``ok`` status exists.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import canon, config_guard, safety, sandbox_exec, setup, written
from .errors import HarnessError
from .safeio import atomic_write, safe_open

MIN_LOOP_CONTRACT = (1, 1, 0)  # fold_ledger flags the gate adapter passes
MIN_CC_VERSION = (2, 1, 295)  # hooks' onFailure: "block" (fail closed) needs this (OQ-4 spike S1)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}  # shortcut: caches unhashed; plugin root is write-protected


def tree_hash(root) -> str:
    if not root or not Path(root).exists():
        return "absent"
    root = Path(os.path.realpath(root))
    if root.is_file():
        return canon.sha256_bytes(safe_open(root, [root.parent]))
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        files += [Path(d) / n for n in names]
    return canon.tree_sha(files, root)


def current_hashes(ctx) -> dict:
    data = Path(ctx.data_root) if ctx.data_root else None
    out = {
        "control": tree_hash(data / "control") if data else "absent",
        "business": tree_hash(data / "business") if data else "absent",
        "gate": tree_hash(data / "control" / "gate.json") if data else "absent",
        "plugin": tree_hash(ctx.plugin_root),
    }
    if ctx.loop_contract_root:
        out["loop_contract"] = tree_hash(ctx.loop_contract_root)
    return out


def recorded_hashes(log_path: Path) -> dict:
    """Latest recorded hash per layer. Accepts ``{"hashes": {...}}`` and ``{"layer", "sha"}`` rows."""
    latest = {}
    for n, line in enumerate(log_path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise HarnessError(f"config_log.jsonl line {n} is not JSON") from e
        if isinstance(row.get("hashes"), dict):
            latest.update(row["hashes"])
        elif isinstance(row.get("layer"), str) and isinstance(row.get("sha"), str):
            latest[row["layer"]] = row["sha"]
    return latest


def _block(state, reason, ctx, **extra):
    if ctx.log is not None:
        try:
            ctx.log.append(state, {"reason": reason, **extra})
        except Exception as e:  # the block stands either way
            print(f"harness: {state} not logged: {type(e).__name__}: {e}", file=sys.stderr)
    return {"decision": "block", "state": state, "reason": reason, **extra}


def unapproved_procedures(ctx) -> list:
    """Shipped skills (``skills/**/SKILL.md``) without a matching ``approved_procedures.json`` entry. A
    symlinked skill file never counts as approved."""
    import hashlib

    from . import protected
    approved = {p.get("path"): p.get("sha256") for p in protected.load_control("approved_procedures.json", ctx)
                .get("procedures", []) if isinstance(p, dict)}
    root = Path(ctx.plugin_root)
    bad = []
    for base, dirs, names in os.walk(root / "skills"):
        for n in names:
            if n != "SKILL.md":
                continue
            f = Path(base) / n
            rel = str(f.relative_to(root))
            try:
                ok = not os.path.islink(f) and approved.get(rel) == hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                ok = False
            if not ok:
                bad.append(rel)
    return sorted(bad)


def canary(ctx) -> bool:
    """A known-irreversible call must be denied and a plain read allowed."""
    ctx = dataclasses.replace(ctx, log=None)  # the canary's own deny is not a SAFETY_DENY
    bad = safety.on_pre({"tool_name": "Bash", "tool_input": {"command": "curl https://canary.invalid/"}}, ctx)
    good = safety.on_pre({"tool_name": "Read", "tool_input": {"file_path": str(Path(ctx.workspace) / "README.md")}}, ctx)
    return bad.get("decision") == "deny" and good == {}


def claude_version(env=None, run=subprocess.run):
    """(major, minor, patch) from $CLAUDE_CODE_VERSION, else `claude --version`; None when unknown."""
    text = (os.environ if env is None else env).get("CLAUDE_CODE_VERSION")
    if not text:
        try:
            text = run(["claude", "--version"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def loop_contract_version(root):
    """(major, minor, patch) from <root>/.claude-plugin/plugin.json; None when unreadable."""
    try:
        f = Path(root) / ".claude-plugin" / "plugin.json"
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", json.loads(safe_open(f, [root])).get("version", ""))
    except Exception:
        return None
    return tuple(int(x) for x in m.groups()) if m else None


def on_start(evt, ctx) -> dict:
    evt = evt if isinstance(evt, dict) else {}
    sid = evt.get("session_id")
    if sid is None or not ctx.data_root:
        return _checks(evt, ctx)
    sdir = written.session_dir(ctx.data_root, sid)
    st = {"ok": False, "reasons": ["session checks did not finish"], "canary": None, "ts": time.time(),
          "cwd": str(ctx.workspace)}
    atomic_write(sdir / "status.json", json.dumps(st).encode())
    atomic_write(sdir / "settings_baseline.json", json.dumps(config_guard.snapshot(ctx)).encode())
    written.start(ctx.data_root, sid, ctx.workspace, source=evt.get("source"))
    out = _checks(evt, ctx)
    st.update(ok=out == {}, reasons=[out["reason"]] if out else [], ts=time.time(),
              canary=True if out == {} else (False if out.get("state") == "CANARY_FAILED" else None))
    atomic_write(sdir / "status.json", json.dumps(st).encode())
    return out


def _checks(evt, ctx) -> dict:
    mode = evt.get("permission_mode") or ctx.permission_mode
    if mode == "bypassPermissions":
        return _block("REFUSED", "bypassPermissions is refused: the harness needs its permission rules", ctx)
    ver = claude_version()
    if ver is None or ver < MIN_CC_VERSION:
        need = ".".join(map(str, MIN_CC_VERSION))
        have = ".".join(map(str, ver)) if ver else "unknown"
        return _block("REFUSED", f"Claude Code {have} < {need}: hooks cannot fail closed (onFailure=block); "
                                 "tasks above T0 are refused", ctx)
    if not ctx.loop_contract_root:
        return _block("REFUSED", "this folder is not set up (loop-contract location unknown): "
                                 "type /dynamic-harness:setup; tasks above T0 are refused", ctx)
    lv = loop_contract_version(ctx.loop_contract_root)
    if lv is None or lv < MIN_LOOP_CONTRACT:
        need = ".".join(map(str, MIN_LOOP_CONTRACT))
        return _block("REFUSED", f"loop-contract {'.'.join(map(str, lv)) if lv else 'unknown'} < {need}: "
                                 "tasks above T0 are refused", ctx)
    log_path = Path(ctx.data_root or "/nonexistent") / "config_log.jsonl"
    if not log_path.is_file():
        return _block("REFUSED", "this folder is not set up (no release baseline in config_log.jsonl): type /dynamic-harness:setup; tasks above T0 are refused", ctx)
    try:
        recorded = recorded_hashes(log_path)
        current = current_hashes(ctx)
    except (HarnessError, ValueError, OSError) as e:  # symlink/hard link in a tree, corrupt log
        return _block("STOPPED_TAMPER", f"config check failed: {e}", ctx, mismatch=["unreadable"])
    if not recorded:
        return _block("STOPPED_TAMPER", "config_log.jsonl holds no hashes", ctx, mismatch=["empty"])
    # a layer present now but never recorded is a mismatch too: nothing is baselined implicitly
    mismatch = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k, "absent"))
    if mismatch:
        return _block("STOPPED_TAMPER", f"hash mismatch against config_log.jsonl: {', '.join(mismatch)}", ctx,
                      mismatch=mismatch)
    bad = unapproved_procedures(ctx)
    if bad:  # R-5: every loaded procedure carries an approval record (content hash + approver)
        return _block("STOPPED_TAMPER", f"unapproved procedures (R-5): {', '.join(bad)}", ctx, mismatch=["procedures"])
    missing = setup.verify(ctx)
    if missing:  # the Claude Code sandbox and deny rules are the boundary; without them, reads only
        return _block("REFUSED", "this folder is not set up (" + "; ".join(missing[:4])
                      + (f"; and {len(missing) - 4} more" if len(missing) > 4 else "")
                      + "): type /dynamic-harness:setup; tasks above T0 are refused", ctx)
    problem = sandbox_exec.probe()
    if problem:  # gate children and harness-sandbox-exec need it; a session without it cannot finish a task
        return _block("REFUSED", f"{problem}: tasks above T0 are refused", ctx)
    if not canary(ctx):
        return _block("CANARY_FAILED", "PreToolUse canary failed: tasks above T0 are refused", ctx)
    return {}
