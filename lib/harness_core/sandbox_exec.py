"""Run agent-influenced code in an OS sandbox (spec §Trust boundaries: gate children).

Backends, picked by platform (``backend()``): macOS ``/usr/bin/sandbox-exec`` with the SBPL
profile below; Linux ``/usr/bin/bwrap`` (bubblewrap, ``bwrap_args``). ``probe()`` says whether
the backend works here; SessionStart refuses the session when it does not.

macOS profile: starts from ``(deny default)`` and allows only exec/fork, signals inside
the sandbox, sysctl reads, file reads (minus ``deny_read``) and writes under ``cwd``
and a fresh per-run temp dir (minus ``deny_write``, plus ``allow_write``). No
network, no Apple Events, no mach-lookup at all (so no LaunchServices ``open``),
and no writes to ``CLAUDE*.md`` or ``.mcp.json`` at any depth.
SBPL is last-match-wins, so rule order below is the policy.
The child runs in its own session; on timeout and on exit the whole process group
is killed, so background children cannot outlive the call.
Paths go into the profile exactly as given (never resolved here): callers pass
real paths they resolved and checked once, and the sandbox matches resolved paths,
so swapping a component for a symlink later cannot widen a rule.

Linux (bwrap): ``/`` read-only; fresh ``/dev`` and ``/proc``; ``/tmp``, ``/var/tmp`` and ``/run``
replaced by empty read-only tmpfs (no X11, D-Bus, ssh-agent or docker sockets); every namespace
unshared (no network; abstract sockets are per network namespace); ``cwd``, the temp dir and
``allow_write`` writable; ``deny_write`` and existing ``CLAUDE*.md``/``.mcp.json`` read-only;
``deny_read`` directories replaced by an empty mode-000 tmpfs. bwrap cannot refuse the *creation*
of a new ``CLAUDE*.md``/``.mcp.json``, so ``run`` removes any the child created and fails the call.
The child is pid 2 of its own pid namespace, so ``setsid()`` cannot outlive it.

Fails closed: if the backend is missing, ``run`` raises ``HarnessError``.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

from .errors import HarnessError

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
BWRAP = "/usr/bin/bwrap"
TIMEOUT_CODE = 124
INSTRUCTION_CODE = 126  # the child created a CLAUDE*.md / .mcp.json (Linux; removed after the run)
_INSTRUCTION = re.compile(r"claude[^/]*\.md|\.mcp\.json", re.IGNORECASE)
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _q(path) -> str:
    s = os.fspath(path)  # never resolved here: callers resolve and check once, so a later swap cannot move a rule
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(paths) -> str:
    return " ".join(f"(subpath {_q(p)})" for p in paths)


def secret_dirs(data_root, home) -> list:
    """keys/ and experience/ of this data root and of every installed plugin (``~/.claude/plugins/data/*``)."""
    out = [os.path.join(os.fspath(data_root), n) for n in ("keys", "experience")]
    for n in ("keys", "experience"):
        out += glob.glob(os.path.join(os.fspath(home), ".claude", "plugins", "data", "*", n))
    return out


def profile(cwd, tmp, deny_read=(), deny_write=(), allow_write=()) -> str:
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow file-read*)",
        '(allow file-ioctl (literal "/dev/null") (regex #"^/dev/(fd/|tty)"))',
        "(deny network*)",
        "(deny appleevent-send)",
        f'(allow file-write* {_subpaths([cwd, tmp])} (literal "/dev/null") (literal "/dev/zero") (regex #"^/dev/(fd/|tty|dtrace)"))',
    ]
    if deny_write:
        rules.append(f"(deny file-write* {_subpaths(deny_write)})")
    if allow_write:
        rules.append(f"(allow file-write* {_subpaths(allow_write)})")
    # after allow_write, so never re-allowed: next-session instructions and MCP config at any depth
    rules.append('(deny file-write* (regex #"/CLAUDE[^/]*\\.md$") (regex #"/\\.mcp\\.json$"))')
    if deny_read:
        rules.append(f"(deny file-read* file-write* {_subpaths(deny_read)})")
    return "\n".join(rules) + "\n"


def backend():
    """``"sandbox-exec"``, ``"bwrap"`` or None when this platform has no supported sandbox."""
    if sys.platform == "darwin":
        return "sandbox-exec" if os.path.exists(SANDBOX_EXEC) else None
    if sys.platform.startswith("linux"):
        return "bwrap" if os.path.exists(BWRAP) else None
    return None


def probe():
    """None when the sandbox works here, else why not (SessionStart refuses the session then)."""
    b = backend()
    if b is None:
        return f"no OS sandbox: need {SANDBOX_EXEC} (macOS) or {BWRAP} (Linux: apt install bubblewrap)"
    if b == "sandbox-exec":
        return None
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="harness-probe-"))
    try:
        r = subprocess.run(bwrap_args(tmp, tmp) + ["/bin/true"], cwd=tmp, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"bwrap failed to start: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode:
        why = r.stderr.decode("utf-8", "replace").strip()[:300]
        return f"bwrap cannot create a sandbox here (are unprivileged user namespaces allowed?): {why}"
    return None


def bwrap_args(cwd, tmp, deny_read=(), deny_write=(), allow_write=()) -> list:
    """bwrap argv (up to ``--``) for the Linux policy; later mounts cover earlier ones."""
    cwd, tmp = os.fspath(cwd), os.fspath(tmp)
    a = [BWRAP, "--die-with-parent", "--new-session", "--unshare-all",
         "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    hidden = [d for d in ("/tmp", "/var/tmp", "/run") if os.path.isdir(d) and not os.path.islink(d)]
    for d in hidden:
        a += ["--tmpfs", d]
    a += ["--bind", tmp, tmp, "--bind", cwd, cwd]
    for p in map(os.fspath, deny_write):
        if os.path.lexists(p) and not os.path.islink(p):  # a symlink's target is listed by the caller
            a += ["--ro-bind", p, p]
    for p in map(os.fspath, allow_write):
        if os.path.isdir(p) and not os.path.islink(p):
            a += ["--bind", p, p]
    for p in _instruction_files([cwd, *map(os.fspath, allow_write)]):  # after allow_write: never re-allowed
        a += ["--ro-bind", p, p]
    deny_read = [os.fspath(p) for p in deny_read]
    for p in deny_read:
        if any(q != p and p.startswith(q.rstrip("/") + "/") for q in deny_read):
            continue  # already hidden by an enclosing entry (and bwrap cannot mount inside its read-only tmpfs)
        if os.path.isdir(p) and not os.path.islink(p):
            a += ["--tmpfs", p, "--chmod", "0000", p, "--remount-ro", p]
        elif os.path.lexists(p):
            a += ["--ro-bind", "/dev/null", p]
    for d in hidden:  # last, after every mount point below them exists
        a += ["--remount-ro", d]
    return a + ["--chdir", cwd, "--"]


def _instruction_files(roots) -> set:
    out = set()
    for r in roots:
        for base, dirs, names in os.walk(r):
            dirs[:] = [d for d in dirs if d != ".git"]
            out.update(os.path.join(base, n) for n in names + dirs if _INSTRUCTION.fullmatch(n))
    return out


def _killpg(pgid) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(argv, cwd, stdin_bytes=None, timeout=60, deny_read=(), deny_write=(), allow_write=(), env=None):
    """Returns ``(code, stdout_bytes, stderr_bytes)``; a timeout gives code 124."""
    b = backend()
    if b is None:
        raise HarnessError(f"no OS sandbox ({SANDBOX_EXEC} or {BWRAP}) found: refusing to run unsandboxed")
    if not argv:
        raise HarnessError("sandbox_exec.run: empty argv")
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="harness-sbx-"))
    try:
        child_env = {"PATH": SAFE_PATH, "HOME": tmp, "TMPDIR": tmp,
                     "LANG": "en_US.UTF-8" if b == "sandbox-exec" else "C.UTF-8"}
        child_env.update(env or {})
        if b == "sandbox-exec":
            cmd = [SANDBOX_EXEC, "-p", profile(cwd, tmp, deny_read, deny_write, allow_write)]
            before = None
        else:
            cmd = bwrap_args(cwd, tmp, deny_read, deny_write, allow_write)
            before = _instruction_files([os.fspath(cwd), *map(os.fspath, allow_write)])
        p = subprocess.Popen(
            cmd + [os.fspath(a) for a in argv],
            cwd=os.fspath(cwd) if b == "sandbox-exec" else tmp,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env, start_new_session=True,
        )
        try:
            out, err = p.communicate(stdin_bytes if stdin_bytes is not None else b"", timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            _killpg(p.pid)
            out, err = p.communicate()
            code, out, err = TIMEOUT_CODE, out or b"", (err or b"") + b"\nharness-sandbox-exec: timeout"
        finally:
            _killpg(p.pid)  # background children of a finished command die with it
        if before is not None:
            made = _instruction_files([os.fspath(cwd), *map(os.fspath, allow_write)]) - before
            if made:  # bwrap cannot refuse creating them; the pid namespace is gone, so no race
                for m in sorted(made, reverse=True):
                    if os.path.isdir(m) and not os.path.islink(m):
                        shutil.rmtree(m, ignore_errors=True)
                    elif os.path.lexists(m):
                        os.unlink(m)
                note = "removed instruction files the command created: " + ", ".join(sorted(made))
                return INSTRUCTION_CODE, out, err + b"\nharness-sandbox-exec: " + note.encode("utf-8", "surrogateescape")
        return code, out, err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
