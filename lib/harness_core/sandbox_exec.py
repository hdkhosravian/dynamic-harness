"""Run agent-influenced code under macOS ``sandbox-exec`` (spec §Trust boundaries: gate children).

Profile: starts from ``(deny default)`` and allows only exec/fork, signals inside
the sandbox, sysctl reads, file reads (minus ``deny_read``) and writes under ``cwd``
and a fresh per-run temp dir (minus ``deny_write``, plus ``allow_write``). No
network, no Apple Events, no mach-lookup at all (so no LaunchServices ``open``),
and no writes to ``CLAUDE*.md`` or ``.mcp.json`` at any depth.
SBPL is last-match-wins, so rule order below is the policy.
The child runs in its own session; on timeout and on exit the whole process group
is killed, so background children cannot outlive the call.
Fails closed: if ``sandbox-exec`` is missing, ``run`` raises ``HarnessError``.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile

from .errors import HarnessError

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TIMEOUT_CODE = 124
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _q(path) -> str:
    s = os.path.realpath(os.fspath(path))
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(paths) -> str:
    return " ".join(f"(subpath {_q(p)})" for p in paths)


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


def _killpg(pgid) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(argv, cwd, stdin_bytes=None, timeout=60, deny_read=(), deny_write=(), allow_write=(), env=None):
    """Returns ``(code, stdout_bytes, stderr_bytes)``; a timeout gives code 124."""
    if not os.path.exists(SANDBOX_EXEC):
        raise HarnessError(f"{SANDBOX_EXEC} not found: refusing to run unsandboxed")
    if not argv:
        raise HarnessError("sandbox_exec.run: empty argv")
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="harness-sbx-"))
    try:
        prof = profile(cwd, tmp, deny_read, deny_write, allow_write)
        child_env = {"PATH": SAFE_PATH, "HOME": tmp, "TMPDIR": tmp, "LANG": "en_US.UTF-8"}
        child_env.update(env or {})
        p = subprocess.Popen(
            [SANDBOX_EXEC, "-p", prof, *[os.fspath(a) for a in argv]],
            cwd=os.fspath(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env, start_new_session=True,
        )
        try:
            out, err = p.communicate(stdin_bytes if stdin_bytes is not None else b"", timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg(p.pid)
            out, err = p.communicate()
            return TIMEOUT_CODE, out or b"", (err or b"") + b"\nharness-sandbox-exec: timeout"
        finally:
            _killpg(p.pid)  # background children of a finished command die with it
        return p.returncode, out, err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
