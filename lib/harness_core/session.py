"""SessionStart checks (spec §Trust boundaries: "Session checks"; §Hooks SessionStart row).

Compares the control, business, gate, plugin-tree and loop-contract-tree hashes
against the latest entries in ``config_log.jsonl`` (written by setup and the
nonce-checked writers). It never re-baselines: it only reads the log. A
mismatch, an unreadable tree or a corrupt log is ``STOPPED_TAMPER``. It also
refuses ``bypassPermissions`` and Claude Code < 2.1.295 (no fail-closed hooks),
and runs an in-process PreToolUse canary.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import canon, safety
from .errors import HarnessError
from .safeio import safe_open

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
        except Exception:
            pass
    return {"decision": "block", "state": state, "reason": reason, **extra}


def canary(ctx) -> bool:
    """A known-irreversible call must be denied and a plain read allowed."""
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


def on_start(evt, ctx) -> dict:
    mode = (evt or {}).get("permission_mode") or ctx.permission_mode
    if mode == "bypassPermissions":
        return _block("REFUSED", "bypassPermissions is refused: the harness needs its permission rules", ctx)
    ver = claude_version()
    if ver is None or ver < MIN_CC_VERSION:
        need = ".".join(map(str, MIN_CC_VERSION))
        have = ".".join(map(str, ver)) if ver else "unknown"
        return _block("REFUSED", f"Claude Code {have} < {need}: hooks cannot fail closed (onFailure=block); "
                                 "tasks above T0 are refused", ctx)
    log_path = Path(ctx.data_root or "/nonexistent") / "config_log.jsonl"
    if not log_path.is_file():
        return _block("REFUSED", "no config_log.jsonl baseline: run /harness:setup; tasks above T0 are refused", ctx)
    try:
        recorded = recorded_hashes(log_path)
        current = current_hashes(ctx)
    except (HarnessError, ValueError, OSError) as e:  # symlink/hard link in a tree, corrupt log
        return _block("STOPPED_TAMPER", f"config check failed: {e}", ctx, mismatch=["unreadable"])
    if not recorded:
        return _block("STOPPED_TAMPER", "config_log.jsonl holds no hashes", ctx, mismatch=["empty"])
    mismatch = sorted(k for k, v in recorded.items() if current.get(k, "absent") != v)
    if mismatch:
        return _block("STOPPED_TAMPER", f"hash mismatch against config_log.jsonl: {', '.join(mismatch)}", ctx,
                      mismatch=mismatch)
    if not canary(ctx):
        return _block("CANARY_FAILED", "PreToolUse canary failed: tasks above T0 are refused", ctx)
    return {}
