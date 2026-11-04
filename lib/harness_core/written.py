"""Files written this session (spec §Hooks, PreToolUse safety: "execution of any file written in this task").

Writer: the PostToolUse hook (``on_post``) appends every path that Write, Edit,
MultiEdit or NotebookEdit wrote to ``sessions/<sid>/written.json``. SessionStart
creates the record (``start``) and snapshots the git-tracked files with their
ctime (``tracked.json``). Reader: ``self_written`` for ``ctx.written_this_task``:

- recorded writes, plus every file under ``.claude/loops/*/work`` and
  ``*/requests`` (agent-writable job dirs);
- when the record is missing or unreadable: every workspace file except a
  tracked file whose ctime is unchanged since SessionStart. mtime is never used
  (``touch -t`` sets it); ctime cannot be set by the user.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from pathlib import Path

from .safeio import atomic_write, safe_open

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
GIT = "/usr/bin/git"


def safe_id(v, what="session_id") -> str:
    if not isinstance(v, str) or not _ID.match(v) or v in (".", ".."):
        raise ValueError(f"invalid {what}: {v!r}")
    return v


def session_dir(data_root, sid) -> Path:
    return Path(data_root) / "sessions" / safe_id(sid)


def _load(path: Path, root):
    """JSON via safe_open (no symlink, no hard link); None when absent."""
    if not os.path.lexists(path):
        return None
    return json.loads(safe_open(path, [root]))


def _ctime(p) -> int:
    return os.lstat(p).st_ctime_ns


def tracked_files(workspace, run=subprocess.run) -> list:
    """Realpaths of git-tracked files; [] when not a repo. fsmonitor off: no repo-configured program runs."""
    try:
        r = run([GIT, "-c", "core.fsmonitor=false", "-C", os.fspath(workspace), "ls-files", "-z"],
                capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    out = r.stdout.decode("utf-8", "surrogateescape") if isinstance(r.stdout, bytes) else r.stdout
    return [os.path.realpath(os.path.join(os.fspath(workspace), x)) for x in out.split("\0") if x]


def start(data_root, sid, workspace, run=subprocess.run) -> None:
    sdir = session_dir(data_root, sid)
    snap = {}
    for p in tracked_files(workspace, run):
        try:
            snap[p] = _ctime(p)
        except OSError:
            continue
    atomic_write(sdir / "tracked.json", json.dumps(snap).encode())
    atomic_write(sdir / "written.json", b"[]")


def on_post(evt, ctx) -> dict:
    """PostToolUse writer. A failure invalidates the record, so readers fall back to the strict rule."""
    evt = evt if isinstance(evt, dict) else {}
    tool = evt.get("tool_name")
    key = WRITE_TOOLS.get(tool)
    if key is None and tool != "Bash":
        return {}
    sdir = session_dir(ctx.data_root, evt.get("session_id"))
    try:
        if tool == "Bash":
            from . import pathtrack

            cmd = (evt.get("tool_input") or {}).get("command")
            if not isinstance(cmd, str):
                raise ValueError("Bash without a command")
            targets = pathtrack.bash_write_targets(cmd, ctx)
            if any(t is None for t in targets):
                raise ValueError("Bash wrote to a path that cannot be resolved")
            paths = list(targets)
        else:
            path = (evt.get("tool_input") or {}).get(key)
            if not isinstance(path, str) or not path:
                raise ValueError(f"{tool} without a path")
            if not os.path.isabs(path):
                path = os.path.join(os.fspath(ctx.workspace), path)
            paths = [path]
        rec = _load(sdir / "written.json", ctx.data_root)
        if not isinstance(rec, list):
            raise ValueError("written.json missing or not a list")
        new = [r for r in (os.path.realpath(p) for p in paths) if r not in rec]
        if new:
            atomic_write(sdir / "written.json", json.dumps(rec + new).encode())
        return {}
    except Exception:
        if os.path.lexists(sdir / "written.json"):
            os.unlink(sdir / "written.json")  # missing record => strict fallback
        raise


def _job_dirs(workspace) -> set:
    out = set()
    for d in glob.glob(os.path.join(os.fspath(workspace), ".claude", "loops", "*", "*")):
        if os.path.basename(d) in ("work", "requests"):
            for base, _, names in os.walk(d):
                out.update(os.path.realpath(os.path.join(base, n)) for n in names)
    return out


def _all_untrusted(sdir, data_root, workspace) -> set:
    try:
        snap = _load(sdir / "tracked.json", data_root)
    except Exception:
        snap = None
    snap = snap if isinstance(snap, dict) else {}
    out = set()
    # shortcut: walks the whole workspace (skipping .git) on each call; fine for small workspaces, cache by dir ctime if slow
    for base, dirs, names in os.walk(os.fspath(workspace)):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = os.path.realpath(os.path.join(base, n))
            try:
                if snap.get(p) != _ctime(p):
                    out.add(p)
            except OSError:
                out.add(p)
    return out


def self_written(data_root, sid, workspace) -> set:
    sdir = session_dir(data_root, sid)
    try:
        rec = _load(sdir / "written.json", data_root)
    except Exception:
        rec = None
    if isinstance(rec, list) and all(isinstance(x, str) for x in rec):
        return set(rec) | _job_dirs(workspace)
    return _all_untrusted(sdir, data_root, workspace) | _job_dirs(workspace)
