"""ConfigChange guard (spec §Hooks ConfigChange row; §Security model "Hooks disabled").

Always blocks: ``disableAllHooks``, disabling this plugin, ``allowManagedHooksOnly``
outside managed policy, and ``bypassPermissions``. During a task (``ctx.job``)
it also blocks any loosening of permissions or the sandbox, judged against the
previous settings (``evt["previous_settings"]`` or ``ctx.settings_baseline[path]``)
and, with no baseline, against absolute rules. An unreadable file is blocked.
"""
from __future__ import annotations

import json
from pathlib import Path

MODE_RANK = {"plan": 0, "default": 1, "dontAsk": 1, "acceptEdits": 2, "bypassPermissions": 3}


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
        if on is False and (name.split("@")[0] in (ctx.plugin_id, "harness")):
            return f"disabling the {name} plugin is blocked"
    if new.get("allowManagedHooksOnly") is True and source != "policy_settings":
        return "allowManagedHooksOnly outside managed settings would disable the harness hooks"
    if _get(new, "permissions", "defaultMode") == "bypassPermissions":
        return "bypassPermissions is blocked"
    return None


def _loosening(old, new):
    if _get(new, "sandbox", "enabled") is False and _get(old, "sandbox", "enabled") is not False:
        return "sandbox disabled"
    if _get(new, "sandbox", "allowUnsandboxedCommands") is True and _get(old, "sandbox", "allowUnsandboxedCommands") is not True:
        return "allowUnsandboxedCommands enabled"
    for keys, what in (
        (("permissions", "deny"), "deny rule removed"),
        (("permissions", "ask"), "ask rule removed"),
    ):
        if _shrank(old, new, *keys):
            return what
    for keys, what in (
        (("permissions", "allow"), "allow rule added"),
        (("permissions", "additionalDirectories"), "additional directory added"),
        (("sandbox", "excludedCommands"), "sandbox excluded command added"),
        (("sandbox", "network", "allowedDomains"), "sandbox network domain added"),
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
    if ctx.job:
        old = evt.get("previous_settings")
        if not isinstance(old, dict):
            old = (ctx.settings_baseline or {}).get(path) or {}
        why = _loosening(old, new)
        if why:
            return {"decision": "block", "reason": f"loosening during task {ctx.job}: {why}"}
    return {}
