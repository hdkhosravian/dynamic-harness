"""The opt-in live scaffold under tests/e2e, proved without the claude binary, a model call or any cost."""
from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

import driver  # noqa: E402
from test_gate import KEY, _all_pass, _run, h  # noqa: E402,F401  (fixture h)
from test_setup import ctx, prompt  # noqa: E402,F401  (fixture ctx)

PACKS = ("finance", "model", "operations", "research")


def e2e_pytest(tmp_path, path, **extra):
    """`pytest tests/e2e` as the owner types it, in a child process with its own PATH and no HARNESS_E2E unless given."""
    env = {"PATH": path, "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),  # pytest may live in the user site
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **extra}  # no third-party plugin: the child starts faster
    return subprocess.run([sys.executable, "-m", "pytest", "tests/e2e", "-v", "-rs", "-p", "no:cacheprovider",
                           f"--basetemp={tmp_path / 'base'}"], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=120)


def test_four_tests_are_collected_and_all_skip_without_the_opt_in(tmp_path):
    r = e2e_pytest(tmp_path, "/usr/bin:/bin")
    assert "4 skipped" in r.stdout and r.returncode == 0, r.stdout + r.stderr
    for pack in PACKS:
        assert re.search(rf"test_packs_live\.py::test_pack\[{pack}\] SKIPPED", r.stdout), r.stdout
    assert "HARNESS_E2E=1" in r.stdout and "costs money" in r.stdout


def test_opt_in_without_the_claude_binary_fails_and_names_it(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = e2e_pytest(tmp_path, str(empty), HARNESS_E2E="1")
    assert "4 failed" in r.stdout and "skipped" not in r.stdout, r.stdout
    assert "no 'claude' binary on PATH" in r.stdout


@pytest.mark.parametrize("lc", ["unset", "old"])
def test_opt_in_with_an_unusable_loop_contract_fails_with_its_problem(tmp_path, lc):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text("#!/bin/sh\nexit 99\n")  # present, so the loop-contract is what is judged; never run
    (bin_ / "claude").chmod(0o755)
    extra = {}
    if lc == "old":
        root = tmp_path / "lc"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text('{"name": "loop-contract", "version": "1.1.0"}')
        extra["HARNESS_E2E_LOOP_CONTRACT"] = str(root)
    r = e2e_pytest(tmp_path, str(bin_), HARNESS_E2E="1", **extra)
    assert "4 failed" in r.stdout, r.stdout
    assert ("HARNESS_E2E_LOOP_CONTRACT is not set" if lc == "unset" else "loop-contract 1.1.0 < 1.2.0") in r.stdout


# --- driver.py, each helper without the binary ---


def test_command_lists_have_no_shell_string_and_no_global_word(tmp_path):
    lc = Path("/opt/lc")  # a fixed path: tmp_path carries the test's name, which says "global"
    cmds = [driver.claude_cmd("claude", p, "11111111-1111-1111-1111-111111111111", resume=r, lc_root=lc)
            for p in (driver.REQUESTS["finance"], driver.REPLY, driver.setup_prompt(),
                      driver.setup_prompt("123456")) for r in (False, True)]
    for cmd in cmds:
        assert isinstance(cmd, list) and all(isinstance(a, str) for a in cmd)
        assert cmd[:2] == ["claude", "-p"] and "global" not in " ".join(cmd).casefold()
        assert "--plugin-dir" in cmd and "--max-budget-usd" in cmd and "--output-format" in cmd
    assert "--resume" in cmds[1] and "--session-id" in cmds[0]


def test_setup_code_is_parsed_from_a_real_plan(ctx):
    plan = prompt(ctx, "/dynamic-harness:setup")["reason"]
    assert "global" in plan  # the plan offers it; the driver must not take it
    code = driver.parse_setup_code(json.dumps({"result": plan}))  # claude --output-format json escapes the text
    assert re.fullmatch(r"\d{6}", code) and f"confirm {code}\n" in plan + "\n"
    assert f"confirm {code} global" not in driver.setup_prompt(code)


@pytest.mark.parametrize("text", ["", "This folder is already set up.", "type: /dynamic-harness:setup confirm 123456 global"])
def test_text_without_a_folder_code_is_a_clear_error(text):
    with pytest.raises(ValueError, match="no one-time setup code"):
        driver.parse_setup_code(text)


def test_environment_is_inside_the_temp_dir_and_never_the_real_home(tmp_path):
    real = pwd.getpwuid(os.getuid()).pw_dir
    lc = tmp_path / "lc"
    env = driver.build_env(tmp_path, lc, {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "k", "AWS_SECRET": "x",
                                          "HOME": real, "CLAUDE_CONFIG_DIR": real + "/.claude"})
    assert env["ANTHROPIC_API_KEY"] == "k" and "AWS_SECRET" not in env and env["LOOP_CONTRACT_ROOT"] == str(lc)
    for k in ("HOME", "CLAUDE_CONFIG_DIR", "TMPDIR"):
        assert Path(env[k]).is_relative_to(tmp_path)
    assert real not in {v for k, v in env.items() if k not in ("PATH",)}
    assert not any(v.startswith(real + "/.claude") for v in env.values())


@pytest.mark.parametrize("pack", PACKS)
def test_workspace_holds_the_packs_sample_files(tmp_path, pack):
    ws = driver.make_workspace(tmp_path, pack)
    assert sorted(p.name for p in ws.iterdir()) == sorted(driver.FILES[pack])
    assert all((ws / n).read_text().strip() for n in driver.FILES[pack])
    assert all(n in driver.REQUESTS[pack] for n in driver.FILES[pack])


def test_data_dir_is_found_by_its_key_and_only_when_unique(tmp_path):
    with pytest.raises(AssertionError, match="no data folder"):
        driver.data_dir(tmp_path)
    for n in ("a", "config/plugins/data/x"):
        (tmp_path / n / "keys").mkdir(parents=True)
        (tmp_path / n / "keys" / "hmac.key").write_bytes(KEY)
    with pytest.raises(AssertionError, match="more than one"):
        driver.data_dir(tmp_path)
    (tmp_path / "a" / "keys" / "hmac.key").unlink()
    assert driver.data_dir(tmp_path) == tmp_path / "config/plugins/data/x"


def test_receipt_check_accepts_a_real_receipt_and_rejects_a_changed_one(h, env):
    _all_pass(h)
    _run(h)
    (env["data"] / "keys").mkdir()
    (env["data"] / "keys" / "hmac.key").write_bytes(KEY)
    assert driver.find_job(env["data"]) == "j1"
    ok = driver.verify_job(env["data"], env["ws"], "j1")
    assert ok["ok"] is True and ok["state"] == "Checked" and driver.passes(ok)
    f = h["p"].run / "receipt.json"
    rec = json.loads(f.read_text())
    rec["hmac"] = ("0" if rec["hmac"][0] != "0" else "1") + rec["hmac"][1:]  # one character
    f.write_text(json.dumps(rec))
    bad = driver.verify_job(env["data"], env["ws"], "j1")
    assert bad["ok"] is False and not driver.passes(bad)


@pytest.mark.parametrize("state,passes", [("Checked", True), ("Done, not fully checked: known mode", True),
                                          ("Partly done: 1 of 2", False)])
def test_only_checked_or_done_not_fully_checked_pass(state, passes):
    assert driver.passes({"ok": True, "state": state}) is passes
    assert not driver.passes({"ok": False, "reason": "no receipt"})


def test_the_loop_sends_the_request_then_replies_and_stops_at_the_limit(tmp_path, monkeypatch):
    sent = []

    def fake(cmd, cwd, env):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(driver, "verdict", lambda *a: {"ok": False, "reason": "no receipt"})
    v = driver.converse("claude", "finance", tmp_path, {}, tmp_path / "lc", run=fake)
    assert v["ok"] is False and len(sent) == driver.MAX_TURNS
    assert sent[0][2] == driver.REQUESTS["finance"] and all(c[2] == driver.REPLY for c in sent[1:])
    assert sent[0][sent[0].index("--session-id") + 1] == sent[1][sent[1].index("--resume") + 1]


def test_the_loop_stops_at_the_first_verifying_receipt(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(driver, "verdict", lambda *a: {"ok": True, "state": "Checked"} if len(sent) == 2 else None)
    v = driver.converse("claude", "finance", tmp_path, {}, tmp_path / "lc",
                        run=lambda cmd, cwd, env: sent.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert v["state"] == "Checked" and len(sent) == 2


def test_a_failing_claude_call_stops_the_loop_with_its_stderr(tmp_path):
    with pytest.raises(RuntimeError, match="exited 3.*not logged in"):
        driver.converse("claude", "finance", tmp_path, {}, tmp_path / "lc",
                        run=lambda cmd, cwd, env: subprocess.CompletedProcess(cmd, 3, "", "not logged in"))


def test_no_e2e_file_imports_outside_the_standard_library_pytest_and_harness_core():
    allowed = {"__future__", "json", "os", "re", "shutil", "subprocess", "sys", "uuid", "pathlib", "pytest",
               "harness_core", "driver"}  # driver is the directory's own helper module
    for f in (ROOT / "tests" / "e2e").glob("*.py"):
        for m in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", f.read_text(), re.M):
            assert m in allowed, f"{f.name} imports {m}"
