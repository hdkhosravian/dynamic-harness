"""Run agent-influenced code under macOS ``sandbox-exec`` (spec §Trust boundaries: gate children).

Profile: no network, no Apple Events, no reads under the given key dirs, writes
only under ``cwd`` and a fresh per-run temp dir (minus ``deny_write``, plus
``allow_write``). SBPL is last-match-wins, so rule order below is the policy.
Fails closed: if ``sandbox-exec`` is missing, ``run`` raises ``HarnessError``.
"""
from __future__ import annotations

import os
import shutil
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
        "(allow default)",
        "(deny network*)",
        "(deny appleevent-send)",
        "(deny file-write*)",
        f'(allow file-write* {_subpaths([cwd, tmp])} (literal "/dev/null") (literal "/dev/zero") (regex #"^/dev/(fd/|tty|dtrace)"))',
    ]
    if deny_write:
        rules.append(f"(deny file-write* {_subpaths(deny_write)})")
    if allow_write:
        rules.append(f"(allow file-write* {_subpaths(allow_write)})")
    if deny_read:
        rules.append(f"(deny file-read* file-write* {_subpaths(deny_read)})")
    return "\n".join(rules) + "\n"


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
        try:
            p = subprocess.run(
                [SANDBOX_EXEC, "-p", prof, *[os.fspath(a) for a in argv]],
                cwd=os.fspath(cwd), input=stdin_bytes if stdin_bytes is not None else b"",
                capture_output=True, timeout=timeout, env=child_env,
            )
        except subprocess.TimeoutExpired as e:
            return TIMEOUT_CODE, e.stdout or b"", (e.stderr or b"") + b"\nharness-sandbox-exec: timeout"
        return p.returncode, p.stdout, p.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
