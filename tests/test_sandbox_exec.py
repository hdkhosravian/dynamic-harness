from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core import sandbox_exec
from harness_core.errors import HarnessError

PY = "/usr/bin/python3"
ROOT = Path(__file__).resolve().parents[1]
PROBLEM = sandbox_exec.probe()
pytestmark = pytest.mark.skipif(PROBLEM is not None, reason=str(PROBLEM))
mac = pytest.mark.skipif(sandbox_exec.backend() != "sandbox-exec", reason="macOS sandbox-exec profile")
linux = pytest.mark.skipif(sandbox_exec.backend() != "bwrap", reason="Linux bubblewrap")


@pytest.fixture
def box(tmp_path):
    cwd, keys = tmp_path / "cwd", tmp_path / "keys"
    cwd.mkdir()
    keys.mkdir()
    (keys / "hmac.key").write_text("s3cret")
    return cwd, keys


def py(code, cwd, **kw):
    return sandbox_exec.run([PY, "-I", "-c", code], cwd, **kw)


def test_normal_compute_works(box):
    cwd, keys = box
    code, out, err = py("print(6 * 7)", cwd, deny_read=[keys])
    assert (code, out.strip()) == (0, b"42"), err


def test_stdin_is_passed(box):
    cwd, _ = box
    code, out, _ = py("import sys; print(sys.stdin.read().upper())", cwd, stdin_bytes=b"abc")
    assert out.strip() == b"ABC"


_CONNECT = ("import socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 80), timeout=3); print('CONNECTED')\n"
            "except OSError as e: print('blocked', e.errno)")


def test_network_blocked(box):
    import errno
    cwd, _ = box
    code, out, err = py(_CONNECT, cwd)
    assert b"CONNECTED" not in out, (out, err)
    words = out.split()
    # the sandbox, not the net: Seatbelt refuses (EPERM/EACCES); an empty Linux network namespace has no route
    assert words[0] == b"blocked" and int(words[1]) in (errno.EPERM, errno.EACCES, errno.ENETUNREACH), (out, err)


def test_network_positive_control_unsandboxed(box):
    """Same probe outside the sandbox connects, so the denial above is the sandbox's doing."""
    r = subprocess.run([PY, "-I", "-c", _CONNECT], capture_output=True, timeout=30)
    if b"CONNECTED" not in r.stdout:
        pytest.skip(f"offline: {r.stdout!r}")


def test_keys_read_blocked(box):
    cwd, keys = box
    code, out, err = py(f"print(open({str(keys / 'hmac.key')!r}).read())", cwd, deny_read=[keys])
    assert code != 0 and b"s3cret" not in out
    assert b"Operation not permitted" in err or b"PermissionError" in err, err


def test_writes_only_under_cwd_and_temp(box, tmp_path):
    cwd, _ = box
    code, _, err = py("import os, tempfile; open('ok.txt','w').write('1'); open(os.path.join(tempfile.gettempdir(),'t'),'w').write('1')", cwd)
    assert code == 0, err
    assert (cwd / "ok.txt").exists()
    code, _, _ = py(f"open({str(tmp_path / 'outside.txt')!r},'w').write('1')", cwd)
    assert code != 0 and not (tmp_path / "outside.txt").exists()


def test_deny_write_inside_cwd_and_reallow(box):
    cwd, _ = box
    (cwd / ".git").mkdir()
    work = cwd / ".claude" / "loops" / "j1" / "work"
    work.mkdir(parents=True)
    kw = dict(deny_write=[cwd / ".git", cwd / ".claude"], allow_write=[work])
    assert py("open('.git/config','w').write('x')", cwd, **kw)[0] != 0
    assert py("open('.claude/settings.json','w').write('x')", cwd, **kw)[0] != 0
    assert py("open('.claude/loops/j1/work/r.md','w').write('x')", cwd, **kw)[0] == 0


def test_timeout(box):
    cwd, _ = box
    code, _, err = py("import time; time.sleep(5)", cwd, timeout=1)
    assert code == 124 and b"timeout" in err


def test_missing_sandbox_exec_fails_closed(box, monkeypatch):
    monkeypatch.setattr(sandbox_exec, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    monkeypatch.setattr(sandbox_exec, "BWRAP", "/nonexistent/bwrap")
    with pytest.raises(HarnessError):
        py("print(1)", box[0])
    assert sandbox_exec.probe() is not None


@mac
def test_profile_escapes_quotes(tmp_path):
    p = sandbox_exec.profile(tmp_path / 'we"ird', tmp_path, deny_read=[tmp_path / "k\\x"])
    assert '\\"' in p and "\\\\" in p


def test_bin_wrapper_blocks_network_and_keys(box, tmp_path):
    cwd, _ = box
    data = tmp_path / "data"
    (data / "keys").mkdir(parents=True)
    (data / "keys" / "k").write_text("s3cret")
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PROJECT_DIR": str(cwd)}
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "bin" / "harness-sandbox-exec"), "--", PY, "-I", "-c",
         f"print(open({str(data / 'keys' / 'k')!r}).read())"],
        cwd=cwd, env=env, capture_output=True, timeout=30,
    )
    assert r.returncode != 0 and b"s3cret" not in r.stdout
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "bin" / "harness-sandbox-exec"), "--", PY, "-I", "-c", "print(2+2)"],
        cwd=cwd, env=env, capture_output=True, timeout=30,
    )
    assert r.returncode == 0 and r.stdout.strip() == b"4"


def _bin(args, cwd, env):
    return subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-sandbox-exec"), *args],
                          cwd=cwd, env=env, capture_output=True, timeout=30)


def test_bin_refuses_without_plugin_data(box):
    cwd, _ = box
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_DATA"}
    r = _bin(["--", PY, "-I", "-c", "print(1)"], cwd, env)
    assert r.returncode == 2 and b"CLAUDE_PLUGIN_DATA" in r.stderr


def test_bin_refuses_cwd_inside_claude_home(box, tmp_path):
    cwd, _ = box
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(data)}
    r = _bin(["--cwd", str(home / ".claude"), "--", PY, "-I", "-c", "open('x','w').write('1')"], cwd, env)
    assert r.returncode == 2 and not (home / ".claude" / "x").exists()


def test_bin_refuses_cwd_inside_a_symlinked_claude_home(box, tmp_path):
    # ~/.claude -> a real dir inside the project (dotfiles setups): the resolved target is still ~/.claude
    cwd, _ = box
    real = cwd / "dotclaude"
    real.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").symlink_to(real)
    data = tmp_path / "data"
    data.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PROJECT_DIR": str(cwd)}
    r = _bin(["--cwd", str(real), "--", PY, "-I", "-c", "open('x','w').write('1')"], cwd, env)
    assert r.returncode == 2 and not (real / "x").exists()


def test_bin_denies_keys_of_every_installed_plugin(box, tmp_path):
    # env points CLAUDE_PLUGIN_DATA elsewhere; keys under ~/.claude/plugins/data/*/keys stay unreadable
    cwd, _ = box
    home = tmp_path / "home"
    k = home / ".claude" / "plugins" / "data" / "harness" / "keys"
    k.mkdir(parents=True)
    (k / "hmac.key").write_text("s3cret")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(decoy), "CLAUDE_PROJECT_DIR": str(cwd)}
    r = _bin(["--", PY, "-I", "-c", f"print(open({str(k / 'hmac.key')!r}).read())"], cwd, env)
    assert b"s3cret" not in r.stdout and b"refusing" not in r.stderr  # ran, and the read was denied


def test_bin_refuses_cwd_outside_project_dir(box, tmp_path):
    cwd, _ = box
    data = tmp_path / "data"
    data.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PROJECT_DIR": str(proj)}
    r = _bin(["--", PY, "-I", "-c", "print(1)"], cwd, env)
    assert r.returncode == 2 and b"outside the workspace" in r.stderr


def test_bin_refuses_without_project_dir(box, tmp_path):
    cwd, _ = box
    data = tmp_path / "data"
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    r = _bin(["--", PY, "-I", "-c", "print(1)"], cwd, env)
    assert r.returncode == 2 and b"CLAUDE_PROJECT_DIR" in r.stderr


# --- review fixes: kill the whole process group (2), deny-default profile (C) ---

_SLEEPER = ("import subprocess, time, sys\n"
            "subprocess.Popen(['/bin/sh', '-c', 'sleep 2; echo late > {flag}'],\n"
            "                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "time.sleep({main})\n")


def test_timeout_kills_background_children(box):
    import time
    cwd, _ = box
    flag = cwd / "late.txt"
    code, _, _ = py(_SLEEPER.format(flag=flag, main=10), cwd, timeout=1)
    assert code == 124
    time.sleep(3)
    assert not flag.exists(), "a background child of the timed-out process survived"


def test_exit_kills_background_children(box):
    import time
    cwd, _ = box
    flag = cwd / "late.txt"
    code, _, err = py(_SLEEPER.format(flag=flag, main=0), cwd, timeout=10)
    assert code == 0, err
    time.sleep(3)
    assert not flag.exists(), "a background child outlived the sandboxed command"


_MACH = ("import ctypes\n"
         "lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')\n"
         "port = ctypes.c_uint(0)\n"
         "kr = lib.bootstrap_look_up(ctypes.c_uint.in_dll(lib, 'bootstrap_port'),\n"
         "                           b'com.apple.coreservices.launchservicesd', ctypes.byref(port))\n"
         "print('kr', kr)\n")


@mac
def test_profile_starts_from_deny_default():
    p = sandbox_exec.profile("/tmp/c", "/tmp/t")
    assert "(deny default)" in p and "(allow default)" not in p
    assert "(deny network*)" in p


@mac
def test_launchservices_lookup_is_denied(box):
    """`open https://…` goes through LaunchServices over mach; the child must not reach it."""
    cwd, _ = box
    code, out, err = py(_MACH, cwd)
    assert code == 0, err
    assert out.strip() != b"kr 0", "mach-lookup of launchservicesd was allowed"


def test_subprocess_and_sh_still_work(box):
    cwd, _ = box
    code, out, err = sandbox_exec.run(["/bin/sh", "-c", "echo hi > x.txt && /bin/cat x.txt && /usr/bin/true"], cwd)
    assert (code, out.strip()) == (0, b"hi"), err
    code, out, err = py("import subprocess, hashlib, json, datetime, shlex, tempfile\n"
                        "print(subprocess.run(['/bin/echo', 'sub'], capture_output=True).stdout.decode().strip())", cwd)
    assert (code, out.strip()) == (0, b"sub"), err


def test_claude_md_and_mcp_json_are_never_writable(box):
    """Nested CLAUDE*.md and .mcp.json are instructions/config for the next session: deny at any depth."""
    cwd, _ = box
    (cwd / "a" / "b").mkdir(parents=True)
    for rel in ("CLAUDE.md", "a/b/CLAUDE.local.md", "a/CLAUDE-notes.md", ".mcp.json", "a/b/.mcp.json"):
        code, _, _ = py(f"open({rel!r}, 'w').write('x')", cwd, allow_write=[cwd])
        assert code != 0 and not (cwd / rel).exists(), rel
    assert py("open('a/b/notes.md', 'w').write('x'); open('a/mcp.json', 'w').write('x')", cwd)[0] == 0


@pytest.mark.parametrize("name", ["claude.md", "Claude.md", "CLAUDE.MD", "claude.local.md", ".MCP.json", ".mcp.JSON"])
def test_case_variants_of_instruction_files_are_not_writable(box, name):
    # macOS file systems are case-insensitive: Claude Code would load claude.md as CLAUDE.md
    cwd, _ = box
    code, _, _ = py(f"open({name!r}, 'w').write('x')", cwd)
    assert code != 0 and not (cwd / name).exists()


@mac
def test_case_variant_of_denied_dir_is_not_writable(box):  # APFS is case-insensitive; ext4 is not
    cwd, _ = box
    (cwd / ".claude").mkdir()
    code, _, _ = py("import os; open(os.path.join('.CLAUDE', 'settings.json'), 'w').write('x')", cwd,
                    deny_write=[str(cwd / ".claude")])
    assert code != 0 and not (cwd / ".claude" / "settings.json").exists()


@mac
def test_profile_does_not_resolve_its_inputs(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    p = sandbox_exec.profile(tmp_path / "link", tmp_path / "t", deny_write=[tmp_path / "link"])
    assert str(tmp_path / "real") not in p and f'(subpath "{tmp_path / "link"}")' in p


def test_cwd_swapped_to_a_symlink_after_resolution_stays_unwritable(tmp_path):
    # the launcher resolves and checks once; a swap afterwards must not move the write allowance
    work, target = tmp_path / "work", tmp_path / "target"
    work.mkdir()
    target.mkdir()
    resolved = os.path.realpath(work)
    work.rename(tmp_path / "moved")
    work.symlink_to(target)
    code, _, _ = sandbox_exec.run(["/bin/sh", "-c", f"echo x > {target}/f"], resolved)
    assert code != 0 and not (target / "f").exists()


def test_secret_dirs_cover_every_installed_plugin(tmp_path):
    home = tmp_path / "home"
    for d in ("a/keys", "b/experience"):
        (home / ".claude" / "plugins" / "data" / d).mkdir(parents=True)
    got = sandbox_exec.secret_dirs(tmp_path / "data", home)
    base = home / ".claude" / "plugins" / "data"
    assert {str(tmp_path / "data" / "keys"), str(base / "a" / "keys"), str(base / "b" / "experience")} <= set(got)


def test_bin_denies_reading_and_locking_anything_in_the_data_root(box, tmp_path):
    cwd, _ = box
    data = tmp_path / "data"
    (data / "sessions" / "s1").mkdir(parents=True)
    (data / "sessions" / "s1" / "written.json").write_text('["/x"]')
    (data / "sessions" / "s1" / "written.lock").write_text("")
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PROJECT_DIR": str(cwd)}
    lock = data / "sessions" / "s1" / "written.lock"
    r = _bin(["--", PY, "-I", "-c", f"print(open({str(data / 'sessions' / 's1' / 'written.json')!r}).read())"], cwd, env)
    assert r.returncode != 0 and b"/x" not in r.stdout and b"refusing" not in r.stderr
    r = _bin(["--", PY, "-I", "-c", f"import fcntl; fcntl.flock(open({str(lock)!r}), fcntl.LOCK_EX); print('LOCKED')"],
             cwd, env)
    assert b"LOCKED" not in r.stdout


def _launcher():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("harness_sandbox_exec_bin", str(ROOT / "bin" / "harness-sandbox-exec"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_launcher_resolve_refuses_a_symlink_in_the_resolved_path(tmp_path, monkeypatch):
    # realpath, then lstat every component: a component swapped to a symlink in between is refused
    mod = _launcher()
    (tmp_path / "a").mkdir()
    assert mod._resolve(str(tmp_path / "a" / "missing")) == os.path.realpath(tmp_path / "a" / "missing")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    monkeypatch.setattr(mod.os.path, "realpath", lambda p: str(tmp_path / "b" / "x"))
    with pytest.raises(ValueError):
        mod._resolve(str(tmp_path / "anything"))


# --- Linux (bubblewrap) ---


@linux
def test_linux_child_cannot_unmount_its_way_to_the_keys(box):
    # mounts made by bwrap are locked in the child's user namespace; a nested one cannot peel them either
    cwd, keys = box
    sh = (f"umount {keys} 2>/dev/null; cat {keys}/hmac.key 2>/dev/null; "
          f"unshare -rm sh -c 'umount {keys}; cat {keys}/hmac.key' 2>/dev/null; true")
    code, out, err = sandbox_exec.run(["/bin/sh", "-c", sh], cwd, deny_read=[keys])
    assert b"s3cret" not in out, (out, err)


@linux
def test_linux_setsid_child_does_not_outlive_the_call(box):
    import time
    cwd, _ = box
    flag = cwd / "late.txt"
    code, _, err = sandbox_exec.run(["/bin/sh", "-c", f"setsid sh -c 'sleep 2; echo late > {flag}' & exit 0"], cwd)
    assert code == 0, err
    time.sleep(3)
    assert not flag.exists(), "a setsid child escaped the pid namespace"


@linux
def test_linux_runtime_sockets_and_host_tmp_are_hidden(box, tmp_path):
    cwd, _ = box
    (tmp_path / "host-tmp-file").write_text("x")
    code, out, err = sandbox_exec.run(["/bin/sh", "-c", "ls -A /run /var/tmp; ls /tmp"], cwd)
    assert b"user" not in out and b"docker.sock" not in out, out
    assert b"host-tmp-file" not in out


@linux
def test_linux_new_instruction_file_is_removed_and_fails_the_call(box):
    cwd, _ = box
    code, _, err = py("open('CLAUDE.md', 'w').write('x')", cwd)
    assert code == sandbox_exec.INSTRUCTION_CODE and not (cwd / "CLAUDE.md").exists(), err
    assert b"quarantined" in err and list(cwd.glob("CLAUDE.md.harness-quarantine-*"))


@linux
def test_linux_missing_deny_write_path_cannot_be_created(box):
    cwd, _ = box  # no .claude yet: creating .claude/settings.json would configure the next session
    code, _, err = py("import os; os.mkdir('.claude'); open('.claude/settings.json', 'w').write('{}')", cwd,
                      deny_write=[cwd / ".claude"])
    assert code == sandbox_exec.INSTRUCTION_CODE and not (cwd / ".claude").exists(), err


@linux
def test_linux_rename_then_recreate_is_caught_and_the_original_is_kept(box):
    cwd, _ = box
    (cwd / "a").mkdir()
    (cwd / "a" / "CLAUDE.md").write_text("orig")
    code, out, err = sandbox_exec.run(
        ["/bin/sh", "-c", "mv a b 2>&1; mkdir -p a; echo evil > a/CLAUDE.md; echo done"], cwd)
    assert b"done" in out, err
    if (cwd / "b").exists():  # the kernel allowed renaming the parent of the read-only mount
        assert (cwd / "b" / "CLAUDE.md").read_text() == "orig"  # never deleted
        assert code == sandbox_exec.INSTRUCTION_CODE, err
    assert not (cwd / "a" / "CLAUDE.md").exists() or (cwd / "a" / "CLAUDE.md").read_text() == "orig"


@linux
def test_linux_new_instruction_file_inside_a_writable_git_dir_is_caught(box):
    cwd, _ = box
    code, _, err = py("import os; os.makedirs('.git'); open('.git/CLAUDE.md', 'w').write('x')", cwd)
    assert code == sandbox_exec.INSTRUCTION_CODE and not (cwd / ".git" / "CLAUDE.md").exists(), err


def _guard(cwd, **kw):
    return sandbox_exec._guard_before(cwd, kw.get("deny_write", ()), kw.get("allow_write", ()))


def test_guard_quarantine_name_is_not_predictable(tmp_path):
    st = _guard(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("evil")
    out = sandbox_exec._guard_after(st)
    q = [p for p in tmp_path.iterdir() if p.name.startswith("CLAUDE.md.harness-quarantine-")]
    assert len(q) == 1 and len(q[0].name.rsplit("-", 1)[1]) >= 16 and not (tmp_path / "CLAUDE.md").exists(), out


def test_guard_falls_back_to_removal_when_rename_fails(tmp_path, monkeypatch):
    st = _guard(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("evil")
    monkeypatch.setattr(sandbox_exec.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("blocked")))
    sandbox_exec._guard_after(st)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_guard_raises_when_the_path_cannot_be_removed(tmp_path, monkeypatch):
    st = _guard(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("evil")
    boom = lambda *a, **k: (_ for _ in ()).throw(OSError("blocked"))
    monkeypatch.setattr(sandbox_exec.os, "rename", boom)
    monkeypatch.setattr(sandbox_exec.os, "unlink", boom)
    with pytest.raises(HarnessError):
        sandbox_exec._guard_after(st)


def test_deny_write_inside_allow_write_stays_read_only(box):
    # most specific path wins, both ways: work/ inside .claude is writable, prot/ inside work/ is not
    cwd, _ = box
    work = cwd / ".claude" / "loops" / "j1" / "work"
    (work / "prot").mkdir(parents=True)
    kw = dict(deny_write=[cwd / ".claude", work / "prot"], allow_write=[work])
    assert py("open('.claude/loops/j1/work/ok.md','w').write('x')", cwd, **kw)[0] == 0
    assert py("open('.claude/loops/j1/work/prot/f','w').write('x')", cwd, **kw)[0] != 0
    assert not (work / "prot" / "f").exists()


def test_guard_sees_into_a_directory_the_child_made_unreadable(tmp_path):
    st = _guard(tmp_path)
    (tmp_path / "hide").mkdir()
    (tmp_path / "hide" / "CLAUDE.md").write_text("evil")
    os.chmod(tmp_path / "hide", 0)
    try:
        out = sandbox_exec._guard_after(st)
    finally:
        os.chmod(tmp_path / "hide", 0o700)
    assert not (tmp_path / "hide" / "CLAUDE.md").exists(), out


# --- inside Claude Code's sandbox: the native sandbox cannot nest (spike: sandbox_apply EPERM) ---


def _home_with_keys(tmp_path, readable=True):
    keys = tmp_path / "rh" / ".claude" / "plugins" / "data" / "dynamic-harness-x" / "keys"
    keys.mkdir(parents=True)
    (keys / "hmac.key").write_bytes(b"k" * 32)
    if not readable:
        os.chmod(keys, 0)
    return tmp_path / "rh"


def test_outer_probe_refuses_when_keys_are_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_real_home", lambda: str(_home_with_keys(tmp_path)))
    reason = sandbox_exec.outer_contained()
    assert reason and "readable" in reason


def test_outer_probe_refuses_without_any_keys_to_check(tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(sandbox_exec, "_real_home", lambda: str(tmp_path / "empty"))
    assert "no plugin keys" in sandbox_exec.outer_contained()


def test_outer_probe_refuses_when_the_network_is_reachable(tmp_path, monkeypatch):
    h = _home_with_keys(tmp_path, readable=False)
    try:
        monkeypatch.setattr(sandbox_exec, "_real_home", lambda: str(h))
        monkeypatch.setattr(sandbox_exec, "_net_reachable", lambda: True)
        monkeypatch.setattr(sandbox_exec, "_data_writable", lambda dirs: [])
        assert "network" in sandbox_exec.outer_contained()
    finally:
        os.chmod(h / ".claude" / "plugins" / "data" / "dynamic-harness-x" / "keys", 0o700)


def test_outer_probe_refuses_when_a_data_root_is_writable(tmp_path, monkeypatch):
    h = _home_with_keys(tmp_path, readable=False)
    try:
        monkeypatch.setattr(sandbox_exec, "_real_home", lambda: str(h))
        monkeypatch.setattr(sandbox_exec, "_net_reachable", lambda: False)
        assert "writable" in sandbox_exec.outer_contained()  # an unsandboxed chmod 000 cannot fake containment
    finally:
        os.chmod(h / ".claude" / "plugins" / "data" / "dynamic-harness-x" / "keys", 0o700)


def test_outer_mode_runs_contained_commands_with_a_clean_env(box, monkeypatch):
    cwd, _ = box
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@localhost:1")
    code, out, err = py("import os; print(sorted(k for k in os.environ if 'PROXY' in k.upper()))", cwd)
    assert (code, out.strip()) == (0, b"[]"), err


def test_outer_mode_still_quarantines_instruction_files(box, monkeypatch):
    cwd, _ = box
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    code, _, err = py("open('CLAUDE.md', 'w').write('x')", cwd)
    assert code == sandbox_exec.INSTRUCTION_CODE and not (cwd / "CLAUDE.md").exists(), err


def test_no_native_sandbox_and_no_containment_fails_closed(box, monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: "keys readable")
    with pytest.raises(HarnessError, match="keys readable"):
        py("print(1)", box[0])


def test_launcher_outer_mode_needs_no_claude_env(box, monkeypatch, capfdbinary):
    # live finding: the Bash tool's environment has no CLAUDE_PROJECT_DIR / CLAUDE_PLUGIN_* variables
    cwd, _ = box
    mod = _launcher()
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    monkeypatch.setattr(sandbox_exec, "_verify_outer", lambda dr, dw: None)  # stands in for the outer sandbox
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mod.sys, "stdin", None)
    code = mod.main(["--cwd", str(cwd), "--", PY, "-I", "-c", "print(6*7)"])
    assert code == 0 and capfdbinary.readouterr().out.strip() == b"42"


def test_launcher_outer_mode_refuses_when_containment_fails(box, monkeypatch, capfdbinary):
    cwd, _ = box
    mod = _launcher()
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: "the network is reachable")
    monkeypatch.setattr(mod.sys, "stdin", None)
    code = mod.main(["--cwd", str(cwd), "--", PY, "-I", "-c", "print(6*7)"])
    assert code == 2 and b"network is reachable" in capfdbinary.readouterr().err


# --- review of a7e77b6: decide the mode once, never drop deny lists, a stronger probe ---


def _rc(code, err=b""):
    return subprocess.CompletedProcess([], code, b"", err)


@pytest.mark.skipif(sandbox_exec.backend() != "sandbox-exec", reason="macOS nesting error")
def test_only_the_nesting_error_means_inside_another_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox_exec.subprocess, "run", lambda *a, **k: _rc(71, b"sandbox-exec: sandbox_apply: Operation not permitted\n"))
    assert sandbox_exec._native_ok("sandbox-exec") == "nested"
    monkeypatch.setattr(sandbox_exec.subprocess, "run", lambda *a, **k: _rc(1, b"something else"))
    assert sandbox_exec._native_ok("sandbox-exec") == "broken"


def test_a_broken_native_sandbox_fails_closed_even_when_contained(box, monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "broken")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    with pytest.raises(HarnessError, match="cannot start"):
        py("print(1)", box[0])


def test_outer_mode_refuses_a_deny_read_it_cannot_see_enforced(box, monkeypatch):
    cwd, keys = box  # keys/ is readable here: the outer sandbox does not cover it
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    with pytest.raises(HarnessError, match="readable"):
        py("print(1)", cwd, deny_read=[keys])


def test_outer_mode_refuses_a_deny_write_it_cannot_see_enforced(box, monkeypatch):
    cwd, _ = box
    (cwd / ".git").mkdir()
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    with pytest.raises(HarnessError, match="writable"):
        py("print(1)", cwd, deny_write=[cwd / ".git"])


def test_outer_probe_opens_the_key_by_name(tmp_path, monkeypatch):
    # a listing can be hidden while the file itself still opens
    h = _home_with_keys(tmp_path)
    real_listdir = os.listdir
    monkeypatch.setattr(sandbox_exec, "_real_home", lambda: str(h))
    monkeypatch.setattr(sandbox_exec.os, "listdir", lambda p: [] if str(p).endswith("/keys") else real_listdir(p))
    assert "readable" in sandbox_exec.outer_contained()


def _fake_proxy(status):
    import socket as s
    import threading
    srv = s.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(f"HTTP/1.1 {status}\r\n\r\n".encode())
        c.close()
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return f"http://u:p@127.0.0.1:{srv.getsockname()[1]}"


@pytest.mark.parametrize("status, ok", [("200 Connection established", False), ("403 Forbidden", True)])
def test_proxy_that_tunnels_counts_as_network(monkeypatch, status, ok):
    for v in ("HTTP_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", _fake_proxy(status))
    assert (sandbox_exec._proxy_check() is None) is ok


def test_no_proxy_to_test_fails_closed(monkeypatch):
    # inside Claude Code's sandbox the proxy variables are always set; stripping them is not a pass
    for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    assert "no sandbox proxy" in sandbox_exec._proxy_check()


def test_an_untestable_proxy_fails_closed(monkeypatch):
    for v in ("HTTP_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")  # nothing listens
    assert "could not be tested" in sandbox_exec._proxy_check()


def test_outer_mode_refuses_a_creatable_missing_protected_path(box, monkeypatch):
    cwd, _ = box  # CLAUDE.md does not exist and can be created here: the outer sandbox does not deny it
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    with pytest.raises(HarnessError, match="can be created"):
        py("print(1)", cwd, deny_write=[cwd / "CLAUDE.md"])
    assert not (cwd / "CLAUDE.md").exists()  # the probe left nothing behind


def test_outer_mode_checks_the_target_of_a_symlinked_protected_name(box, tmp_path, monkeypatch):
    cwd, _ = box
    (tmp_path / "notes.md").write_text("x")
    (cwd / "CLAUDE.md").symlink_to(tmp_path / "notes.md")
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    with pytest.raises(HarnessError, match="writable"):
        py("print(1)", cwd, deny_write=[cwd / "CLAUDE.md"])


def test_run_refuses_when_the_callers_mode_differs(box, monkeypatch):
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "ok")
    with pytest.raises(HarnessError, match="mode"):
        sandbox_exec.run(["/usr/bin/true"], box[0], mode="nested")


def test_launcher_outer_mode_keeps_the_workspace_checks(box, monkeypatch, capfdbinary):
    cwd, _ = box
    (cwd / ".git" / "hooks").mkdir(parents=True)
    mod = _launcher()
    monkeypatch.setattr(sandbox_exec, "_native_ok", lambda b: "nested")
    monkeypatch.setattr(sandbox_exec, "outer_contained", lambda: None)
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mod.sys, "stdin", None)
    code = mod.main(["--cwd", str(cwd / ".git" / "hooks"), "--", PY, "-I", "-c", "print(1)"])
    assert code == 2 and b"protected" in capfdbinary.readouterr().err
