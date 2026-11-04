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
pytestmark = pytest.mark.skipif(not os.path.exists(sandbox_exec.SANDBOX_EXEC), reason="macOS sandbox-exec only")


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


def test_network_blocked(box):
    cwd, _ = box
    code, out, err = py(
        "import socket\n"
        "try:\n socket.create_connection(('1.1.1.1', 80), timeout=3); print('CONNECTED')\n"
        "except OSError as e: print('blocked', e.errno)",
        cwd,
    )
    assert b"CONNECTED" not in out and b"blocked" in out, (out, err)


def test_keys_read_blocked(box):
    cwd, keys = box
    code, out, err = py(f"print(open({str(keys / 'hmac.key')!r}).read())", cwd, deny_read=[keys])
    assert code != 0 and b"s3cret" not in out
    assert b"Operation not permitted" in err or b"PermissionError" in err


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
    with pytest.raises(HarnessError):
        py("print(1)", box[0])


def test_profile_escapes_quotes(tmp_path):
    p = sandbox_exec.profile(tmp_path / 'we"ird', tmp_path, deny_read=[tmp_path / "k\\x"])
    assert '\\"' in p and "\\\\" in p


def test_bin_wrapper_blocks_network_and_keys(box, tmp_path):
    cwd, _ = box
    data = tmp_path / "data"
    (data / "keys").mkdir(parents=True)
    (data / "keys" / "k").write_text("s3cret")
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
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


def test_bin_denies_keys_of_every_installed_plugin(box, tmp_path):
    # env points CLAUDE_PLUGIN_DATA elsewhere; keys under ~/.claude/plugins/data/*/keys stay unreadable
    cwd, _ = box
    home = tmp_path / "home"
    k = home / ".claude" / "plugins" / "data" / "harness" / "keys"
    k.mkdir(parents=True)
    (k / "hmac.key").write_text("s3cret")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    env = {**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(decoy)}
    r = _bin(["--", PY, "-I", "-c", f"print(open({str(k / 'hmac.key')!r}).read())"], cwd, env)
    assert b"s3cret" not in r.stdout
