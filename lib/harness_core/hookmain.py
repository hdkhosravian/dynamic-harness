"""Hook entry point (spec §Trust boundaries: "Hook failure is a deny").

Every handler runs inside ``try/except BaseException`` and a watchdog: the
matcher timeout minus 5 s for the gate and fetch hooks (1795 s, 55 s), and
minus 1 s otherwise (Stop 9 s, every other hook 4 s). Any exception, overrun or
malformed result exits 2 with a reason on stderr: a deny for PreToolUse, a
block for Stop. Claude Code treats other non-zero exits as allow, so 2 is the
only failure code used. A normal deny also exits 2 with the reason on stderr.

``build_ctx`` fails closed: no ``CLAUDE_PLUGIN_DATA`` / ``CLAUDE_PLUGIN_ROOT``
(protected patterns could not match) or no known workspace raises. The
workspace is ``CLAUDE_PROJECT_DIR``, else the cwd SessionStart recorded, never
the event's current cwd. Hook state is read with ``safe_open``.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

WATCHDOG = {"Stop": 9, "fetch": 55, "gate": 1795, "UserPromptSubmit": 9}
DEFAULT_WATCHDOG = 4
STATUS_MAX_AGE = 24 * 3600  # shortcut: a day-old SessionStart status goes stale; resume/compact re-run SessionStart


def watchdog(event: str) -> float:
    return WATCHDOG.get(event, DEFAULT_WATCHDOG)


HANDLERS = {"PreToolUse": ("pretool", "on_pre"), "SessionStart": ("session", "on_start"),
            "ConfigChange": ("config_guard", "on_change"), "PostToolUse": ("posttool", "on_post"),
            "PostToolUseFailure": ("written", "on_post"),  # a failed Write/Edit may still have written
            "UserPromptSubmit": ("prompt", "on_prompt"), "SubagentStop": ("subagent", "on_subagent_stop"),
            "SubagentStart": ("lanes", "on_subagent_start"),
            "Stop": ("stopgate", "on_stop"), "gate": ("gatehook", "on_post")}  # PostToolUse --gate: its own 1800 s hooks.json entry


def _handler(event):
    """Import only the module this event needs, so one module's bug cannot take down the others."""
    import importlib

    if event not in HANDLERS:
        return None
    mod, fn = HANDLERS[event]
    return getattr(importlib.import_module(f"{__package__}.{mod}"), fn)


def _config_value(data: Path, key: str):
    """Latest ``key`` in config_log.jsonl (written by setup). Unparseable lines are SessionStart's to judge."""
    from .safeio import safe_open

    f = data / "config_log.jsonl"
    if not os.path.lexists(f):
        return None
    val = None
    for line in safe_open(f, [data]).decode("utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get(key), str):
            val = row[key]
    return val


def _grant(data: Path, job: str):
    """The job's egress grant, honoured only when the latest event recording it is a GRANT with its sha."""
    from . import canon
    from .events import EventLog
    from .safeio import safe_open

    run = data / "runs" / job
    f = run / "egress_grant.json"
    if not os.path.lexists(f):
        return None
    raw = safe_open(f, [data])
    log = EventLog(run, data / "heads" / job)
    log.verify()  # a broken chain raises: fail closed
    rec = [e for e in log.events() if e.get("file") == "egress_grant.json"]
    if not rec or rec[-1].get("type") != "GRANT" or rec[-1].get("file_sha") != canon.sha256_bytes(raw):
        return None
    return json.loads(raw)


def _session_ok(status, lcr):
    if not isinstance(status, dict):
        return False, "no SessionStart status for this session"
    if status.get("ok") is not True:
        return False, "; ".join(map(str, status.get("reasons") or [])) or "SessionStart checks failed"
    ts = status.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not (0 <= time.time() - ts <= STATUS_MAX_AGE):
        return False, "SessionStart status is stale"
    if not lcr:
        return False, "not set up (loop-contract location unknown): type /dynamic-harness:setup"
    return True, None


def _spawned_in(data, ws, job, agent_id) -> bool:
    """A subagent belongs to a task only when the task's spawn record links its agent id."""
    if not job or not isinstance(agent_id, str):
        return False
    from . import store
    from .events import EventLog
    from .paths import job_paths
    p = job_paths(data, ws, job)
    return any(r.get("agent_id") == agent_id for r in store.read_jsonl(p.run, EventLog(p.run, p.heads), "spawns.jsonl"))


def build_ctx(evt: dict, env, event=None):
    from .errors import TamperError
    from . import written
    from .events import EventLog
    from .safety import Ctx

    def p(name):
        v = env.get(name)
        return Path(v) if v else None

    data, root = p("CLAUDE_PLUGIN_DATA"), p("CLAUDE_PLUGIN_ROOT")
    if data is None or root is None:
        raise ValueError("CLAUDE_PLUGIN_DATA or CLAUDE_PLUGIN_ROOT unset: protected paths cannot be checked")
    sid = evt.get("session_id")
    sdir = written.session_dir(data, sid) if sid is not None else None

    def load(path):
        return written._load(path, data)

    status = load(sdir / "status.json") if sdir else None
    ws = env.get("CLAUDE_PROJECT_DIR") or (evt.get("cwd") if event == "SessionStart" else (status or {}).get("cwd"))
    if not isinstance(ws, str) or not ws:
        raise ValueError("workspace unknown: CLAUDE_PROJECT_DIR unset and no SessionStart cwd recorded")
    lcr = _config_value(data, "loop_contract_root") or env.get("LOOP_CONTRACT_ROOT")
    ctx = Ctx(
        workspace=Path(ws),
        data_root=data,
        plugin_root=root,
        home=p("HOME"),
        loop_contract_root=Path(os.path.realpath(lcr)) if lcr else None,  # safe_open compares resolved paths
        agent_type=evt.get("agent_type"),
        permission_mode=evt.get("permission_mode"),
        cwd=Path(evt["cwd"]) if isinstance(evt.get("cwd"), str) and evt.get("cwd") else None,
    )
    ctx.session_ok, ctx.session_reason = _session_ok(status, lcr)
    ctx.connector_registry = load(data / "business" / "connector_registry.json") or {}
    terms = load(data / "business" / "protected_terms.json") or []
    ctx.protected_terms = list(terms.get("terms", []) if isinstance(terms, dict) else terms)
    if sdir is None:
        return ctx
    ctx.log = EventLog(sdir, sdir / "head")
    ctx.settings_baseline = load(sdir / "settings_baseline.json")
    if event in (None, "PreToolUse"):  # only the classifier reads them; one workspace walk per call
        ctx.written_this_task, ctx.untrusted_files = written.session_sets(data, sid, ctx.workspace)
    from . import sessionstate
    try:
        sess = sessionstate.load(ctx, sid)  # checked against its SESSION event: an out-of-band edit is tamper
    except TamperError as e:  # never brick the session (setup and reads must still work): reset it closed
        sess = sessionstate.reset(ctx, sid, str(e))
    ctx.pending_card = sess.get("pending_card") if isinstance(sess.get("pending_card"), dict) else None
    ctx.prompt_scope = sess.get("prompt_scope")
    # scope rule: hooks resolve to active_task only when this prompt belongs to it; a subagent's calls belong
    # to the task it was spawned in, whatever the main thread's latest prompt was
    agent_id = evt.get("agent_id")  # agent_type without agent_id is the main thread run as an agent
    if agent_id:
        open_job = sess.get("active_task")
        open_job = open_job if open_job and not sessionstate.jobs(ctx).get(open_job, {}).get("terminal") else None
        job = open_job  # fail closed: an unlinked subagent keeps the open task's gates, never "no task"
        ctx.subagent_unlinked = bool(open_job) and not _spawned_in(data, ws, open_job, agent_id)
    else:
        job = sess.get("active_task") if sess.get("prompt_scope") == "active_task" else None
    if job:
        ctx.job = written.safe_id(job, "job")
        run = data / "runs" / ctx.job
        ctx.grant = None if ctx.subagent_unlinked else _grant(data, ctx.job)  # no borrowed research grant
        ctx.owner_tokens = list(load(run / "owner_tokens.json") or [])
        ctx.task_started = (load(run / "tier.json") or {}).get("started_ts")
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


def _mask(v):
    return f"<redacted {type(v).__name__} len={len(json.dumps(v, default=str))}>"


def _redact(evt):
    """Tool inputs/responses and the transcript path never reach the debug log."""
    if not isinstance(evt, dict):
        return _mask(evt)
    out = dict(evt)
    for k in ("tool_input", "tool_response"):
        if isinstance(out.get(k), dict):
            out[k] = {kk: _mask(vv) for kk, vv in out[k].items()}
        elif k in out:
            out[k] = _mask(out[k])
    if "transcript_path" in out:
        out["transcript_path"] = "<redacted>"
    return out


def _debug(env, event, stdin_json, code, out, reason):
    """Opt-in (HARNESS_DEBUG_LOG=<file>) JSONL trace of hook input and decision. Never affects the result."""
    path = env.get("HARNESS_DEBUG_LOG")
    if not path:
        return
    try:
        row = {"event": event, "input": _redact(stdin_json), "code": code, "out": out, "reason": reason}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as e:
        print(f"harness: debug log not written: {type(e).__name__}: {e}", file=sys.stderr)


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
        result = _with_watchdog(lambda: fn(stdin_json, build_ctx(stdin_json, env, event)), seconds)
        if result is None or result == {}:
            return 0, None, None
        if not isinstance(result, dict):
            return _fail(f"handler returned {type(result).__name__} (fail closed)")
        if event == "UserPromptSubmit" and result.get("decision") == "block":
            return 0, result, None  # JSON block: the reason is shown to the user, not added to context
        if result.get("decision") in ("deny", "block"):
            return _fail(str(result.get("reason") or "denied"))
        return 0, result, None
    except BaseException as e:  # includes SystemExit/KeyboardInterrupt: a crash is a deny
        return _fail(f"{type(e).__name__}: {e} (fail closed)")
