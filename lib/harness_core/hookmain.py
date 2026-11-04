"""Hook entry point (spec §Trust boundaries: "Hook failure is a deny").

Every handler runs inside ``try/except BaseException`` and a watchdog (matcher
timeout minus 5 s: 4 s by default, Stop 9 s, fetch 55 s, gate 1795 s). Any
exception, overrun or malformed result exits 2 with a reason on stderr: a deny
for PreToolUse, a block for Stop. Claude Code treats other non-zero exits as
allow, so 2 is the only failure code used. A normal deny also exits 2 with the
reason on stderr (INTERFACES: until the OQ-4 spike confirms the JSON form).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

WATCHDOG = {"Stop": 9, "fetch": 55, "gate": 1795}
DEFAULT_WATCHDOG = 4
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def watchdog(event: str) -> float:
    return WATCHDOG.get(event, DEFAULT_WATCHDOG)


HANDLERS = {"PreToolUse": ("safety", "on_pre"), "SessionStart": ("session", "on_start"),
            "ConfigChange": ("config_guard", "on_change")}


def _handler(event):
    """Import only the module this event needs, so one module's bug cannot take down the others."""
    import importlib

    if event not in HANDLERS:
        return None
    mod, fn = HANDLERS[event]
    return getattr(importlib.import_module(f"{__package__}.{mod}"), fn)


def _json(path: Path):
    return json.loads(path.read_text("utf-8")) if path.is_file() else None


def _safe_id(v, what):
    if not isinstance(v, str) or not _ID.match(v) or v in (".", ".."):
        raise ValueError(f"invalid {what}: {v!r}")
    return v


def build_ctx(evt: dict, env):
    from .safety import Ctx

    def p(name):
        v = env.get(name)
        return Path(v) if v else None

    data = p("CLAUDE_PLUGIN_DATA")
    ctx = Ctx(
        workspace=Path(evt.get("cwd") or env.get("CLAUDE_PROJECT_DIR") or os.getcwd()),
        data_root=data,
        plugin_root=p("CLAUDE_PLUGIN_ROOT"),
        home=p("HOME"),
        loop_contract_root=p("LOOP_CONTRACT_ROOT"),
        agent_type=evt.get("agent_type"),
        permission_mode=evt.get("permission_mode"),
    )
    if data is None:
        return ctx
    ctx.connector_registry = _json(data / "business" / "connector_registry.json") or {}
    terms = _json(data / "business" / "protected_terms.json") or []
    ctx.protected_terms = list(terms.get("terms", []) if isinstance(terms, dict) else terms)
    sid = evt.get("session_id")
    if sid is not None:
        sess = _json(data / "sessions" / f"{_safe_id(sid, 'session_id')}.json") or {}
        job = sess.get("active_task")
        if job:
            ctx.job = _safe_id(job, "job")
            run = data / "runs" / ctx.job
            ctx.grant = _json(run / "egress_grant.json")
            ctx.written_this_task = set(_json(run / "written.json") or [])
            ctx.owner_tokens = list(_json(run / "owner_tokens.json") or [])
            ctx.task_started = (_json(run / "tier.json") or {}).get("started_ts")
    return ctx


class _Overrun(Exception):
    pass


def _with_watchdog(fn, seconds):
    box = {}

    def target():
        try:
            box["r"] = fn()
        except BaseException as e:  # carried to the caller, which fails closed
            box["e"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise _Overrun(f"watchdog: handler exceeded {seconds} s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def _fail(reason: str):
    return 2, None, reason


def _debug(env, event, stdin_json, code, out, reason):
    """Opt-in (HARNESS_DEBUG_LOG=<file>) JSONL trace of hook input and decision. Never affects the result."""
    try:
        path = env.get("HARNESS_DEBUG_LOG")
        if path:
            row = {"event": event, "input": stdin_json, "code": code, "out": out, "reason": reason}
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


def main(event: str, stdin_json, env, handlers=None, timeouts=None):
    """Returns ``(exit_code, stdout_json)``. Never raises."""
    code, out, reason = _main(event, stdin_json, env, handlers, timeouts)
    if reason:
        print(f"harness: {reason}", file=sys.stderr)
    _debug(env, event, stdin_json, code, out, reason)
    return code, out


def _main(event, stdin_json, env, handlers, timeouts):
    try:
        if not isinstance(stdin_json, dict):
            return _fail("hook input is not a JSON object (fail closed)")
        fn = handlers.get(event) if handlers is not None else _handler(event)
        if fn is None:
            return _fail(f"no handler for hook event {event!r} (fail closed)")
        seconds = (timeouts or {}).get(event, watchdog(event))
        result = _with_watchdog(lambda: fn(stdin_json, build_ctx(stdin_json, env)), seconds)
        if result is None or result == {}:
            return 0, None, None
        if not isinstance(result, dict):
            return _fail(f"handler returned {type(result).__name__} (fail closed)")
        if result.get("decision") in ("deny", "block"):
            return _fail(str(result.get("reason") or "denied"))
        return 0, result, None
    except BaseException as e:  # includes SystemExit/KeyboardInterrupt: a crash is a deny
        return _fail(f"{type(e).__name__}: {e} (fail closed)")
