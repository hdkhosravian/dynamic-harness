"""/dynamic-harness:setup (P5): two-step consent in the UserPromptSubmit hook; the model is never involved."""
from __future__ import annotations

import json
import os
import re
import stat
import time

import pytest

from harness_core import hookmain, setup
from harness_core.safety import Ctx

SID = "s1"


@pytest.fixture
def ctx(tmp_path):
    home, ws, data, plugin, lc = (tmp_path / n for n in ("home", "ws", "data", "plugin", "lc"))
    for d in (home / ".claude", ws, data, plugin / ".claude-plugin", lc / ".claude-plugin"):
        d.mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "dynamic-harness"}')
    (lc / ".claude-plugin" / "plugin.json").write_text('{"name": "loop-contract", "version": "1.1.0"}')
    return Ctx(workspace=ws, data_root=data, plugin_root=plugin, home=home, loop_contract_root=lc)


def files(ctx):
    return setup.settings_paths(ctx)


def load(p):
    return json.loads(p.read_text()) if p.exists() else None


def prompt(ctx, text, sid=SID):
    return setup.on_prompt({"prompt": text, "session_id": sid}, ctx)


def code_of(out):
    m = re.search(r"/dynamic-harness:setup confirm (\d{6})", out["reason"])
    assert m, out["reason"]
    return m.group(1)


def apply(ctx):
    out = prompt(ctx, "/dynamic-harness:setup")
    return prompt(ctx, f"/dynamic-harness:setup confirm {code_of(out)}")


# --- plan: shows, never writes ---


def test_plan_lists_every_change_and_writes_nothing(ctx):
    out = prompt(ctx, "/dynamic-harness:setup")
    assert out["decision"] == "block"
    r = out["reason"]
    for needle in ("sandbox.enabled", "strictAllowlist", "denyRead", "denyWrite", "disableBypassPermissionsMode",
                   "allowUnsandboxedCommands", "failIfUnavailable", "permissions.deny"):
        assert needle in r, needle
    assert all(not p.exists() for p in files(ctx).values())
    assert not (ctx.data_root / "config_log.jsonl").exists() and not (ctx.data_root / "keys").exists()


def test_plan_says_what_it_changes_for_other_projects(ctx):
    # the one user-scope key reaches every project that turns the sandbox on
    assert "every project" in prompt(ctx, "/dynamic-harness:setup")["reason"]


# --- confirm ---


def test_confirm_applies_the_required_settings(ctx):
    out = apply(ctx)
    assert out["decision"] == "block" and "Applied" in out["reason"]
    user, local = load(files(ctx)["user"]), load(files(ctx)["local"])
    assert user["sandbox"]["network"]["strictAllowlist"] is True
    sb = local["sandbox"]
    assert sb["enabled"] is True and sb["failIfUnavailable"] is True and sb["allowUnsandboxedCommands"] is False
    assert sb["excludedCommands"] == [] and sb["network"]["allowedDomains"] == []
    data = os.path.realpath(ctx.data_root)
    assert data in sb["filesystem"]["denyRead"] and data in sb["filesystem"]["denyWrite"]
    assert f"Read(/{data}/keys/**)" in local["permissions"]["deny"]
    assert local["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert setup.verify(ctx) == []


def test_confirm_creates_the_hmac_key_once_and_private(ctx):
    apply(ctx)
    k = ctx.data_root / "keys" / "hmac.key"
    first = k.read_bytes()
    assert len(first) == 32 and stat.S_IMODE(k.stat().st_mode) == 0o600
    assert stat.S_IMODE((ctx.data_root / "keys").stat().st_mode) == 0o700
    (ctx.data_root / "config_log.jsonl").unlink()  # force a re-run that has something to do
    apply(ctx)
    assert k.read_bytes() == first  # never rotated by a re-run


def test_confirm_records_the_release_in_config_log(ctx):
    from harness_core import session
    apply(ctx)
    rows = [json.loads(x) for x in (ctx.data_root / "config_log.jsonl").read_text().splitlines()]
    assert rows[-1]["loop_contract_root"] == str(ctx.loop_contract_root)
    assert rows[-1]["hashes"] == session.current_hashes(ctx)


def test_existing_settings_are_kept(ctx):
    u = files(ctx)["user"]
    u.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(~/.ssh/**)"]},
                             "sandbox": {"network": {"allowedDomains": ["pypi.org"]}}}))
    apply(ctx)
    got = load(u)
    assert got["model"] == "opus" and got["permissions"]["allow"] == ["Bash(ls)"]
    assert got["permissions"]["deny"] == ["Read(~/.ssh/**)"]
    assert got["sandbox"]["network"]["allowedDomains"] == []  # merged across scopes: must be empty everywhere


def test_wrong_code_changes_nothing(ctx):
    prompt(ctx, "/dynamic-harness:setup")
    out = prompt(ctx, "/dynamic-harness:setup confirm 000000")
    assert "does not match" in out["reason"] and not files(ctx)["local"].exists()


def test_a_code_works_once(ctx):
    out = prompt(ctx, "/dynamic-harness:setup")
    c = code_of(out)
    prompt(ctx, f"/dynamic-harness:setup confirm {c}")
    assert "no pending" in prompt(ctx, f"/dynamic-harness:setup confirm {c}")["reason"].lower()


def test_confirm_from_another_session_is_refused(ctx):
    c = code_of(prompt(ctx, "/dynamic-harness:setup"))
    out = prompt(ctx, f"/dynamic-harness:setup confirm {c}", sid="other")
    assert "another session" in out["reason"] and not files(ctx)["local"].exists()


def test_expired_plan_is_refused(ctx, monkeypatch):
    c = code_of(prompt(ctx, "/dynamic-harness:setup"))
    real = time.time
    monkeypatch.setattr(setup.time, "time", lambda: real() + setup.PENDING_TTL + 1)
    assert "expired" in prompt(ctx, f"/dynamic-harness:setup confirm {c}")["reason"]
    assert not files(ctx)["local"].exists()


def test_settings_edited_after_the_plan_are_not_overwritten(ctx):
    c = code_of(prompt(ctx, "/dynamic-harness:setup"))
    files(ctx)["user"].write_text('{"model": "sonnet"}')
    out = prompt(ctx, f"/dynamic-harness:setup confirm {c}")
    assert "changed since" in out["reason"] and load(files(ctx)["user"]) == {"model": "sonnet"}


def test_invalid_json_is_never_overwritten(ctx):
    files(ctx)["user"].write_text("{broken")
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "not valid JSON" in out["reason"] and files(ctx)["user"].read_text() == "{broken"


def test_symlinked_user_settings_are_written_through(ctx, tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{"model": "opus"}')
    files(ctx)["user"].symlink_to(real)
    apply(ctx)
    assert files(ctx)["user"].is_symlink() and load(real)["sandbox"]["network"]["strictAllowlist"] is True


def test_rerun_with_everything_in_place_changes_no_settings(ctx):
    apply(ctx)
    before = {k: p.read_bytes() for k, p in files(ctx).items() if p.exists()}
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "already" in out["reason"].lower()
    assert {k: p.read_bytes() for k, p in files(ctx).items() if p.exists()} == before


def test_no_loop_contract_blocks_setup(ctx):
    object.__setattr__(ctx, "loop_contract_root", None)
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "loop-contract" in out["reason"] and "confirm" not in out["reason"]


# --- undo ---


def test_undo_restores_the_previous_files(ctx):
    files(ctx)["user"].write_text('{"model": "opus"}')
    apply(ctx)
    c = code_of(prompt(ctx, "/dynamic-harness:setup undo"))
    out = prompt(ctx, f"/dynamic-harness:setup confirm {c}")
    assert "Restored" in out["reason"]
    assert load(files(ctx)["user"]) == {"model": "opus"} and not files(ctx)["local"].exists()


def test_undo_without_a_backup_says_so(ctx):
    assert "nothing to undo" in prompt(ctx, "/dynamic-harness:setup undo")["reason"].lower()


# --- verify (SessionStart reads it) ---


def test_verify_lists_what_is_missing(ctx):
    probs = setup.verify(ctx)
    assert any("sandbox.enabled" in p for p in probs) and any("strictAllowlist" in p for p in probs)


def test_local_settings_win_over_a_project_file_that_turns_the_sandbox_off(ctx):
    apply(ctx)
    files(ctx)["project"].write_text('{"sandbox": {"enabled": false}}')
    assert setup.verify(ctx) == []  # precedence: local > project > user


def test_excluded_commands_in_any_scope_fail_verify(ctx):
    apply(ctx)
    files(ctx)["project"].write_text('{"sandbox": {"excludedCommands": ["docker"]}}')
    assert any("excludedCommands" in p for p in setup.verify(ctx))


def test_strict_allowlist_only_counts_from_user_scope(ctx):
    apply(ctx)
    u = load(files(ctx)["user"])
    del u["sandbox"]["network"]["strictAllowlist"]
    files(ctx)["user"].write_text(json.dumps(u))
    loc = load(files(ctx)["local"])
    loc["sandbox"]["network"]["strictAllowlist"] = True  # ignored by Claude Code from project scopes
    files(ctx)["local"].write_text(json.dumps(loc))
    assert any("strictAllowlist" in p for p in setup.verify(ctx))


def test_status_reports_verify(ctx):
    assert "not set up" in prompt(ctx, "/dynamic-harness:setup status")["reason"].lower()
    apply(ctx)
    assert "set up" in prompt(ctx, "/dynamic-harness:setup status")["reason"].lower()


def test_unknown_arguments_show_usage_and_change_nothing(ctx):
    out = prompt(ctx, "/dynamic-harness:setup --force")
    assert "usage" in out["reason"].lower() and not files(ctx)["local"].exists()


# --- hook wiring ---


def test_other_prompts_pass_through_without_any_state(tmp_path):
    # no env at all: a normal prompt must never be blocked by the harness
    assert hookmain.main("UserPromptSubmit", {"prompt": "hello", "session_id": SID}, {}) == (0, None)
    assert hookmain.main("UserPromptSubmit", {"prompt": "/dynamic-harness:setupx"}, {}) == (0, None)


def test_setup_prompt_is_blocked_with_the_plan_on_stdout(ctx):
    env = {"CLAUDE_PLUGIN_DATA": str(ctx.data_root), "CLAUDE_PLUGIN_ROOT": str(ctx.plugin_root),
           "CLAUDE_PROJECT_DIR": str(ctx.workspace), "HOME": str(ctx.home),
           "LOOP_CONTRACT_ROOT": str(ctx.loop_contract_root)}
    code, out = hookmain.main("UserPromptSubmit", {"prompt": "/dynamic-harness:setup", "session_id": SID}, env)
    assert code == 0 and out["decision"] == "block" and "confirm" in out["reason"]


def test_task_notification_is_never_a_setup_prompt():
    assert not setup.is_setup_prompt({"prompt": "<task-notification>/dynamic-harness:setup confirm 123456"})


def test_hooks_json_wires_user_prompt_submit(env):
    cfg = json.loads((env["root"] / "hooks" / "hooks.json").read_text())
    (h,) = [h for g in cfg["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert h["command"].endswith('harness-hook" UserPromptSubmit') and h["onFailure"] == "block"


def test_setup_command_cannot_be_invoked_by_the_model(env):
    text = (env["root"] / "commands" / "setup.md").read_text()
    assert re.search(r"^disable-model-invocation:\s*true\s*$", text, re.M)


# --- review of 22c961d ---


@pytest.mark.parametrize("scope", ["project", "local"])
def test_symlinked_workspace_settings_are_refused(ctx, tmp_path, scope):
    # a cloned repo can plant .claude/settings.local.json -> ~/.bashrc; setup must not write JSON into it
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    files(ctx)[scope].parent.mkdir(exist_ok=True)
    files(ctx)[scope].symlink_to(victim)
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "symlink" in out["reason"] and "confirm" not in out["reason"] and victim.read_text() == "keep me"


def test_symlinked_workspace_claude_dir_is_refused(ctx, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (ctx.workspace / ".claude").symlink_to(tmp_path / "elsewhere")
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "symlink" in out["reason"] and not list((tmp_path / "elsewhere").iterdir())


def test_changed_release_hashes_are_named_as_a_warning(ctx):
    apply(ctx)
    (ctx.plugin_root / "planted.py").write_text("x = 1\n")
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "changed since the last setup: plugin" in out["reason"] and "did not" in out["reason"]


@pytest.mark.parametrize("key, value", [
    (("sandbox", "enableWeakerNestedSandbox"), True),
    (("sandbox", "allowNestedUserNamespaces"), True),
    (("sandbox", "network", "allowAllUnixSockets"), True),
    (("sandbox", "network", "allowUnixSockets"), ["/var/run/docker.sock"]),
    (("sandbox", "filesystem", "disabled"), True),
])
def test_sandbox_weakening_keys_fail_verify_and_setup_removes_them(ctx, key, value):
    apply(ctx)
    u = load(files(ctx)["user"])
    setup._put(u, key, value)
    files(ctx)["user"].write_text(json.dumps(u))
    assert any(".".join(key) in p for p in setup.verify(ctx))
    apply(ctx)
    assert setup.verify(ctx) == []


# --- review of 4c6fac6..e99d9d2 ---


def test_undo_keeps_edits_made_after_setup(ctx):
    files(ctx)["user"].write_text('{"theme": "dark"}')
    apply(ctx)
    u = load(files(ctx)["user"])
    u["enabledPlugins"] = {"x@y": True}
    files(ctx)["user"].write_text(json.dumps(u))
    loc = load(files(ctx)["local"])
    loc["permissions"]["allow"] = ["Bash(ls)"]  # e.g. an "always allow" Claude Code wrote later
    files(ctx)["local"].write_text(json.dumps(loc))
    prompt(ctx, f"/dynamic-harness:setup confirm {code_of(prompt(ctx, '/dynamic-harness:setup undo'))}")
    assert load(files(ctx)["user"]) == {"theme": "dark", "enabledPlugins": {"x@y": True}}
    assert load(files(ctx)["local"]) == {"permissions": {"allow": ["Bash(ls)"]}}


def test_undo_leaves_a_value_changed_after_setup_and_says_so(ctx):
    apply(ctx)
    loc = load(files(ctx)["local"])
    loc["sandbox"]["failIfUnavailable"] = False
    files(ctx)["local"].write_text(json.dumps(loc))
    out = prompt(ctx, "/dynamic-harness:setup undo")
    assert "changed since setup" in out["reason"]
    prompt(ctx, f"/dynamic-harness:setup confirm {code_of(out)}")
    assert load(files(ctx)["local"]) == {"sandbox": {"failIfUnavailable": False}}


@pytest.mark.parametrize("key, value", [
    (("sandbox", "filesystem", "allowRead"), "{data}"),
    (("sandbox", "filesystem", "allowRead"), "{data}/keys"),
    (("sandbox", "filesystem", "allowWrite"), "~/.claude"),
    (("sandbox", "filesystem", "allowWrite"), "{ws}/CLAUDE.md"),
])
def test_allow_entries_over_protected_paths_fail_verify_and_are_removed(ctx, key, value):
    apply(ctx)
    loc = load(files(ctx)["local"])
    entry = value.format(data=os.path.realpath(ctx.data_root), ws=os.path.realpath(ctx.workspace))
    setup._put(loc, key, [entry, "/opt/fine"])
    files(ctx)["local"].write_text(json.dumps(loc))
    assert any(".".join(key) in p for p in setup.verify(ctx))
    apply(ctx)
    assert setup._get(load(files(ctx)["local"]), key) == ["/opt/fine"] and setup.verify(ctx) == []


@pytest.mark.parametrize("key", [("sandbox", "network", "httpProxyPort"), ("sandbox", "network", "socksProxyPort")])
def test_a_custom_proxy_port_fails_verify_and_is_removed(ctx, key):
    apply(ctx)
    u = load(files(ctx)["user"])
    setup._put(u, key, 8080)
    files(ctx)["user"].write_text(json.dumps(u))
    assert any(".".join(key) in p for p in setup.verify(ctx))
    apply(ctx)
    assert setup._get(load(files(ctx)["user"]), key) is None and setup.verify(ctx) == []


def test_lookalike_values_do_not_count(ctx):
    apply(ctx)
    loc = load(files(ctx)["local"])
    loc["sandbox"]["enabled"] = 1  # 1 == True in Python, not in Claude Code
    files(ctx)["local"].write_text(json.dumps(loc))
    assert any("sandbox.enabled" in p for p in setup.verify(ctx))
    apply(ctx)
    assert load(files(ctx)["local"])["sandbox"]["enabled"] is True


@pytest.mark.parametrize("scope, key, value", [
    ("project", ("sandbox", "excludedCommands"), True),
    ("project", ("permissions", "deny"), 5),
    ("local", ("sandbox", "filesystem", "denyRead"), "/secret"),
    ("user", ("sandbox",), True),
])
def test_odd_value_types_are_reported_never_crash_or_rewrite(ctx, scope, key, value):
    d = {}
    setup._put(d, key, value)
    files(ctx)[scope].parent.mkdir(exist_ok=True)
    files(ctx)[scope].write_text(json.dumps(d))
    probs = setup.verify(ctx)
    assert any("must be" in p for p in probs), probs
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "cannot continue" in out["reason"] and load(files(ctx)[scope]) == d


def test_managed_settings_that_override_are_named_and_block(ctx, monkeypatch):
    monkeypatch.setattr(setup, "_managed", lambda: {"sandbox": {"enabled": False}})
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "managed" in out["reason"] and "sandbox.enabled" in out["reason"] and "confirm" not in out["reason"]


def test_symlinked_loop_contract_path_is_accepted(ctx, tmp_path):
    link = tmp_path / "lc-link"
    link.symlink_to(ctx.loop_contract_root)
    object.__setattr__(ctx, "loop_contract_root", link)
    apply(ctx)
    rows = [json.loads(x) for x in (ctx.data_root / "config_log.jsonl").read_text().splitlines()]
    assert rows[-1]["loop_contract_root"] == os.path.realpath(link)


def test_dangling_user_settings_symlink_is_refused(ctx, tmp_path):
    files(ctx)["user"].symlink_to(tmp_path / "missing.json")
    assert "missing" in prompt(ctx, "/dynamic-harness:setup")["reason"]
    assert not (tmp_path / "missing.json").exists()


def test_any_whitespace_after_the_command_counts():
    assert setup.is_setup_prompt({"prompt": "/dynamic-harness:setup\nconfirm 123456"})


def test_emptying_user_wide_lists_is_said_plainly(ctx):
    files(ctx)["user"].write_text(json.dumps({"sandbox": {"network": {"allowedDomains": ["github.com"]}}}))
    out = prompt(ctx, "/dynamic-harness:setup")
    assert "github.com" in out["reason"] and "other projects" in out["reason"]


@pytest.mark.parametrize("entry", ["./x", "x", "//{data}", "~/.claude/plugins/data/*", "{link}", "{data_upper}"])
def test_ambiguous_or_aliased_allow_entries_count_as_overlapping(ctx, tmp_path, entry):
    (tmp_path / "alias").symlink_to(ctx.data_root)
    data = os.path.realpath(ctx.data_root)
    e = entry.format(data=data.lstrip("/"), link=str(tmp_path / "alias"), data_upper=data.upper())
    if entry == "{data_upper}" and not os.path.exists(e):
        pytest.skip("case-sensitive file system")
    assert setup._inside(e, data, setup._home(ctx))


def test_a_plain_unrelated_allow_entry_is_kept(ctx):
    assert not setup._inside("/opt/fine", os.path.realpath(ctx.data_root), setup._home(ctx))
