"""Budget, stall and cost stops (spec §Budget and failure handling; build phase P4).

``charge(evt, ctx)`` runs in PostToolUse for every tool call in a T1/T2 task (subagents included):
``budget.json`` ``calls + 1`` (a ``BUDGET_CHARGE`` event). Then, in order:

- calls above ``calls_cap`` (T1 150, T2 60 per unit, plus approved increments), or a T1 task older than
  30 minutes → ``STOPPED_BUDGET``;
- no new unit verdict in ``STALL_CALLS`` (40) calls → ``STOPPED_STALL``;
- estimated spend above the owner's task cap → ``STOPPED_COST``. The estimate is the transcript's token
  usage since the task started × ``control/prices.json``; it is active only when the business profile has
  ``caps.task`` in the price table's currency (the business layer arrives in P5).

A stop appends ``STOPPED{state}``, marks the jobs index, writes an interim decision record, and for budget
and cost stops records an uncounted ``cost`` contact that the owner answers with ``harness-contact cost
--accept``. ``latest_stop(log)`` is the latest stop record with no ``RESUMED`` after it.
"""
from __future__ import annotations

import calendar
import json
import time
from pathlib import Path

from . import contacts, protected, record, sessionstate, store
from .events import EventLog
from .ledger import ROW_SCHEMA
from .paths import job_paths

STALL_CALLS = 40
T1_SECONDS = 30 * 60
STOPS = ("STOPPED_BUDGET", "STOPPED_COST", "STOPPED_STALL", "PAUSED")


def latest_stop(log):
    state = None
    for e in log.events():
        if e["type"] == "STOPPED" and e["payload"].get("state") in STOPS:
            state = e["payload"]["state"]
        elif e["type"] == "PAUSED":
            state = state or "PAUSED"  # a pause never masks a budget, cost or stall stop
        elif e["type"] == "RESUMED":
            state = None
    return state


def _template(key, **kw):
    t = json.loads((Path(__file__).resolve().parents[2] / "templates" / "en.json").read_text("utf-8")).get(key, key)
    return t.format(**kw)


def _verdicts(log) -> int:
    return sum(1 for e in log.events() if e["type"] == "ROW" and e["payload"].get("kind") in ROW_SCHEMA)


def _spend(ctx, transcript, since) -> float:
    """Estimated money spent since ``since`` (epoch seconds), or None when it cannot be estimated."""
    caps = (sessionstate_business(ctx).get("caps") or {})
    prices = protected.load_control("prices.json", ctx) if (Path(ctx.data_root) / "control" / "prices.json").is_file() \
        else None
    if not prices or not isinstance(caps.get("task"), (int, float)) or prices.get("currency") != caps.get("currency"):
        return None
    if not isinstance(transcript, str) or not Path(transcript).is_file():
        return None
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(since))
    tin = tout = 0
    # shortcut: rereads the whole transcript per call; keep a byte offset in budget.json if transcripts grow large
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            u = (e.get("message") or {}).get("usage") if isinstance(e, dict) else None
            if not isinstance(u, dict) or str(e.get("timestamp", ""))[:19] < since_iso:
                continue
            tin += sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens"))
            tout += int(u.get("output_tokens") or 0)
    per = prices.get("per_mtok") or {}
    return tin / 1e6 * float(per.get("input", 0)) + tout / 1e6 * float(per.get("output", 0))


def sessionstate_business(ctx) -> dict:
    f = Path(ctx.data_root) / "business" / "profile.json"
    try:
        v = json.loads(f.read_text("utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def stop(ctx, job, state, reason, sid=None, calls=None, amount=None) -> str:
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    log.append("STOPPED", {"state": state, "reason": reason})
    sessionstate.set_job(ctx, job, state=state)
    record.write(ctx, job, state, interim=True)
    from .lease import remaining
    from .ledger import Ledger
    total = len(Ledger(p, log).sanctioned_scope())
    done = total - len(remaining(ctx, job))
    if state in ("STOPPED_BUDGET", "STOPPED_COST"):
        more = f"{amount:.2f} {sessionstate_business(ctx).get('caps', {}).get('currency', '')}".strip() if amount \
            else f"{calls} more tool calls"
        from .turns import Turns
        seq = (Turns(ctx.data_root).latest() or {}).get("seq", 0)  # only a later reply can accept it
        row = contacts.record(ctx, "cost", "", recheck=False, extra={"calls": calls, "amount": amount, "turn_seq": seq})
        if sid:
            sessionstate.update(ctx, sid, pending_contact_id=row["contact_id"])
        return _template("stopped_budget", done=done, total=total, more=more)
    return _template("stopped_stall", what=job, why=reason)


def charge(evt, ctx) -> dict:
    job = getattr(ctx, "job", None)
    if not job or contacts.tier(ctx) not in ("T1", "T2"):
        return {}
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    if latest_stop(log):
        return {}
    with log.lock():
        b = store.read_json(p.run, log, "budget.json", None) or {}
        b = dict(b, calls=int(b.get("calls") or 0) + 1)
        seen = _verdicts(log)
        if seen != b.get("verdicts_seen"):
            b.update(verdicts_seen=seen, calls_at_verdict=b["calls"])
        store.write_json(p.run, log, "budget.json", b, "BUDGET_CHARGE", {"calls": b["calls"]})
    tier = store.read_json(p.run, log, "tier.json", None) or {}
    started = calendar.timegm(time.strptime(tier["ts"][:19], "%Y-%m-%dT%H:%M:%S")) if tier.get("ts") else time.time()
    sid = evt.get("session_id")
    cap = b.get("calls_cap")
    if isinstance(cap, int) and b["calls"] > cap:
        return _block(stop(ctx, job, "STOPPED_BUDGET", f"{b['calls']} tool calls (cap {cap})", sid,
                           calls=max(cap // 2, 20)))
    if tier.get("tier") == "T1" and time.time() - started > T1_SECONDS + float(b.get("seconds_increment") or 0):
        return _block(stop(ctx, job, "STOPPED_BUDGET", "T1 time budget (30 minutes)", sid, calls=max((cap or 150) // 2, 20)))
    if b["calls"] - int(b.get("calls_at_verdict") or 0) >= STALL_CALLS:
        return _block(stop(ctx, job, "STOPPED_STALL", f"no new verdict in {STALL_CALLS} calls", sid))
    spent = _spend(ctx, evt.get("transcript_path"), started)
    caps = sessionstate_business(ctx).get("caps") or {}
    if spent is not None and spent > float(caps["task"]) + float(b.get("money_increment") or 0):
        return _block(stop(ctx, job, "STOPPED_COST", f"estimated spend {spent:.2f} over the task cap", sid,
                           amount=round(spent * 0.5, 2)))
    return {}


def _block(msg):
    return {"decision": "block", "reason": msg}
