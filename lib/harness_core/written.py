"""Files written this session (spec §Hooks, PreToolUse safety: "execution of any file written in this task").

Writers append realpaths to ``sessions/<sid>/written.json`` under an ``flock`` on
``written.lock`` (read-modify-write; sandboxed code cannot reach the data root):

- PreToolUse (``record_bash``, called by ``safety.on_pre`` once a Bash call is
  allowed): ``pathtrack.bash_write_targets`` against the event cwd, recorded
  *before* the command runs, so it never depends on PostToolUse firing;
- PostToolUse and PostToolUseFailure (``on_post``): the path Write, Edit,
  MultiEdit or NotebookEdit wrote.

An unresolvable target or any writer failure deletes the record (strict
fallback). SessionStart (``start``) with ``source == "startup"`` creates the
record and the ctime baseline (``started.json``, ``tracked.json`` = git-tracked
files with their ctime); compact/resume/clear/fork keep what is there.

Reader (``session_sets``, one workspace walk per call; ``.git`` skipped):

- written: recorded paths (a recorded directory covers every file under it,
  e.g. a ``cp -r`` / ``tar -x`` destination) plus every file under
  ``.claude/loops/*/work`` and ``*/requests``; when the record is missing or
  unreadable, every workspace file except a tracked file whose ctime is
  unchanged since SessionStart (strict fallback);
- untrusted: every workspace file whose ctime is at or after SessionStart, or a
  tracked file whose ctime changed (``ctx.untrusted_files``).

mtime is never used (``touch -t`` sets it); ctime cannot be set by the user.
"""
from __future__ import annotations

import contextlib
import fcntl
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .safeio import atomic_write, safe_open

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
GIT = "/usr/bin/git"
LOCK = "written.lock"


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


@contextlib.contextmanager
def _locked(sdir: Path):
    fd = os.open(os.fspath(sdir / LOCK), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the lock


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


def start(data_root, sid, workspace, run=subprocess.run, source="startup") -> None:
    """Only ``startup`` starts a fresh record; any other source (compact, resume, clear, fork, unknown) keeps it."""
    sdir = session_dir(data_root, sid)
    sdir.mkdir(parents=True, exist_ok=True)
    with _locked(sdir):
        fresh = source == "startup"
        if not fresh and all(os.path.lexists(sdir / n) for n in ("started.json", "tracked.json")):
            return
        if not fresh:  # baseline lost: what was written before is unknown, so stay strict
            with contextlib.suppress(FileNotFoundError):
                os.unlink(sdir / "written.json")
        atomic_write(sdir / "started.json", json.dumps({"ctime_ns": time.time_ns()}).encode())  # before the snapshot
        snap = {}
        # baseline lost: a fresh snapshot would trust tracked files the agent already changed, so trust none
        for p in tracked_files(workspace, run) if fresh else ():
            try:
                snap[p] = _ctime(p)
            except OSError:
                continue
        atomic_write(sdir / "tracked.json", json.dumps(snap).encode())
        if fresh:
            atomic_write(sdir / "written.json", b"[]")


def _append(data_root, sid, paths, missing_ok=False) -> bool:
    """Add ``paths`` under the lock. False when the record was dropped (a None path or a bad record)."""
    sdir = session_dir(data_root, sid)
    if missing_ok and not os.path.lexists(sdir / "written.json"):
        return True  # already the strict fallback
    with _locked(sdir):
        try:
            rec = _load(sdir / "written.json", data_root)
            if rec is None and missing_ok:
                return True
            if not isinstance(rec, list) or any(p is None for p in paths):
                raise ValueError("written.json missing or not a list, or an unresolvable write")
            new = [r for r in dict.fromkeys(os.path.realpath(p) for p in paths) if r not in rec]
            if new:
                atomic_write(sdir / "written.json", json.dumps(rec + new).encode())
            return True
        except Exception:
            if os.path.lexists(sdir / "written.json"):
                os.unlink(sdir / "written.json")  # missing record => strict fallback; an unlink error propagates
            return False


def record_bash(evt, ctx) -> None:
    """PreToolUse, after safety allowed a Bash call: record its write targets before it runs."""
    sid = evt.get("session_id")
    if ctx.data_root is None or sid is None:
        return
    from . import pathtrack

    cmd = (evt.get("tool_input") or {}).get("command")
    _append(ctx.data_root, sid, pathtrack.bash_write_targets(cmd, ctx) if isinstance(cmd, str) else [None], True)


def on_post(evt, ctx) -> dict:
    """PostToolUse / PostToolUseFailure writer. A failure invalidates the record, so readers fall back to strict."""
    evt = evt if isinstance(evt, dict) else {}
    key = WRITE_TOOLS.get(evt.get("tool_name"))
    if key is None:
        return {}
    path = (evt.get("tool_input") or {}).get(key)
    if isinstance(path, str) and path and not os.path.isabs(path):
        path = os.path.join(os.fspath(ctx.workspace), path)
    if not _append(ctx.data_root, evt.get("session_id"), [path if isinstance(path, str) and path else None], True):
        raise ValueError(f"{evt.get('tool_name')}: write not recordable; record dropped (strict fallback)")
    return {}


def _job_dirs(workspace) -> set:
    out = set()
    for d in glob.glob(os.path.join(os.fspath(workspace), ".claude", "loops", "*", "*")):
        if os.path.basename(d) in ("work", "requests"):
            for base, _, names in os.walk(d):
                out.update(os.path.realpath(os.path.join(base, n)) for n in names)
    return out


def _expand(rec) -> set:
    """Recorded paths; a recorded directory (copy/extract destination) covers every file under it."""
    out = set(rec)
    for r in rec:
        if os.path.isdir(r) and not os.path.islink(r):
            # shortcut: walks each recorded directory per call; a huge destination can hit the 4 s watchdog (deny)
            for base, dirs, names in os.walk(r):
                dirs[:] = [d for d in dirs if d != ".git"]  # as _scan: git's own files are not workspace code
                out.update(os.path.realpath(os.path.join(base, n)) for n in names)
    return out


def _scan(sdir, data_root, workspace):
    """One walk of the workspace (``.git`` skipped): (untrusted since SessionStart, strict fallback set)."""
    def load(name, kind):
        try:
            v = _load(sdir / name, data_root)
        except Exception:
            v = None
        return v if isinstance(v, kind) else None

    snap = load("tracked.json", dict) or {}
    started = (load("started.json", dict) or {}).get("ctime_ns")
    started = started if isinstance(started, int) and not isinstance(started, bool) else None
    untrusted, strict = set(), set()
    for base, dirs, names in os.walk(os.fspath(workspace)):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = os.path.realpath(os.path.join(base, n))
            try:
                ct = _ctime(p)
            except OSError:
                ct = None
            if ct is None or snap.get(p) != ct:
                strict.add(p)
            if ct is None or started is None or ct >= started or (p in snap and snap[p] != ct):
                untrusted.add(p)
    return untrusted, strict


def session_sets(data_root, sid, workspace):
    """(written_this_task, untrusted_files) from one workspace walk."""
    sdir = session_dir(data_root, sid)
    untrusted, strict = _scan(sdir, data_root, workspace)
    try:
        rec = _load(sdir / "written.json", data_root)
    except Exception:
        rec = None
    ok = isinstance(rec, list) and all(isinstance(x, str) for x in rec)
    return (_expand(rec) if ok else strict) | _job_dirs(workspace), untrusted


def self_written(data_root, sid, workspace) -> set:
    return session_sets(data_root, sid, workspace)[0]
