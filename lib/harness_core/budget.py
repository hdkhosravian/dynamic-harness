"""Budget, stall and cost stops (spec §Budget and failure handling; build phase P4).

``charge(evt, ctx)`` runs in PostToolUse for every tool call in a T1/T2 task (subagents included):
``budget.json`` ``calls + 1`` (a ``BUDGET_CHARGE`` event). Then, in order:

- calls above ``calls_cap`` (T1 150, T2 60 per unit, plus approved increments), or a T1 task older than
  30 minutes → ``STOPPED_BUDGET``;
- no new unit verdict in ``STALL_CALLS`` (40) calls → ``STOPPED_STALL``;
- estimated spend above the owner's task cap, or this month's recorded spend plus this task's above the
  monthly cap → ``STOPPED_COST``. The estimate is the transcript's token usage since the task started ×
  ``control/prices.json``, converted with the profile's ``fx.<currency>`` when the price table is in another
  currency; with no rate the money caps stay off. The estimate is kept in ``budget.json`` ``money``, which the
  decision record carries.

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


def _template(key, ctx=None, **kw):
    from . import templates
    return templates.render(key, ctx, **kw)


def _verdicts(log) -> int:
    """Units whose latest verdict is terminal: a repeated BLOCKED on one unit is not progress."""
    from .lease import TERMINAL
    latest = {}
    for e in log.events():
        if e["type"] == "ROW" and e["payload"].get("kind") in ROW_SCHEMA:
            latest[e["payload"]["row"]["id"]] = e["payload"]["row"].get("verdict")
    return sum(1 for v in latest.values() if v in TERMINAL)


def rate(ctx):
    """(price table, multiplier from its currency to the owner's), or (None, None) when money cannot be priced:
    the owner's caps need a currency, and a foreign price table needs ``fx.<currency>`` in the profile."""
    prof = sessionstate_business(ctx)
    cur = (prof.get("caps") or {}).get("currency") or prof.get("currency")
    try:
        prices = protected.load_control("prices.json", ctx)
    except Exception:  # no price table: the money caps stay off, the call caps still apply
        return None, None
    if not cur or not isinstance(prices.get("per_mtok"), dict):
        return None, None
    if prices.get("currency") == cur:
        return prices, 1.0
    fx = (prof.get("fx") or {}).get(prices.get("currency"))
    return (prices, float(fx)) if isinstance(fx, (int, float)) and fx > 0 else (None, None)


def _spend(ctx, transcript, since) -> float:
    """Estimated money spent since ``since`` (epoch seconds), in the owner's currency, or None when it cannot be
    estimated (no price table, no currency rate, no transcript)."""
    prices, mult = rate(ctx)
    if prices is None:
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
    return (tin / 1e6 * float(per.get("input", 0)) + tout / 1e6 * float(per.get("output", 0))) * mult


def month_spent(ctx, exclude=None) -> float:
    """Spend recorded this calendar month by other tasks (their latest decision-record row)."""
    import glob
    import json as _json
    month = time.strftime("%Y-%m")
    total = 0.0
    for f in glob.glob(str(Path(ctx.data_root) / "records" / "*.jsonl")):
        if exclude and Path(f).stem == exclude:
            continue
        try:
            rows = [_json.loads(x) for x in Path(f).read_text("utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            continue
        last = rows[-1] if rows else {}
        money = (last.get("cost") or {}).get("money")
        if str(last.get("ts", ""))[:7] == month and isinstance(money, (int, float)):
            total += money
    return total


def sessionstate_business(ctx) -> dict:
    f = Path(ctx.data_root) / "business" / "profile.json"
    try:
        v = json.loads(f.read_text("utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def stop(ctx, job, state, reason, sid=None, calls=None, amount=None):
    """The stop message, or None when another hook stopped the task first."""
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    with log.lock():
        if latest_stop(log):
            return None
        log.append("STOPPED", {"state": state, "reason": reason})
    sessionstate.set_job(ctx, job, state=state)
    record.write(ctx, job, state, interim=True)
    from .lease import remaining
    from .ledger import Ledger
    total = len(Ledger(p, log).sanctioned_scope())
    done = total - len(remaining(ctx, job))
    if state in ("STOPPED_BUDGET", "STOPPED_COST"):
        from . import templates
        lang = templates.language(ctx)
        cur = sessionstate_business(ctx).get("caps", {}).get("currency", "")
        more = (templates.render("more.money", ctx, lang, amount=round(amount, 2), currency=templates.currency(cur, lang))
                if amount else templates.render("more.calls", ctx, lang, n=calls))
        from .turns import Turns
        seq = (Turns(ctx.data_root).latest() or {}).get("seq", 0)  # only a later reply can accept it
        row = contacts.record(ctx, "cost", "", recheck=False, extra={"calls": calls, "amount": amount, "turn_seq": seq})
        if sid:
            sessionstate.update(ctx, sid, pending_contact_id=row["contact_id"])
        return _template("stopped_budget", ctx, done=done, total=total, more=more)
    return _template("stopped_stall", ctx)  # the reason stays in the record: it names internal steps


def charge(evt, ctx) -> dict:
    job = getattr(ctx, "job", None)
    if not job or contacts.tier(ctx) not in ("T1", "T2"):
        return {}
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    if latest_stop(log):
        return {}
    tier = store.read_json(p.run, log, "tier.json", None) or {}
    started = calendar.timegm(time.strptime(tier["ts"][:19], "%Y-%m-%dT%H:%M:%S")) if tier.get("ts") else time.time()
    spent = _spend(ctx, evt.get("transcript_path"), started)
    with log.lock():
        b = store.read_json(p.run, log, "budget.json", None) or {}
        b = dict(b, calls=int(b.get("calls") or 0) + 1)
        if spent is not None:
            b["money"] = round(spent, 2)
        seen = _verdicts(log)
        if seen != b.get("verdicts_seen", 0):
            b.update(verdicts_seen=seen, calls_at_verdict=b["calls"])
        store.write_json(p.run, log, "budget.json", b, "BUDGET_CHARGE", {"calls": b["calls"]})
    sid = evt.get("session_id")
    cap = b.get("calls_cap")
    if isinstance(cap, int) and b["calls"] > cap:
        return _block(stop(ctx, job, "STOPPED_BUDGET", f"{b['calls']} tool calls (cap {cap})", sid,
                           calls=max(cap // 2, 20)))
    if tier.get("tier") == "T1" and time.time() - started > T1_SECONDS + float(b.get("seconds_increment") or 0):
        return _block(stop(ctx, job, "STOPPED_BUDGET", "T1 time budget (30 minutes)", sid, calls=max((cap or 150) // 2, 20)))
    if b["calls"] - int(b.get("calls_at_verdict") or 0) >= STALL_CALLS:
        return _block(stop(ctx, job, "STOPPED_STALL", f"no new verdict in {STALL_CALLS} calls", sid))
    caps = sessionstate_business(ctx).get("caps") or {}
    extra = float(b.get("money_increment") or 0)
    if spent is not None and isinstance(caps.get("task"), (int, float)) and spent > float(caps["task"]) + extra:
        return _block(stop(ctx, job, "STOPPED_COST", f"estimated spend {spent:.2f} over the task cap", sid,
                           amount=round(spent * 0.5, 2)))
    if spent is not None and isinstance(caps.get("month"), (int, float)) and \
            month_spent(ctx, exclude=job) + spent > float(caps["month"]) + extra:
        return _block(stop(ctx, job, "STOPPED_COST", "estimated spend over the monthly cap", sid,
                           amount=round(max(spent * 0.5, 1.0), 2)))
    return {}


def _block(msg):
    if msg is None:
        return {}
    # the owner sees the cost question as rendered here, not the model's retelling of it
    return {"decision": "block", "reason": msg, "systemMessage": msg}
