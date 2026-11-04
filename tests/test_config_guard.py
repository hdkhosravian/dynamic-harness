from __future__ import annotations

import json
import os

import pytest

from harness_core import config_guard
from harness_core.safety import Ctx


@pytest.fixture
def ctx(tmp_path):
    return Ctx(workspace=tmp_path, job="j1", settings_baseline={})


def change(tmp_path, settings, source="user_settings"):
    f = tmp_path / f"settings-{len(list(tmp_path.iterdir()))}.json"
    f.write_text(json.dumps(settings))
    return {"hook_event_name": "ConfigChange", "source": source, "file_path": str(f)}


def blocked(out):
    return out.get("decision") == "block"


def test_benign_change_allowed(ctx, tmp_path):
    assert config_guard.on_change(change(tmp_path, {"theme": "dark", "permissions": {"deny": ["Bash(curl:*)"]}}), ctx) == {}


@pytest.mark.parametrize(
    "settings",
    [
        {"disableAllHooks": True},
        {"enabledPlugins": {"dynamic-harness@local": False}},
        {"allowManagedHooksOnly": True},
        {"permissions": {"defaultMode": "bypassPermissions"}},
        {"sandbox": {"enabled": False}},
        {"sandbox": {"allowUnsandboxedCommands": True}},
    ],
)
def test_always_blocked(ctx, tmp_path, settings):
    ctx.job = None  # hooks/plugin/bypass are blocked even outside a task
    if "sandbox" in settings:
        ctx.job = "j1"
    assert blocked(config_guard.on_change(change(tmp_path, settings), ctx))


def test_other_plugin_disable_is_fine(ctx, tmp_path):
    assert config_guard.on_change(change(tmp_path, {"enabledPlugins": {"other@x": False}}), ctx) == {}


@pytest.mark.parametrize(
    "old, new",
    [
        ({"permissions": {"deny": ["Read(keys/**)"]}}, {"permissions": {"deny": []}}),
        ({"permissions": {"allow": []}}, {"permissions": {"allow": ["Bash(*)"]}}),
        ({"permissions": {"ask": ["Bash(git push:*)"]}}, {"permissions": {}}),
        ({"permissions": {"defaultMode": "default"}}, {"permissions": {"defaultMode": "acceptEdits"}}),
        ({"permissions": {"disableBypassPermissionsMode": "disable"}}, {"permissions": {}}),
        ({"permissions": {"additionalDirectories": []}}, {"permissions": {"additionalDirectories": ["/"]}}),
        ({"sandbox": {"network": {"allowedDomains": []}}}, {"sandbox": {"network": {"allowedDomains": ["evil.com"]}}}),
        ({"sandbox": {"excludedCommands": []}}, {"sandbox": {"excludedCommands": ["curl"]}}),
    ],
)
def test_loosening_during_task_blocked(ctx, tmp_path, old, new):
    evt = change(tmp_path, new)
    ctx.settings_baseline = {evt["file_path"]: old}
    assert blocked(config_guard.on_change(evt, ctx))


def test_tightening_allowed(ctx, tmp_path):
    evt = change(tmp_path, {"permissions": {"deny": ["Read(a)", "Read(b)"]}})
    ctx.settings_baseline = {evt["file_path"]: {"permissions": {"deny": ["Read(a)"]}}}
    assert config_guard.on_change(evt, ctx) == {}


def test_loosening_outside_task_allowed(ctx, tmp_path):
    ctx.job = None
    evt = change(tmp_path, {"permissions": {"allow": ["Bash(ls:*)"]}})
    ctx.settings_baseline = {evt["file_path"]: {"permissions": {"allow": []}}}
    assert config_guard.on_change(evt, ctx) == {}


def test_no_baseline_blocks(ctx, tmp_path):
    ctx.settings_baseline, ctx.job = None, None  # SessionStart never ran for this session
    out = config_guard.on_change(change(tmp_path, {"theme": "dark"}), ctx)
    assert blocked(out) and "baseline" in out["reason"]


def test_baseline_keyed_by_realpath(ctx, tmp_path):
    evt = change(tmp_path, {"permissions": {"deny": []}})
    ctx.settings_baseline = {os.path.realpath(evt["file_path"]): {"permissions": {"deny": ["Read(a)"]}}}
    assert blocked(config_guard.on_change(evt, ctx))


HARNESS = {"enabledPlugins": {"harness@local": True}}


@pytest.mark.parametrize(
    "old, new",
    [
        ({"permissions": {"deny": ["Read(keys/**)"]}}, {"permissions": {"deny": []}}),
        ({"sandbox": {"enabled": True}}, {"sandbox": {}}),
        ({"sandbox": {"filesystem": {"denyWrite": ["/d"]}}}, {"sandbox": {"filesystem": {"denyWrite": []}}}),
        ({"sandbox": {"filesystem": {"denyRead": ["/k"]}}}, {"sandbox": {}}),
        ({"sandbox": {"network": {"allowedDomains": []}}}, {"sandbox": {"network": {"allowedDomains": ["evil.com"]}}}),
        ({"sandbox": {"network": {"strictAllowlist": True}}}, {"sandbox": {"network": {"strictAllowlist": False}}}),
        ({"sandbox": {"failIfUnavailable": True}}, {"sandbox": {}}),
        ({}, {"sandbox": {"allowUnsandboxedCommands": True}}),
        (HARNESS, {"enabledPlugins": {}}),
    ],
)
def test_backstop_loosening_blocked_outside_a_task(ctx, tmp_path, old, new):
    ctx.job = None
    evt = change(tmp_path, new)
    ctx.settings_baseline = {evt["file_path"]: old}
    assert blocked(config_guard.on_change(evt, ctx))


def test_harness_plugin_kept_enabled_is_fine(ctx, tmp_path):
    ctx.job = None
    evt = change(tmp_path, {**HARNESS, "theme": "dark"})
    ctx.settings_baseline = {evt["file_path"]: HARNESS}
    assert config_guard.on_change(evt, ctx) == {}


def test_unreadable_settings_blocked(ctx, tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    assert blocked(config_guard.on_change({"source": "user_settings", "file_path": str(f)}, ctx))
    assert blocked(config_guard.on_change({"source": "user_settings", "file_path": str(tmp_path / "missing.json")}, ctx))


def test_inline_settings(ctx):
    assert blocked(config_guard.on_change({"source": "user_settings", "settings": {"disableAllHooks": True}}, ctx))
