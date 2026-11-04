"""ConfigChange guard (spec §Hooks ConfigChange row; §Security model "Hooks disabled").

The changed file is judged against the session baseline that SessionStart
snapshotted (``sessions/<sid>/settings_baseline.json`` -> ``ctx.settings_baseline``,
keyed by realpath). No baseline blocks every change.

Always blocks: ``disableAllHooks``, disabling or dropping this plugin,
``allowManagedHooksOnly`` outside managed policy, ``bypassPermissions``, and any
loosening of the safety backstop (deny rules, sandbox enabled / denyWrite /
denyRead / allowedDomains / strictAllowlist / failIfUnavailable /
allowUnsandboxedCommands). During a task (``ctx.job``) it also blocks any other
loosening of permissions or the sandbox. An unreadable file is blocked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MODE_RANK = {"plan": 0, "default": 1, "dontAsk": 1, "acceptEdits": 2, "bypassPermissions": 3}


def settings_files(ctx) -> list:
    """The settings files a session baseline covers: user, project and project-local."""
    home = Path(ctx.home) if ctx.home else Path(os.path.expanduser("~"))
    ws = Path(ctx.workspace)
    return [home / ".claude" / "settings.json", ws / ".claude" / "settings.json", ws / ".claude" / "settings.local.json"]


def snapshot(ctx) -> dict:
    """``{realpath: settings}`` at SessionStart; an absent or unreadable file is ``{}`` (judged from nothing)."""
    out = {}
    for f in settings_files(ctx):
        try:
            v = json.loads(f.read_text("utf-8"))  # user dotfiles may be symlinks: plain read is intended
        except (OSError, ValueError):
            v = {}
        out[os.path.realpath(f)] = v if isinstance(v, dict) else {}
    return out


def _get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def _grew(old, new, *keys):
    return bool(set(_get(new, *keys, default=[])) - set(_get(old, *keys, default=[])))


def _shrank(old, new, *keys):
    return bool(set(_get(old, *keys, default=[])) - set(_get(new, *keys, default=[])))


def _always(new, source, ctx):
    if new.get("disableAllHooks") is True:
        return "disableAllHooks is blocked: harness hooks must stay on"
    for name, on in (_get(new, "enabledPlugins", default={}) or {}).items():
        if on is False and _ours(name, ctx):
            return f"disabling the {name} plugin is blocked"
    if new.get("allowManagedHooksOnly") is True and source != "policy_settings":
        return "allowManagedHooksOnly outside managed settings would disable the harness hooks"
    if _get(new, "permissions", "defaultMode") == "bypassPermissions":
        return "bypassPermissions is blocked"
    return None


def _on(d, *keys):
    return _get(d, *keys) is True


def _ours(name, ctx):
    return name.split("@")[0] in (ctx.plugin_id, "harness")


def _backstop(old, new, ctx):
    """Loosening of the hard backstop: blocked whether or not a task is open."""
    if _on(old, "sandbox", "enabled") and not _on(new, "sandbox", "enabled") or \
            _get(new, "sandbox", "enabled") is False and _get(old, "sandbox", "enabled") is not False:
        return "sandbox disabled"
    if _on(new, "sandbox", "allowUnsandboxedCommands") and not _on(old, "sandbox", "allowUnsandboxedCommands"):
        return "allowUnsandboxedCommands enabled"
    for keys, what in (
        (("sandbox", "network", "strictAllowlist"), "sandbox strictAllowlist turned off"),
        (("sandbox", "failIfUnavailable"), "sandbox failIfUnavailable turned off"),
    ):
        if _on(old, *keys) and not _on(new, *keys):
            return what
    for keys, what in (
        (("permissions", "deny"), "deny rule removed"),
        (("sandbox", "filesystem", "denyWrite"), "sandbox denyWrite entry removed"),
        (("sandbox", "filesystem", "denyRead"), "sandbox denyRead entry removed"),
    ):
        if _shrank(old, new, *keys):
            return what
    if _grew(old, new, "sandbox", "network", "allowedDomains"):
        return "sandbox network domain added"
    was = {k for k, v in (_get(old, "enabledPlugins", default={}) or {}).items() if v is True and _ours(k, ctx)}
    now = {k for k, v in (_get(new, "enabledPlugins", default={}) or {}).items() if v is True}
    if was - now:
        return f"the {sorted(was - now)[0]} plugin dropped from enabledPlugins"
    return None


def _loosening(old, new):
    if _shrank(old, new, "permissions", "ask"):
        return "ask rule removed"
    for keys, what in (
        (("permissions", "allow"), "allow rule added"),
        (("permissions", "additionalDirectories"), "additional directory added"),
        (("sandbox", "excludedCommands"), "sandbox excluded command added"),
        (("sandbox", "network", "allowUnixSockets"), "sandbox unix socket added"),
    ):
        if _grew(old, new, *keys):
            return what
    om = MODE_RANK.get(_get(old, "permissions", "defaultMode", default="default"), 1)
    nm = MODE_RANK.get(_get(new, "permissions", "defaultMode", default="default"), 3)
    if nm > om:
        return "permission mode loosened"
    if _get(old, "permissions", "disableBypassPermissionsMode") == "disable" and \
            _get(new, "permissions", "disableBypassPermissionsMode") != "disable":
        return "disableBypassPermissionsMode removed"
    if _get(new, "sandbox", "network", "allowLocalBinding") is True and _get(old, "sandbox", "network", "allowLocalBinding") is not True:
        return "sandbox local binding enabled"
    return None


def on_change(evt, ctx) -> dict:
    evt = evt if isinstance(evt, dict) else {}
    source = evt.get("source")
    path = evt.get("file_path")
    try:
        new = evt["settings"] if isinstance(evt.get("settings"), dict) else json.loads(Path(path).read_text("utf-8"))
        if not isinstance(new, dict):
            raise ValueError("settings are not an object")
    except Exception as e:
        return {"decision": "block", "reason": f"cannot read changed settings ({type(e).__name__}): blocked"}
    why = _always(new, source, ctx)
    if why:
        return {"decision": "block", "reason": why}
    base = ctx.settings_baseline
    if not isinstance(base, dict):
        return {"decision": "block", "reason": "no settings baseline for this session (SessionStart did not run): blocked"}
    old = base.get(os.path.realpath(path), base.get(path)) if isinstance(path, str) else None
    old = old if isinstance(old, dict) else {}  # a file the baseline never saw is judged from nothing
    why = _backstop(old, new, ctx)
    if why:
        return {"decision": "block", "reason": f"loosening the safety backstop: {why}"}
    if ctx.job:
        why = _loosening(old, new)
        if why:
            return {"decision": "block", "reason": f"loosening during task {ctx.job}: {why}"}
    return {}
