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


def _money_warnings(ctx, field, value) -> list:
    """What the owner must know for a spending limit to work: a currency, and a rate when prices are foreign."""
    from . import protected
    prof = dict(business.profile(ctx))
    if field == "currency":
        prof["currency"] = value
    elif field == "fx.USD":
        prof["fx"] = dict(prof.get("fx") or {}, USD=value)
    if field not in ("caps.task", "caps.month", "currency", "fx.USD") or not (prof.get("caps") or field.startswith("caps")):
        return []
    out = []
    cur = prof.get("currency")
    try:
        price_cur = protected.load_control("prices.json", ctx).get("currency")
    except Exception:
        price_cur = None
    if not cur:
        out.append("spending limits apply only once your currency is set (business set currency IRT)")
    elif price_cur and price_cur != cur and not (prof.get("fx") or {}).get(price_cur):
        out.append(f"model prices are in {price_cur}: spending limits apply only once you set the rate "
                   f"(business set fx.{price_cur} <{cur} per {price_cur}>)")
    if field == "currency" and (prof.get("caps") or {}) and {business.profile(ctx).get("currency"), value} != {"IRT", "IRR"}:
        out.append("your spending limits are in the old currency and will be removed; set them again")
    return out


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
        value = business.parse_value(field, raw, business.profile(ctx).get("calendar"))
        business.guard(ctx)
        shown = f"{business.CURRENCIES.get(value, value)} ({value})" if field == "currency" else value
        label = business.PROFILE_FIELDS[field]
        sentence = f"Set {label} to {shown}."
        lines = [f"- {sentence}"]
        lines += [f"- Note: {w}" for w in _money_warnings(ctx, field, value)]
        return _offer(ctx, sid, "business", {"op": "set", "field": field, "value": value, "sentence": sentence},
                      lines)
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


# --- memory --------------------------------------------------------------------------------------------------
def _memory_rows(ctx):
    from . import memory, store
    return store.read_jsonl(ctx.data_root, store.session_log(ctx.data_root), memory.FILE)


def _uses_since(ctx, ts) -> int:
    """Tasks recorded after a preference was set (the "use count" of the What I remember view)."""
    import glob
    n = 0
    for f in glob.glob(str(Path(ctx.data_root) / "records" / "*.jsonl")):
        try:
            first = json.loads(Path(f).read_text("utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        n += str(first.get("ts", "")) >= str(ts)
    return n


def _memory(ctx, sid, args) -> dict:
    from . import memory, store
    from .events import now
    if not args or args == ["show"]:
        eff = memory.effective(ctx)
        if not eff:
            return _block("I don't remember any preferences yet.")
        latest = {}
        for r in _memory_rows(ctx):
            if r.get("key") in eff and not r.get("forgotten") and r.get("scope") != "task":
                latest[r["key"]] = r
        lines = ["What I remember:"]
        for k, v in sorted(eff.items()):
            ts = (latest.get(k) or {}).get("ts", "")
            lines.append(f"- {k}: {v} (since {str(ts)[:10]}, used in {_uses_since(ctx, ts)} tasks)")
        lines.append(f"Change one with {PREFIX}memory set <key> <value>, or {PREFIX}memory forget <key>|all.")
        return _block(lines)
    if args[0] == "confirm" and len(args) == 2:
        ch = _take(ctx, sid, "memory", args[1])
        row = {"schema": "harness/memory@1", "id": secrets.token_hex(8), "scope": "global", "person_id": "owner",
               "key": ch["key"], "ts": now(), "source_task": None}
        if ch["op"] == "forget":
            row.update(value=None, forgotten=True, verbatim="")
        else:
            row.update(value=ch["value"], verbatim=f"typed: {ch['value']}")
        store.append_jsonl(ctx.data_root, store.session_log(ctx.data_root), memory.FILE, row, "MEMORY",
                           {"key": ch["key"], "scope": "global", "typed": True})
        return _block("Done. " + ch["sentence"])
    if args[0] == "set" and len(args) == 3:
        key, value = args[1], memory._value(args[1], args[2])
        if key not in memory.ALLOWED or value is None:
            opts = "; ".join(f"{k}: {', '.join(v)}" for k, v in memory.ALLOWED.items())
            return _block(f"I can remember only these: {opts}.")
        sentence = f"Remember {key} = {value} for every task."
        return _offer(ctx, sid, "memory", {"op": "set", "key": key, "value": value, "sentence": sentence},
                      [f"- {sentence}"])
    if args[0] == "forget" and len(args) == 2:
        key = "*" if args[1] == "all" else args[1]
        if key != "*" and key not in memory.effective(ctx):
            return _block(f"I don't remember anything for {args[1]!r}.")
        sentence = "Forget everything I remember." if key == "*" else f"Forget {key}."
        return _offer(ctx, sid, "memory", {"op": "forget", "key": key, "sentence": sentence}, [f"- {sentence}"])
    return _block(f"Usage: {PREFIX}memory show | set <key> <value> | forget <key>|all")


# --- status ----------------------------------------------------------------------------------------------------
def _status(ctx, sid, args) -> dict:
    from . import record, sessionstate
    from .events import EventLog
    from .paths import job_paths
    jobs = sessionstate.jobs(ctx)
    if not args:
        if not jobs:
            return _block("No tasks yet.")
        lines = ["Your tasks:"]
        for j, r in sorted(jobs.items(), key=lambda kv: str(kv[1].get("ts", "")), reverse=True)[:30]:
            lines.append(f"- {j}: {r.get('state')} ({r.get('task_type') or 'task'}, {str(r.get('ts', ''))[:16]})")
        lines.append(f"To stop one for good: {PREFIX}status --abandon <task>.")
        return _block(lines)
    if args[0] == "confirm" and len(args) == 2:
        ch = _take(ctx, sid, "status", args[1])
        job = ch["job"]
        p = job_paths(ctx.data_root, ctx.workspace, job)
        try:
            EventLog(p.run, p.heads).append("STOPPED", {"state": "ABANDONED", "reason": ch["why"]})
        except HarnessError:  # a tampered chain cannot take an event; the jobs index and record still close it
            pass
        sessionstate.set_job(ctx, job, state="ABANDONED", closed_by=ch["why"])
        try:
            record.write(ctx, job, "ABANDONED")
        except HarnessError as e:
            return _block(f"Closed {job}. Its decision record could not be read back ({e}); its files are kept.")
        return _block(f"Closed {job}. It will not continue; its decision record is kept.")
    if args[0] in ("--abandon", "--repair") and len(args) == 2:
        job = args[1]
        row = jobs.get(job)
        if row is None:
            return _block(f"There is no task {job!r}.")
        if args[0] == "--abandon":
            if row.get("terminal"):
                return _block(f"{job} has already ended ({row.get('state')}).")
            why = "abandoned by the owner"
            sentence = f"Stop {job} for good. Its work and record are kept; it cannot be resumed."
        else:
            if row.get("state") != "STOPPED_TAMPER":
                return _block(f"--repair closes a task stopped for tampering; {job} is {row.get('state')}.")
            why = "closed by the operator after tampering; its records are not trusted"
            sentence = (f"Close {job}, which stopped because its records were changed outside the harness. "
                        "Nothing in it is trusted or re-signed; start a new task for the work.")
        return _offer(ctx, sid, "status", {"job": job, "why": why, "sentence": sentence}, [f"- {sentence}"])
    return _block(f"Usage: {PREFIX}status [--abandon <task> | --repair <task>]")


# --- export ----------------------------------------------------------------------------------------------------
EXPORT_COLUMNS = ("job_id", "state", "interim", "ts", "requester", "tier", "task_type", "request_sha", "checks",
                  "receipt_mode", "receipt_caps", "report_sha", "cost_calls", "cost_money", "duration_s")


def _export(ctx, sid, args) -> dict:
    import csv
    import glob
    import io
    if len(args) != 2 or args[0] not in ("--task", "--period"):
        return _block(f"Usage: {PREFIX}export --task <task> | --period <YYYY-MM>[:<YYYY-MM>]")
    rows = []
    for f in sorted(glob.glob(str(Path(ctx.data_root) / "records" / "*.jsonl"))):
        try:
            rows += [json.loads(x) for x in Path(f).read_text("utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            continue
    from . import canon
    here = canon.sha256_bytes(str(ctx.workspace).encode("utf-8"))
    rows = [r for r in rows if r.get("workspace_sha") == here]  # only this folder's tasks land in this folder
    if args[0] == "--task":
        rows = [r for r in rows if r.get("job_id") == args[1]]
        label = re.sub(r"[^A-Za-z0-9._-]", "_", args[1])[:80]
    else:
        m = re.fullmatch(r"(\d{4}-\d{2})(?::(\d{4}-\d{2}))?", args[1])
        if not m:
            return _block("The period is YYYY-MM or YYYY-MM:YYYY-MM (Gregorian months).")
        lo, hi = m.group(1), m.group(2) or m.group(1)
        rows = [r for r in rows if lo <= str(r.get("ts", ""))[:7] <= hi]
        label = args[1].replace(":", "_to_")
    if not rows:
        return _block("No decision records match.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    def cell(v):  # a spreadsheet must never run a cell as a formula
        t = "" if v is None else str(v)
        return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t
    for r in rows:
        last = (r.get("cards") or [{}])[-1]
        rec = r.get("receipt") or {}
        w.writerow(map(cell, [r.get("job_id"), r.get("state"), r.get("interim"), r.get("ts"), r.get("requester"), r.get("tier"),
                    r.get("task_type") or last.get("task_type"), r.get("request_sha"),
                    "; ".join(f"{c.get('unit')}={c.get('verdict')}" for c in r.get("checks") or []),
                    rec.get("mode"), ",".join(rec.get("caps") or []), r.get("report_sha"),
                    (r.get("cost") or {}).get("calls"), (r.get("cost") or {}).get("money"), r.get("duration_s")]))
    out_dir = Path(ctx.workspace) / "exports"
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        return _block("exports/ in this folder is not a plain folder; nothing was written.")
    out_dir.mkdir(exist_ok=True)
    target = out_dir / f"dynamic-harness-records-{label}.csv"
    if target.is_symlink():
        return _block(f"{target.name} is a symlink; nothing was written.")
    atomic_write(target, buf.getvalue().encode("utf-8"))
    return _block(f"Wrote {len(rows)} decision record rows to exports/{target.name}. Records hold no personal data "
                  "values, only references and hashes.")


# --- config and off (operator) ------------------------------------------------------------------------------------
EDITABLE = ("tiers.json", "gate.json", "egress_allowlist.json", "prices.json", "enforcement.json",
            "decision_policies.json", "probe.json")
FIXED = ("classes.json", "protected_paths.json", "approved_procedures.json", "state_lexicon.json")


def _control_file(name):
    name = name if name.endswith(".json") else f"{name}.json"
    if name in FIXED:
        raise Denied(f"{name} is fixed in v1; it changes only with a release.")
    if name not in EDITABLE:
        raise Denied(f"No control file {name!r}. Files: {', '.join(EDITABLE)}.")
    return name


def _get(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise Denied(f"No key {dotted!r} in that file.")
        cur = cur[part]
    return cur


def _set_control(ctx, name, dotted, value) -> None:
    from . import protected
    doc = json.loads(json.dumps(protected.load_control(name, ctx)))
    cur = doc
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value
    d = Path(ctx.data_root) / "control"
    d.mkdir(parents=True, exist_ok=True)
    if (d / name).is_symlink():
        raise Denied(f"control/{name} is a symlink; nothing was changed")
    atomic_write(d / name, (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    business.baseline(ctx, f"config {name} {dotted}", layers=("control", "gate"))


def _config(ctx, sid, args) -> dict:
    from . import protected
    if len(args) == 2 and args[0] == "show":
        name = _control_file(args[1])
        return _block(json.dumps(protected.load_control(name, ctx), indent=2, ensure_ascii=False))
    if len(args) == 2 and args[0] == "confirm":
        ch = _take(ctx, sid, "config", args[1])
        business.guard(ctx, layers=("control", "gate"))
        _set_control(ctx, ch["file"], ch["key"], ch["value"])
        return _block(f"Done. {ch['sentence']} New sessions and tasks use it.")
    if len(args) >= 4 and args[0] == "set":
        name = _control_file(args[1])
        old = _get(protected.load_control(name, ctx), args[2])
        raw = " ".join(args[3:])
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        if type(value) is not type(old) and not (isinstance(old, float) and type(value) is int):
            raise Denied(f"{args[2]} is a {type(old).__name__}; {raw!r} is not.")
        business.guard(ctx, layers=("control", "gate"))
        sentence = f"Set {args[2]} in {name} from {json.dumps(old, ensure_ascii=False)} to " \
                   f"{json.dumps(value, ensure_ascii=False)}."
        return _offer(ctx, sid, "config", {"file": name, "key": args[2], "value": value, "sentence": sentence},
                      [f"- {sentence}", "(Operator setting: it applies to every task in this harness data folder.)"])
    return _block(f"Usage: {PREFIX}config show <file> | set <file> <key> <value>   files: {', '.join(EDITABLE)}")


def _off(ctx, sid, args) -> dict:
    from . import enforcement
    if len(args) == 2 and args[0] == "confirm":
        ch = _take(ctx, sid, "off", args[1])
        business.guard(ctx, layers=("control", "gate"))
        for comp in enforcement.COMPONENTS:
            _set_control(ctx, "enforcement.json", comp, ch["mode"])
        return _block("Done. " + ch["sentence"])
    if args in ([], ["on"]):
        mode = "enforce" if args == ["on"] else "shadow"
        business.guard(ctx, layers=("control", "gate"))
        sentence = ("The contact cap and the tripwire block again." if mode == "enforce" else
                    "The contact cap and the tripwire only log what they would have blocked. Safety, budgets, "
                    "protected files and the completion check stay on.")
        return _offer(ctx, sid, "off", {"mode": mode, "sentence": sentence}, [f"- {sentence}"])
    return _block(f"Usage: {PREFIX}off | {PREFIX}off on")


HANDLERS = {"business": _business, "memory": _memory, "status": _status, "export": _export, "config": _config,
            "off": _off}


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
