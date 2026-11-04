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
``deny_read`` directories replaced by an empty mode-000 tmpfs. bwrap cannot refuse *creating* a path
that did not exist (a new ``CLAUDE*.md``/``.mcp.json`` at any depth, or a missing ``deny_write`` path
such as ``.claude/``), so ``run`` compares by inode afterwards, renames each such path aside
(``<path>.harness-quarantine-<random>``; removed only if it cannot be moved) and fails the call with code 126.
The child is pid 2 of its own pid namespace, so ``setsid()`` cannot outlive it.

Fails closed: if the backend is missing, ``run`` raises ``HarnessError``.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import secrets
import tempfile

from .errors import HarnessError

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
BWRAP = "/usr/bin/bwrap"
TIMEOUT_CODE = 124
INSTRUCTION_CODE = 126  # Linux: the child created a path bwrap could not refuse (quarantined after the run)
_INSTRUCTION = re.compile(r"claude[^/]*\.md|\.mcp\.json", re.IGNORECASE)
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _q(path) -> str:
    s = os.fspath(path)  # never resolved here: callers resolve and check once, so a later swap cannot move a rule
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(paths) -> str:
    return " ".join(f"(subpath {_q(p)})" for p in paths)


def _write_rules(deny_write, allow_write) -> list:
    """``[(path, allow)]``, shallow first, deny after allow on the same path: the most specific rule wins
    (``work/`` inside a denied ``.claude`` is writable; ``prot/`` inside an allowed ``work/`` is not)."""
    rules = [(os.fspath(p), True) for p in allow_write] + [(os.fspath(p), False) for p in deny_write]
    return sorted(rules, key=lambda r: (len(os.path.normpath(r[0]).split(os.sep)), not r[1]))


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
    for path, allow in _write_rules(deny_write, allow_write):  # last match wins: most specific path last
        rules.append(f"({'allow' if allow else 'deny'} file-write* {_subpaths([path])})")
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


def _real_home() -> str:
    import pwd

    return pwd.getpwuid(os.getuid()).pw_dir  # not $HOME: the agent controls the environment


def _native_ok(b) -> bool:
    """The native backend can start here. Inside Claude Code's sandbox it cannot: nesting is refused
    (spike 2026-11-04: ``sandbox-exec: sandbox_apply: Operation not permitted``, exit 71)."""
    if b == "sandbox-exec":
        try:
            r = subprocess.run([SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                               capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0
    return probe() is None


def _net_reachable() -> bool:
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 443), timeout=2).close()
            return True
        except OSError:
            continue
    return False


def _data_writable(dirs) -> list:
    out = []
    for d in dirs:
        p = os.path.join(d, f".harness-probe-{secrets.token_hex(8)}")
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            continue
        os.close(fd)
        os.unlink(p)
        out.append(d)
    return out


def outer_contained():
    """None when, seen from inside this process, the surrounding sandbox holds the three properties the
    harness needs: no plugin ``keys/`` is readable, there is no direct network, and no plugin data root
    is writable. Otherwise the reason. Properties, not identity: whoever set them up, they hold. Faking
    them without a sandbox needs writes to the data root, which the PreToolUse classifier denies."""
    home = _real_home()
    datas = sorted(d for d in glob.glob(os.path.join(home, ".claude", "plugins", "data", "*")) if not os.path.islink(d))
    seen = 0
    for d in datas:
        k = os.path.join(d, "keys")
        try:
            names = os.listdir(k)
        except FileNotFoundError:
            continue
        except OSError:
            seen += 1  # denied (Seatbelt: EPERM)
            continue
        if names:
            return f"{k} is readable"
        seen += 1  # an empty view (a read-only tmpfs over it): nothing to read
    if not seen:
        return f"no plugin keys found under {home}/.claude/plugins/data to check (run /dynamic-harness:setup)"
    if _net_reachable():
        return "the network is reachable"
    bad = _data_writable(datas)
    if bad:
        return f"{bad[0]} is writable"
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
    for p, allow in _write_rules(deny_write, allow_write):  # a later mount covers: most specific path last
        if os.path.islink(p):
            continue  # a symlink's target is listed by the caller
        if allow and os.path.isdir(p):
            a += ["--bind", p, p]
        elif not allow and os.path.lexists(p):
            a += ["--ro-bind", p, p]
    for p in _instruction_files([cwd, *map(os.fspath, allow_write)], [x for x in map(os.fspath, deny_write) if os.path.isdir(x)]):
        # after allow_write: never re-allowed
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


def _instruction_files(roots, skip=()) -> dict:
    """``{path: (st_dev, st_ino)}`` of every CLAUDE*.md / .mcp.json under ``roots``; ``skip`` = read-only mounts.

    Fails closed: a directory the child made unlistable gets owner rwx back and is listed again;
    if it still cannot be listed, ``HarnessError``. A matched name that cannot be stat'ed counts as new."""
    out, skip, stack = {}, set(skip), [os.fspath(r) for r in roots]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except FileNotFoundError:
            continue
        except PermissionError:
            try:
                if not os.path.islink(d):
                    os.chmod(d, os.stat(d).st_mode | 0o700)
                entries = list(os.scandir(d))
            except OSError as e:
                raise HarnessError(f"cannot check {d} for instruction files: {e}") from e
        for e in entries:
            if _INSTRUCTION.fullmatch(e.name):
                try:
                    st = os.lstat(e.path)
                    out[e.path] = (st.st_dev, st.st_ino)
                except OSError:
                    out[e.path] = None
            if e.is_dir(follow_symlinks=False) and e.path not in skip:
                stack.append(e.path)
    return out


def _guard_before(cwd, deny_write, allow_write):
    roots = [os.fspath(cwd), *map(os.fspath, allow_write)]
    skip = [p for p in map(os.fspath, deny_write) if os.path.isdir(p) and not os.path.islink(p)]  # read-only mounts
    missing = [p for p in map(os.fspath, deny_write) if not os.path.lexists(p)]
    return roots, skip, missing, _instruction_files(roots, skip)


def _guard_after(state) -> list:
    """What bwrap could not refuse: new or replaced instruction files, and deny_write paths that appeared.
    Each is renamed aside (never deleted: a moved original or user files inside a new directory survive)."""
    roots, skip, missing, before = state
    after, seen = _instruction_files(roots, skip), {v for v in before.values() if v}
    bad = [p for p, ident in after.items() if before.get(p) != ident and ident not in seen]
    bad += [p for p in missing if os.path.lexists(p)]
    moved = sorted(p for p, ident in before.items() if after.get(p) != ident)
    out = []
    for p in sorted(set(bad), key=len, reverse=True):  # deepest first
        q = f"{p}.harness-quarantine-{secrets.token_hex(8)}"  # unpredictable: the child cannot pre-occupy it
        try:
            if os.path.lexists(q):
                raise OSError(f"{q} exists")
            os.rename(p, q)
            out.append(f"{p} -> {q}")
            continue
        except OSError:
            pass
        try:  # the path must not stay: remove it when it cannot be moved aside
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
            out.append(f"{p} (removed)")
        except OSError as e:
            raise HarnessError(f"could not quarantine or remove {p}: {e}") from e
    out += [f"{p} (original moved or replaced)" for p in moved]
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
        if not _native_ok(b):
            why = outer_contained()
            if why:
                raise HarnessError(f"the {b} sandbox cannot start here (inside another sandbox?), and the "
                                   f"surrounding one does not contain the command: {why}")
            # outer mode: Claude Code's sandbox contains it; this adds the clean env, the timeout, the
            # process-group kill and the instruction-file guard (deny_read/deny_write are the outer policy's)
            cmd, before, run_cwd = [], _guard_before(cwd, deny_write, allow_write), os.fspath(cwd)
        elif b == "sandbox-exec":
            cmd, before, run_cwd = [SANDBOX_EXEC, "-p", profile(cwd, tmp, deny_read, deny_write, allow_write)], None, os.fspath(cwd)
        else:
            cmd, before, run_cwd = bwrap_args(cwd, tmp, deny_read, deny_write, allow_write), _guard_before(cwd, deny_write, allow_write), tmp
        p = subprocess.Popen(
            cmd + [os.fspath(a) for a in argv],
            cwd=run_cwd,
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
            broke = _guard_after(before)  # the pid namespace is gone, so nothing races this
            if broke:
                note = "quarantined what the sandbox could not refuse: " + "; ".join(broke)
                return INSTRUCTION_CODE, out, err + b"\nharness-sandbox-exec: " + note.encode("utf-8", "surrogateescape")
        return code, out, err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
