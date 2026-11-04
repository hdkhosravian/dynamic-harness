"""The owner's and operator's typed commands (spec §Detailed plan 9 "commands"; build phase P5).

``/dynamic-harness:<command> …`` is handled entirely in the UserPromptSubmit hook, like setup: Claude Code passes
the typed line to the hook before the command expands, and a ``block`` decision shows the answer to the user,
adds nothing to context and stops the prompt. The model never sees, runs or forges these commands (the slash
command files set ``disable-model-invocation: true``; a model-typed slash command is not a user prompt).

Reading commands answer at once. Every command that changes something works in two steps, as setup does: the
hook shows the change in plain words with a one-time 6-digit code, and ``… confirm <code>`` applies exactly that
change, when the code matches, is under 10 minutes old and comes from the same session. The code works once.
"""
from __future__ import annotations

import contextlib
import fcntl
import hmac
import json
import os
import re
import secrets
import shlex
import time
from pathlib import Path

from . import business
from .errors import Denied, HarnessError
from .safeio import atomic_write

PREFIX = "/dynamic-harness:"
PENDING_TTL = 600
COMMANDS = ("business", "config", "memory", "status", "export", "off")
_LINE = re.compile(re.escape(PREFIX) + r"(" + "|".join(COMMANDS) + r")(?:\s+(.*))?", re.S)


def command_of(evt):
    p = evt.get("prompt") if isinstance(evt, dict) else None
    m = _LINE.fullmatch(p.strip()) if isinstance(p, str) else None
    return (m.group(1), m.group(2) or "") if m else None


def _block(lines) -> dict:
    return {"decision": "block", "reason": "\n".join(lines) if isinstance(lines, (list, tuple)) else lines}


def _dir(ctx) -> Path:
    d = Path(ctx.data_root) / "admin"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def _locked(ctx):
    with open(_dir(ctx) / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _offer(ctx, sid, cmd, change: dict, lines) -> dict:
    """Store the pending change and show it with its code. Nothing changes yet."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    atomic_write(_dir(ctx) / "pending.json", json.dumps({"cmd": cmd, "change": change, "code": code, "sid": sid,
                                                         "ts": time.time()}).encode("utf-8"))
    return _block(["This will change:", *lines, "", f"To apply, type: {PREFIX}{cmd} confirm {code}",
                   "(The code works once, in this session, for 10 minutes. Ignore it to cancel.)"])


def _take(ctx, sid, cmd, code) -> dict:
    """The pending change for ``code``, consumed; Denied with the reason otherwise."""
    f = _dir(ctx) / "pending.json"
    try:
        pend = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        raise Denied(f"Nothing is waiting for confirmation. Type the {PREFIX}{cmd} change first.")
    if pend.get("cmd") != cmd or not hmac.compare_digest(str(pend.get("code")), code):
        raise Denied("That code does not match the latest change. Nothing was changed.")
    os.unlink(f)  # one use, whatever happens next
    if pend.get("sid") != sid:
        raise Denied("That code was issued in another session. Nothing was changed.")
    if not (0 <= time.time() - float(pend.get("ts", 0)) <= PENDING_TTL):
        raise Denied("That code has expired. Nothing was changed.")
    return pend["change"]


# --- business ----------------------------------------------------------------------------------------------------
BUSINESS_USAGE = [
    f"Usage: {PREFIX}business show",
    f"       {PREFIX}business set <field> <value>     fields: {', '.join(business.PROFILE_FIELDS)}",
    f"       {PREFIX}business add|remove <list> <value>   lists: {', '.join(business.LIST_FIELDS)}",
    f"       {PREFIX}business metric remove <metric_id>",
    "Metric definitions are added when you confirm one during a task.",
]


def _business(ctx, sid, args) -> dict:
    if not args or args == ["show"]:
        return _block(business.describe(ctx))
    if args[0] == "confirm" and len(args) == 2:
        ch = _take(ctx, sid, "business", args[1])
        if ch["op"] == "set":
            business.set_field(ctx, ch["field"], ch["value"])
        elif ch["op"] in ("add", "remove"):
            business.change_list(ctx, ch["field"], ch["value"], ch["op"] == "add")
        elif ch["op"] == "metric_remove":
            business.remove_metric(ctx, ch["metric_id"])
        return _block(["Done. " + ch["sentence"], "New tasks use it from now on."])
    if args[0] == "set" and len(args) >= 3:
        field, raw = args[1], " ".join(args[2:])
        value = business.parse_value(field, raw)
        business.guard(ctx)
        shown = f"{business.CURRENCIES.get(value, value)} ({value})" if field == "currency" else value
        label = business.PROFILE_FIELDS[field]
        sentence = f"Set {label} to {shown}."
        return _offer(ctx, sid, "business", {"op": "set", "field": field, "value": value, "sentence": sentence},
                      [f"- {sentence}"])
    if args[0] in ("add", "remove") and len(args) >= 3 and args[1] in business.LIST_FIELDS:
        value = " ".join(args[2:])
        business.guard(ctx)
        sentence = (f"{'Add' if args[0] == 'add' else 'Remove'} {value!r} "
                    f"{'to' if args[0] == 'add' else 'from'} {business.LIST_FIELDS[args[1]]}.")
        return _offer(ctx, sid, "business", {"op": args[0], "field": args[1], "value": value, "sentence": sentence},
                      [f"- {sentence}"])
    if args[:2] == ["metric", "remove"] and len(args) == 3:
        if args[2] not in business.dictionary(ctx):
            raise Denied(f"There is no metric {args[2]!r}.")
        sentence = f"Remove the definition of {args[2]}. Tasks that need it will ask you again."
        return _offer(ctx, sid, "business", {"op": "metric_remove", "metric_id": args[2], "sentence": sentence},
                      [f"- {sentence}"])
    return _block(BUSINESS_USAGE)


HANDLERS = {"business": _business}


def on_prompt(evt, ctx) -> dict:
    """UserPromptSubmit for ``/dynamic-harness:<command>``: always blocks, so the model never sees it."""
    cmd, rest = command_of(evt)
    sid = evt.get("session_id")
    try:
        args = shlex.split(rest)
    except ValueError as e:
        return _block(f"Could not read that command: {e}.")
    fn = HANDLERS.get(cmd)
    if fn is None:
        return _block(f"{PREFIX}{cmd} is not available yet.")
    try:
        with _locked(ctx):
            return fn(ctx, sid, args)
    except Denied as e:
        return _block(str(e))
    except HarnessError as e:
        return _block(f"Nothing was changed: {e}")
