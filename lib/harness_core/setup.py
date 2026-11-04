"""/dynamic-harness:setup (spec §Security model "Setup", build step P5).

A plugin cannot ship permissions or a sandbox, so setup writes them into the user's settings, with
consent. It runs entirely inside the UserPromptSubmit hook, outside the Claude Code sandbox, and the
model never sees it: Claude Code passes the typed line to the hook before expanding the command, and
a ``block`` decision shows the reason to the user, is not added to context, and stops the prompt
(docs; spike 2026-11-04). Two steps:

1. ``/dynamic-harness:setup`` shows every change and a one-time 6-digit code. Nothing is written.
2. ``/dynamic-harness:setup confirm <code>`` applies exactly that plan, if the code matches, is under
   10 minutes old, comes from the same session, and the settings files have not changed since.

``undo`` restores the files as they were before the last setup (same two steps); ``status`` runs
``verify``. ``verify`` is also what SessionStart runs: a session whose effective settings miss
anything here is refused above T0.

Scopes (docs, code.claude.com settings and sandboxing): ``sandbox.network.strictAllowlist`` is
honoured only from user, managed or ``--settings`` scope, so it goes into ``~/.claude/settings.json``.
It only acts where the sandbox is on, so other projects without a sandbox are unaffected. Everything
else goes into this workspace's ``.claude/settings.local.json``, so other projects keep their
settings: local beats project and user for single values, permission rules and sandbox path lists
merge across scopes, and ``disableBypassPermissionsMode`` takes the strictest value from any scope.
Lists that must be empty (``excludedCommands``, ``allowedDomains``) merge too, so entries in any
scope are removed, and the plan says so.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import fcntl
import fnmatch
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

from .safeio import atomic_write

PREFIX = "/dynamic-harness:setup"
PENDING_TTL = 600
USAGE = (f"Usage: {PREFIX} (show the plan) | {PREFIX} confirm <code> | {PREFIX} undo | {PREFIX} status")
MANAGED = {"darwin": "/Library/Application Support/ClaudeCode/managed-settings.json",
           "linux": "/etc/claude-code/managed-settings.json"}


class Blocked(Exception):
    """Setup cannot make a plan; the message tells the user what to fix."""


def is_setup_prompt(evt) -> bool:
    p = evt.get("prompt") if isinstance(evt, dict) else None
    return isinstance(p, str) and re.fullmatch(re.escape(PREFIX) + r"(\s.*)?", p.strip(), re.S) is not None


def settings_paths(ctx) -> dict:
    home = Path(ctx.home) if ctx.home else Path(os.path.expanduser("~"))
    ws = Path(ctx.workspace)
    return {"user": home / ".claude" / "settings.json", "project": ws / ".claude" / "settings.json",
            "local": ws / ".claude" / "settings.local.json"}


def _home(ctx) -> str:
    return os.path.realpath(ctx.home) if ctx.home else os.path.realpath(os.path.expanduser("~"))


def required(ctx, lcr) -> dict:
    """The one list of what setup writes and verify checks."""
    home, ws = _home(ctx), os.path.realpath(ctx.workspace)
    d, p, lc = (os.path.realpath(x) for x in (ctx.data_root, ctx.plugin_root, lcr))
    plugins = os.path.join(home, ".claude", "plugins", "data")
    deny_read = ("sandbox", "filesystem", "denyRead")
    deny_write = ("sandbox", "filesystem", "denyWrite")
    return {
        # scope -> {key: value}; compared by type and value (Claude Code does not read 1 as true)
        "set": {
            "user": {("sandbox", "network", "strictAllowlist"): True},
            "local": {("sandbox", "enabled"): True, ("sandbox", "failIfUnavailable"): True,
                      ("sandbox", "allowUnsandboxedCommands"): False,
                      ("permissions", "disableBypassPermissionsMode"): "disable"},
        },
        # merged across scopes: each entry must be present somewhere; setup adds missing ones to local
        "include": {
            ("permissions", "deny"): [f"Read(/{d}/keys/**)", f"Read(/{d}/experience/**)",
                                      f"Edit(/{d}/**)", f"Edit(/{p}/**)", f"Edit(/{lc}/**)"],
            # the whole data root (hook state and keys) and every installed plugin's secrets
            deny_read: [d, os.path.join(plugins, "*", "keys"), os.path.join(plugins, "*", "experience")],
            # Claude Code already protects .claude settings/skills/agents/commands/hooks, .mcp.json and
            # .git hooks/config in the working directory; this adds the rest of the trust boundary
            deny_write: [os.path.join(home, ".claude"), os.path.join(home, ".claude.json"), d, p, lc,
                         os.path.join(ws, "CLAUDE.md"), os.path.join(ws, "CLAUDE.local.md"),
                         os.path.join(ws, "**", "CLAUDE.md"), os.path.join(ws, "**", "CLAUDE.local.md")],
        },
        # merged across scopes: must be empty in every scope (local states it explicitly)
        "empty": [("sandbox", "excludedCommands"), ("sandbox", "network", "allowedDomains"),
                  ("sandbox", "network", "allowUnixSockets")],  # a docker.sock allowance is an escape
        # must not be switched on in any scope: each one weakens the sandbox
        "forbid": [("sandbox", "enableWeakerNestedSandbox"), ("sandbox", "allowNestedUserNamespaces"),
                   ("sandbox", "network", "allowAllUnixSockets"), ("sandbox", "filesystem", "disabled")],
        # must be absent in every scope: a proxy of one's own need not enforce the allow-list
        "delete": [("sandbox", "network", "httpProxyPort"), ("sandbox", "network", "socksProxyPort")],
        # allow entries may not sit on or inside a protected path (a narrower allow could win)
        "no_overlap": {("sandbox", "filesystem", "allowRead"): deny_read,
                       ("sandbox", "filesystem", "allowWrite"): deny_write},
    }


EXPLAIN = {
    ("sandbox", "network", "strictAllowlist"): "Allow no network access beyond each project's own list",
    ("sandbox", "enabled"): "Run every command Claude starts inside the operating system's sandbox",
    ("sandbox", "failIfUnavailable"): "If the sandbox cannot start, run nothing rather than run unprotected",
    ("sandbox", "allowUnsandboxedCommands"): "Claude cannot ask to run a command outside the sandbox",
    ("permissions", "disableBypassPermissionsMode"): "Turn off the mode that skips every permission check",
    ("permissions", "deny"): "Claude's file tools cannot read the harness keys or change harness, plugin and "
                             "loop-contract files",
    ("sandbox", "filesystem", "denyRead"): "Commands cannot read the harness's private data or any plugin's keys",
    ("sandbox", "filesystem", "denyWrite"): "Commands cannot change Claude's settings, the harness, plugins, "
                                            "loop-contract or any CLAUDE.md instruction file",
    ("sandbox", "excludedCommands"): "No command is exempt from the sandbox",
    ("sandbox", "network", "allowedDomains"): "Commands get no network access (the harness does any web lookups)",
    ("sandbox", "network", "allowUnixSockets"): "Commands cannot connect to local service sockets, such as Docker's",
    ("sandbox", "filesystem", "allowRead"): "No read exception may reach the harness's private data or keys",
    ("sandbox", "filesystem", "allowWrite"): "No write exception may reach Claude's settings, the harness or "
                                             "CLAUDE.md files",
}


def _dot(key) -> str:
    return ".".join(key)


def _same(a, b) -> bool:
    return type(a) is type(b) and a == b


def _differs(a, b) -> bool:
    """Compared as JSON: Python treats ``{"x": 1}`` and ``{"x": true}`` as equal, Claude Code does not."""
    return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def _has(d, key) -> bool:
    for k in key:
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return True


def _get(d, key):
    for k in key:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _put(d, key, value):
    for k in key[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[key[-1]] = value


def _existing_depth(d, key) -> int:
    """How many containers along ``key`` (excluding the leaf) already exist as objects."""
    i = 0
    while i < len(key) - 1 and isinstance(d, dict) and isinstance(d.get(key[i]), dict):
        d = d[key[i]]
        i += 1
    return i


def _delete(d, key, keep):
    """Remove ``key``, then any now-empty container setup created (those deeper than ``keep`` levels)."""
    chain = [d]
    for k in key[:-1]:
        chain.append(chain[-1][k])
    chain[-1].pop(key[-1], None)
    for j in range(len(key) - 1, keep, -1):
        if chain[j] == {}:
            chain[j - 1].pop(key[j - 1], None)


def _value(d, key, label):
    """The value at ``key``; Blocked when a container on the way is not an object."""
    cur = d
    for i, k in enumerate(key):
        if not isinstance(cur, dict):
            raise Blocked(f"{label}: {_dot(key[:i])} must be an object (is {json.dumps(cur)}); fix it first")
        if k not in cur:
            return None
        cur = cur[k]
    return cur


def _list(d, key, label) -> list:
    v = _value(d, key, label)
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Blocked(f"{label}: {_dot(key)} must be a list of strings (is {json.dumps(v)}); fix it first")
    return v


def _inside(entry, protected, home) -> bool:
    """``entry`` is on or inside ``protected`` (a path, or a pattern with * / **)."""
    e = os.path.normpath(os.path.join(home, entry[2:]) if entry.startswith("~/") else home if entry == "~" else entry)
    if any(c in protected for c in "*?["):
        pat = protected.replace("**", "*")
        return fnmatch.fnmatchcase(e, pat) or fnmatch.fnmatchcase(e, pat + "/*")
    p = protected.rstrip("/")
    return e == p or e.startswith(p + "/")


def _read(path: Path):
    """``(dict, raw bytes or None)``; a missing file is ``{}``. Invalid JSON raises Blocked: never overwritten."""
    if os.path.islink(path) and not os.path.exists(path):
        raise Blocked(f"{path} is a symlink to a missing file; fix the link first")
    try:
        raw = path.read_bytes()  # the user's own dotfile may be a symlink: read through it
    except FileNotFoundError:
        return {}, None
    try:
        v = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except ValueError as e:
        raise Blocked(f"{path} is not valid JSON ({e}); fix it first, setup never overwrites it") from e
    if not isinstance(v, dict):
        raise Blocked(f"{path} is not a JSON object; fix it first")
    return v, raw


def _sha(raw) -> str:
    return "absent" if raw is None else hashlib.sha256(raw).hexdigest()


def find_loop_contract(ctx):
    """The loop-contract root, resolved: ctx (config_log / env), else the installed plugin with version >= 1.1.0."""
    from . import session

    if ctx.loop_contract_root:
        return os.path.realpath(ctx.loop_contract_root)
    reg = Path(_home(ctx)) / ".claude" / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(reg.read_text("utf-8")).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return None
    best = None
    for name, entries in plugins.items():
        if not name.startswith("loop-contract@") or not isinstance(entries, list):
            continue
        for e in entries:
            root = e.get("installPath") if isinstance(e, dict) else None
            root = os.path.realpath(root) if isinstance(root, str) else None
            v = session.loop_contract_version(root) if root else None
            if v and v >= session.MIN_LOOP_CONTRACT and (best is None or v > best[0]):
                best = (v, root)
    return best[1] if best else None


def _short(ctx, path) -> str:
    s, home = str(path), _home(ctx)
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


# --- judging a state (verify, and plan's check of what it would write) ---


def _managed():
    path = MANAGED.get("darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "")
    if not path or not os.path.exists(path):
        return {}
    try:
        v = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _problems(ctx, lcr, scopes, managed) -> list:
    """What these settings miss against ``required``; never raises."""
    out = []
    labelled = [("managed settings", managed), ("local", scopes["local"]), ("project", scopes["project"]),
                ("user", scopes["user"])]  # highest precedence first
    order = [d for _, d in labelled]
    req = required(ctx, lcr)

    def each_list(key):
        merged = []
        for label, d in labelled:
            try:
                merged += _list(d, key, label)
            except Blocked as e:
                out.append(str(e))
        return merged

    def each_value(key):
        vals = []
        for label, d in labelled:
            try:
                vals.append(_value(d, key, label))
            except Blocked as e:
                out.append(str(e))
                vals.append(None)
        return vals

    for _scope, values in req["set"].items():
        for key, want in values.items():
            vals = each_value(key)  # managed, local, project, user
            if key == ("sandbox", "network", "strictAllowlist"):
                have = next((v for v in (vals[0], vals[3]) if v is not None), None)
            elif key == ("permissions", "disableBypassPermissionsMode"):
                have = "disable" if any(_same(v, "disable") for v in vals) else None
            elif key == ("sandbox", "allowUnsandboxedCommands") and (_same(vals[0], False) or _same(vals[3], False)):
                have = False  # a false here holds against project and local scopes (docs)
            else:
                have = next((v for v in vals if v is not None), None)
            if not _same(have, want):
                out.append(f"{_dot(key)} is {json.dumps(have) if have is not None else 'unset'} "
                           f"(needs {json.dumps(want)})")
    for key, wanted in req["include"].items():
        merged = set(each_list(key))
        missing = [w for w in wanted if w not in merged]
        if missing:
            out.append(f"{_dot(key)} lacks {', '.join(missing)}")
    for key in req["empty"]:
        merged = each_list(key)
        if merged:
            out.append(f"{_dot(key)} must be empty in every scope (has {json.dumps(merged)})")
    for key in req["forbid"]:
        if any(v not in (None, False) for v in each_value(key)):
            out.append(f"{_dot(key)} must be off in every scope")
    for key in req["delete"]:
        if any(v is not None for v in each_value(key)):
            out.append(f"{_dot(key)} must be unset in every scope")
    home = _home(ctx)
    for key, against in req["no_overlap"].items():
        bad = [e for e in each_list(key) if any(_inside(e, p, home) for p in req["include"][against])]
        if bad:
            out.append(f"{_dot(key)} must not reach a protected path (has {json.dumps(bad)})")
    return list(dict.fromkeys(out))


def verify(ctx) -> list:
    """What the effective settings miss; [] when this workspace is set up."""
    lcr = find_loop_contract(ctx)
    if not lcr:
        return ["loop-contract root unknown"]
    out, scopes = [], {}
    for scope, path in settings_paths(ctx).items():
        try:
            scopes[scope] = _read(path)[0]
        except Blocked as e:
            out.append(str(e))
            scopes[scope] = {}
    managed = _managed()
    if managed is None:
        out.append("managed settings are unreadable")
        managed = {}
    return out + _problems(ctx, lcr, scopes, managed)


# --- plan ---


def plan(ctx) -> dict:
    from . import session

    ws, home = os.path.realpath(ctx.workspace), _home(ctx)
    if ws == "/" or home == ws or home.startswith(ws.rstrip("/") + "/"):
        raise Blocked("open Claude Code in a project folder: setup will not configure your home directory "
                      "or one of its parents as a workspace")
    lcr = find_loop_contract(ctx)
    if not lcr:
        raise Blocked("the loop-contract plugin (>= 1.1.0) was not found; install it, then run setup again")
    lv = session.loop_contract_version(lcr)
    if not lv or lv < session.MIN_LOOP_CONTRACT:
        raise Blocked(f"loop-contract at {lcr} is older than 1.1.0; update it, then run setup again")
    req, paths = required(ctx, lcr), settings_paths(ctx)
    claude_dir = Path(ctx.workspace) / ".claude"
    if os.path.islink(claude_dir):
        raise Blocked(f"{claude_dir} is a symlink; setup will not write through it (remove it, then run setup again)")
    for scope in ("project", "local"):  # a cloned repo controls these; only the user's own dotfile is followed
        if os.path.islink(paths[scope]):
            raise Blocked(f"{paths[scope]} is a symlink; setup will not write through it "
                          "(replace it with a regular file, then run setup again)")
    managed = _managed()
    if managed is None:
        raise Blocked("the managed settings file is unreadable; ask your administrator")
    cur, base = {}, {}
    for scope, path in paths.items():
        cur[scope], raw = _read(path)
        base[scope] = _sha(raw)
        label = _short(ctx, path)
        for key in [*[k for v in req["set"].values() for k in v], *req["forbid"], *req["delete"]]:
            _value(cur[scope], key, label)  # shape checks: Blocked, never a silent rewrite
        for key in [*req["include"], *req["empty"], *req["no_overlap"]]:
            _list(cur[scope], key, label)
    new = copy.deepcopy(cur)
    ops = []
    groups = {g: [] for g in ("warning", "user", "project", "local", "harness")}

    def note(scope, text, detail=()):
        groups[scope].append(f"- {text}")
        groups[scope].extend(f"    {_short(ctx, d)}" for d in detail)

    def setv(scope, key, value):
        ops.append({"scope": scope, "key": list(key), "kind": "set", "had": _has(new[scope], key),
                    "old": _get(new[scope], key), "new": value, "keep": _existing_depth(new[scope], key)})
        _put(new[scope], key, value)

    for scope, values in req["set"].items():
        for key, want in values.items():
            have = _get(new[scope], key)
            if not _same(have, want):
                setv(scope, key, want)
                note(scope, f"{EXPLAIN[key]} ({_dot(key)} = {json.dumps(want)}, now "
                            f"{'unset' if have is None else json.dumps(have)})")
    for key, wanted in req["include"].items():
        merged = {x for sc in new for x in _list(new[sc], key, sc)}
        missing = [w for w in wanted if w not in merged]
        if missing:
            ops.append({"scope": "local", "key": list(key), "kind": "add", "entries": missing,
                        "had": _has(new["local"], key), "keep": _existing_depth(new["local"], key)})
            _put(new["local"], key, _list(new["local"], key, "local") + missing)
            note("local", f"{EXPLAIN[key]} (add to {_dot(key)}):", missing)
    for key in req["empty"]:
        for scope in ("user", "project", "local"):
            have = _list(new[scope], key, scope)
            if have:
                ops.append({"scope": scope, "key": list(key), "kind": "remove", "entries": list(have)})
                _put(new[scope], key, [])
                note(scope, f"{EXPLAIN[key]}: remove {json.dumps(have)} from {_dot(key)}"
                            + (" (other projects lose these entries too)" if scope == "user" else ""))
            elif scope == "local" and not _has(new[scope], key):
                setv(scope, key, [])
                note(scope, f"{EXPLAIN[key]} ({_dot(key)} = [])")
    for key in req["forbid"]:
        for scope in ("user", "project", "local"):
            if _get(new[scope], key) not in (None, False):
                note(scope, f"Turn off {_dot(key)} (now {json.dumps(_get(new[scope], key))}): it weakens the sandbox")
                setv(scope, key, False)
    for key in req["delete"]:
        for scope in ("user", "project", "local"):
            if _has(new[scope], key):
                old = _get(new[scope], key)
                ops.append({"scope": scope, "key": list(key), "kind": "delete", "old": old})
                _delete(new[scope], key, len(key))
                note(scope, f"Remove {_dot(key)} (now {json.dumps(old)}): a proxy of its own need not keep to the allow-list")
    for key, against in req["no_overlap"].items():
        for scope in ("user", "project", "local"):
            bad = [e for e in _list(new[scope], key, scope) if any(_inside(e, p, home) for p in req["include"][against])]
            if bad:
                ops.append({"scope": scope, "key": list(key), "kind": "remove", "entries": bad})
                _put(new[scope], key, [e for e in _list(new[scope], key, scope) if e not in bad])
                note(scope, f"{EXPLAIN[key]}: remove {json.dumps(bad)} from {_dot(key)}")
    left = _problems(ctx, lcr, new, managed)
    if left:  # e.g. managed settings from an administrator, which setup cannot change
        raise Blocked("these settings would still be missing after setup, so it would not help:\n"
                      + "\n".join(f"  - {x}" for x in left)
                      + ("\n  Your managed settings (set by an administrator) override this folder; ask them to "
                         "change these." if managed else ""))
    if os.path.islink(paths["user"]) and _differs(new["user"], cur["user"]):
        groups["user"].append(f"  ({_short(ctx, paths['user'])} is a symlink: setup writes to "
                              f"{_short(ctx, os.path.realpath(paths['user']))})")
    key_missing = not (Path(ctx.data_root) / "keys" / "hmac.key").exists()
    if key_missing:
        note("harness", "Create the private key that signs task receipts (readable only by you):",
             [Path(ctx.data_root) / "keys" / "hmac.key"])
    log = Path(ctx.data_root) / "config_log.jsonl"
    hashes = session.current_hashes(dataclasses.replace(ctx, loop_contract_root=Path(lcr)))
    try:
        recorded = session.recorded_hashes(log) if log.is_file() else {}
    except Exception:
        recorded = {}
    need_log = recorded != hashes or _config(log, "loop_contract_root") != lcr
    changed_layers = sorted(k for k in set(recorded) | set(hashes) if recorded and recorded.get(k) != hashes.get(k))
    if changed_layers:  # never re-baseline quietly: this is what SessionStart reports as STOPPED_TAMPER
        groups["warning"].append(
            f"WARNING: changed since the last setup: {', '.join(changed_layers)}. Confirm only if you updated or "
            "edited these yourself. If you did not, do not confirm: something else changed them.")
    if need_log:
        note("harness", "Record this release, so a later change to the harness, loop-contract or its controls is "
                        "noticed at session start (plus where loop-contract is):", [log, lcr])
    only_strict = all(o["key"] == ["sandbox", "network", "strictAllowlist"] for o in ops if o["scope"] == "user")
    heads = {"user": f"Your user settings, {_short(ctx, paths['user'])}, which every project reads. "
                     + ("This one key acts only in projects that turn the sandbox on: there, commands reach only "
                        "the domains that project allows, with no prompt to add one:" if only_strict else
                        "strictAllowlist acts only in projects that turn the sandbox on. Entries removed here would "
                        "also reach this folder, so other projects lose them too:"),
             "project": f"This folder's shared settings, {_short(ctx, paths['project'])}:",
             "local": f"This folder only, {_short(ctx, paths['local'])}:",
             "harness": "Harness files:"}
    lines = list(groups["warning"])
    for g in ("user", "project", "local", "harness"):
        if groups[g]:
            lines += ([""] if lines else []) + [heads[g], *groups[g]]
    changed = {sc: new[sc] for sc in paths if _differs(new[sc], cur[sc])}
    return {"files": changed, "paths": {sc: str(pp) for sc, pp in paths.items()}, "base": base, "lines": lines,
            "lcr": lcr, "need_key": key_missing, "need_log": need_log, "ops": ops,
            "created": {sc: base[sc] == "absent" for sc in changed}}


def _config(log: Path, key):
    val = None
    try:
        for line in log.read_text("utf-8").splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get(key), str):
                    val = row[key]
    except OSError:
        return None
    return val


def _plan_sha(p) -> str:
    keep = {k: p[k] for k in ("files", "paths", "base", "lcr", "need_key", "need_log", "ops")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


# --- apply / undo ---


def _sdir(ctx) -> Path:
    d = Path(ctx.data_root) / "setup"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def _locked(ctx):
    with open(_sdir(ctx) / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write_json(path: Path, value: dict, follow: bool):
    # only the user's own dotfile is followed (kept as a link); workspace files never are (plan refuses them)
    target = Path(os.path.realpath(path)) if follow else path
    if not follow and (os.path.islink(path) or os.path.islink(path.parent)):
        raise Blocked(f"{path} became a symlink; nothing more was written")
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat_mode(target)
    atomic_write(target, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    if mode is not None:
        os.chmod(target, mode)


def stat_mode(p):
    try:
        return os.stat(p).st_mode & 0o7777
    except FileNotFoundError:
        return None


def _make_key(ctx):
    keys = Path(ctx.data_root) / "keys"
    keys.mkdir(mode=0o700, exist_ok=True)
    os.chmod(keys, 0o700)
    try:
        fd = os.open(keys / "hmac.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return  # never rotated by a re-run
    with os.fdopen(fd, "wb") as fh:
        fh.write(secrets.token_bytes(32))


def _apply(ctx, p) -> list:
    from . import session

    bdir = _sdir(ctx) / "backups" / f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
    bdir.mkdir(parents=True)
    for scope in p["files"]:  # full copies, for recovery by hand; undo reverses the recorded operations
        src = Path(p["paths"][scope])
        if src.exists():
            shutil.copyfile(os.path.realpath(src), bdir / f"{scope}.json")
    manifest = {"paths": p["paths"], "ops": p["ops"], "created": p["created"]}
    atomic_write(bdir / "manifest.json", json.dumps(manifest, indent=2).encode())
    for scope, value in p["files"].items():
        _write_json(Path(p["paths"][scope]), value, follow=scope == "user")
    if p["need_key"]:
        _make_key(ctx)
    if p["need_log"]:
        lctx = dataclasses.replace(ctx, loop_contract_root=Path(p["lcr"]))
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hashes": session.current_hashes(lctx),
               "loop_contract_root": p["lcr"], "setup": sorted(p["paths"][s] for s in p["files"])}
        with open(Path(ctx.data_root) / "config_log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return p["lines"]


def _latest_backup(ctx):
    d = Path(ctx.data_root) / "setup" / "backups"
    found = sorted(glob.glob(str(d / "*" / "manifest.json")))
    return Path(found[-1]).parent if found else None


def _undo_plan(ctx):
    """Reverse setup's own operations on the files as they are now; anything changed since is left alone."""
    b = _latest_backup(ctx)
    if b is None:
        return None
    m = json.loads((b / "manifest.json").read_text("utf-8"))
    if "ops" not in m:
        raise Blocked(f"the last setup was made by an older version; restore by hand from {b}")
    paths = {sc: Path(pp) for sc, pp in m["paths"].items()}
    cur, base = {}, {}
    for sc, path in paths.items():
        if sc != "user" and (os.path.islink(path) or os.path.islink(path.parent)):
            raise Blocked(f"{path} is now a symlink; undo will not write through it")
        cur[sc], raw = _read(path)
        base[sc] = _sha(raw)
    new = copy.deepcopy(cur)
    lines, skipped = [], []
    for op in reversed(m["ops"]):
        sc, key, d = op["scope"], tuple(op["key"]), new[op["scope"]]
        where = f"{_short(ctx, paths[sc])}: {_dot(key)}"
        try:
            if op["kind"] == "set":
                if _has(d, key) and _same(_get(d, key), op["new"]):
                    if op["had"]:
                        _put(d, key, op["old"])
                        lines.append(f"- {where}: back to {json.dumps(op['old'])}")
                    else:
                        _delete(d, key, op["keep"])
                        lines.append(f"- {where}: remove")
                else:
                    skipped.append(f"- {where}: changed since setup, left as is")
            elif op["kind"] == "add":
                have = _list(d, key, sc)
                gone = [e for e in op["entries"] if e in have]
                rest = [e for e in have if e not in op["entries"]]
                if rest or op["had"]:
                    _put(d, key, rest)
                elif _has(d, key):
                    _delete(d, key, op["keep"])
                if gone:
                    lines.append(f"- {where}: remove the {len(gone)} entries setup added")
            elif op["kind"] == "remove":
                have = _list(d, key, sc)
                back = [e for e in op["entries"] if e not in have]
                if back:
                    _put(d, key, have + back)
                    lines.append(f"- {where}: put back {json.dumps(back)}")
            elif op["kind"] == "delete":
                if not _has(d, key):
                    _put(d, key, op["old"])
                    lines.append(f"- {where}: back to {json.dumps(op['old'])}")
                else:
                    skipped.append(f"- {where}: changed since setup, left as is")
        except Blocked:
            skipped.append(f"- {where}: changed since setup, left as is")
    remove = [sc for sc in new if m["created"].get(sc) and new[sc] == {} and base[sc] != "absent"]
    for sc in remove:
        lines.append(f"- {_short(ctx, paths[sc])}: remove the file (setup created it, and it is empty again)")
    return {"backup": str(b), "paths": {sc: str(pp) for sc, pp in paths.items()}, "base": base, "new": new,
            "remove": remove, "lines": lines + skipped, "changed": [sc for sc in new if _differs(new[sc], cur[sc])]}


def _undo(ctx, u):
    for sc in u["changed"]:
        path = Path(u["paths"][sc])
        if sc in u["remove"]:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
        else:
            _write_json(path, u["new"][sc], follow=sc == "user")
    for sc in u["remove"]:
        if sc not in u["changed"]:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(u["paths"][sc])
    shutil.rmtree(u["backup"])
    return u["lines"]


def _undo_sha(u) -> str:
    keep = {k: u[k] for k in ("backup", "paths", "base", "new", "remove")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


# --- the hook ---


def _block(text):
    return {"decision": "block", "reason": text}


def _code():
    return f"{secrets.randbelow(10 ** 6):06d}"


def on_prompt(evt, ctx) -> dict:
    """UserPromptSubmit for ``/dynamic-harness:setup ...``: always blocks, so the prompt never reaches the model."""
    args = evt["prompt"].strip()[len(PREFIX):].split()
    sid = evt.get("session_id")
    with _locked(ctx):
        pending_f = _sdir(ctx) / "pending.json"
        if not args or args == ["undo"]:
            try:
                if args:
                    u = _undo_plan(ctx)
                    if u is None:
                        return _block("Nothing to undo: no setup backup exists.")
                    body, kind, sha = u["lines"], "undo", _undo_sha(u)
                else:
                    p = plan(ctx)
                    if not p["lines"]:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(pending_f)
                        return _block("dynamic-harness is already set up for this workspace. Nothing to change.")
                    body, kind, sha = p["lines"], "apply", _plan_sha(p)
            except Blocked as e:
                return _block(f"Setup cannot continue: {e}")
            code = _code()
            atomic_write(pending_f, json.dumps({"kind": kind, "code": code, "sid": sid, "ts": time.time(),
                                                "sha": sha}).encode())
            head = ("Setup will make these changes. Nothing changes until you confirm." if kind == "apply"
                    else "Undo will make these changes. Nothing changes until you confirm.")
            tail = (f"To apply, type: {PREFIX} confirm {code}\n"
                    f"(The code works once, in this session, for 10 minutes. Ignore it to cancel.)")
            return _block("\n".join([head, "", *body, "", tail]))
        if args[0] == "confirm" and len(args) == 2:
            try:
                pend = json.loads(pending_f.read_text("utf-8"))
            except (OSError, ValueError):
                return _block(f"No pending setup plan. Type {PREFIX} first.")
            if not hmac.compare_digest(str(pend.get("code")), args[1]):
                return _block("That code does not match the latest plan. Nothing was changed.")
            os.unlink(pending_f)  # one use, whatever happens next
            if pend.get("sid") != sid:
                return _block("That code was issued in another session. Nothing was changed. Run setup again here.")
            if not (0 <= time.time() - float(pend.get("ts", 0)) <= PENDING_TTL):
                return _block("That code has expired. Nothing was changed. Run setup again.")
            try:
                if pend.get("kind") == "undo":
                    u = _undo_plan(ctx)
                    if u is None or _undo_sha(u) != pend.get("sha"):
                        return _block("The settings changed since the undo plan. Nothing was changed. Run undo again.")
                    done = _undo(ctx, u)
                    return _block("\n".join(["Restored:", *done, "",
                                             "Harness tasks above T0 are refused until setup runs again. "
                                             "Start a new session for the restored settings to apply everywhere."]))
                p = plan(ctx)
                if _plan_sha(p) != pend.get("sha"):
                    return _block("The settings changed since that plan. Nothing was changed. Run setup again.")
                done = _apply(ctx, p)
            except Blocked as e:
                return _block(f"Setup cannot continue: {e}")
            return _block("\n".join(["Applied:", "", *done, "",
                                     "Start a new Claude Code session in this folder: the sandbox and the session "
                                     f"checks apply from the next session. To reverse this, type {PREFIX} undo."]))
        if args == ["status"]:
            probs = verify(ctx)
            if not probs:
                return _block("dynamic-harness is set up for this workspace.")
            return _block("\n".join(["dynamic-harness is not set up for this workspace:", *[f"- {x}" for x in probs],
                                     "", f"Type {PREFIX} to see the plan."]))
        return _block(USAGE)
