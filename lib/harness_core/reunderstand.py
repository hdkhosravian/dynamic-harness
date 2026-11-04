"""RE_UNDERSTAND: a corrected card for the open task (spec §State machine "Re-understand"; build phase P4).

``harness-compile --card FILE --reunderstand <turn_id>`` reaches ``compile.apply``, which hands it here:

- the open task of this session (lease owner, not stopped), at most one RE_UNDERSTAND per task;
- the cited turn is a correction the prompt hook recorded, after the latest scope freeze;
- card v(n+1) keeps the owner's original request; the tier only goes up;
- scope v(n+1): a unit identical to a current one (same oracle) keeps its id and verdicts; every other unit
  gets a new id, so nothing carries over unverified. Units not kept are ``DESCOPED_BY_USER`` citing the turn;
- the start-question allowance resets only when the turn names a changed unit;
- the rework notice is a statement: "This changes X of Y items; applying now."
"""
from __future__ import annotations

from . import budget, card as cardmod, contacts, corrections, lease, schema, sessionstate, store, tier_select
from .card import CardError
from .errors import Denied
from .events import EventLog
from .ledger import Ledger
from .paths import job_paths
from .record import _cards
from .turns import Turns, _names

UNIT_KEYS = ("label", "claim", "check_id", "deliverable")


def _same(a, b) -> bool:
    return all(a.get(k) == b.get(k) for k in UNIT_KEYS)


def apply(req, ctx, evt, budget_of) -> dict:
    sid = evt.get("session_id")
    st = sessionstate.load(ctx, sid)
    job = st.get("active_task")
    if not job or st.get("prompt_scope") != "active_task" or sessionstate.jobs(ctx).get(job, {}).get("terminal"):
        raise Denied("re-understanding needs this session's open task")
    lease.require(ctx, job, sid)
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    if budget.latest_stop(log):
        raise Denied(f"task {job} is {budget.latest_stop(log)}; resume it first")
    if any(e["type"] == "RE_UNDERSTAND" for e in log.events()):
        raise Denied("this task was already re-understood once; start a new task for a further change")
    turn_id = req["reunderstand"]
    if turn_id not in {r["turn_id"] for r in corrections.rows(ctx, job)}:
        raise Denied(f"turn {turn_id} is not a recorded correction of task {job}")
    led = Ledger(p, log)
    freeze = [e for e in log.events() if e["type"] == "SCOPE"][-1]
    turn = Turns(ctx.data_root).cite(turn_id, after_seq=freeze["payload"]["turn_seq"])
    cards = _cards(p, log)
    old = cards[-1]
    probe = st.get("probe") or {}
    c = cardmod.normalize(req.get("card"), probe, ctx, request=old["request"])
    c["multi_session"] = cardmod.multi_session(c)
    c.setdefault("follows", old.get("follows"))
    c["scenario_of"] = old.get("scenario_of")
    old_tier = (store.read_json(p.run, log, "tier.json", None) or {}).get("tier", "T2")
    tier = tier_select.select_tier(c, dict(probe, floor_tier=old_tier), ctx=ctx,
                                   stored_tier=lambda j: sessionstate.tier_of(ctx, j))
    if tier == tier_select.REFUSE:
        raise CardError(["the corrected card falls in a refused decision class; it is not applied"])
    tier = tier_select.tier_max(tier, old_tier)
    version = len(cards) + 1
    c.update(job_id=job, version=version, business_hash=old.get("business_hash"), versions=old.get("versions"))
    schema.check(c, "card")
    current = led.sanctioned_scope()
    same_oracle = c.get("oracle") == old.get("oracle")
    n = max([int(u["id"][1:]) for u in current if u["id"][1:].isdigit()] + [0])
    rows, kept = [], set()
    for u in c["unit_list"]:
        match = next((x for x in current if same_oracle and x["id"] not in kept and _same(x, u)), None)
        if match:
            kept.add(match["id"])
            rows.append(match)
        else:
            n += 1
            rows.append(dict(u, id=f"u{n}"))
    removed = [u["id"] for u in current if u["id"] not in kept]
    changed = len(rows) - len(kept) + len(removed)
    with log.lock():
        store.write_json(p.run, log, f"card.v{version}.json", c, "CARD", {"version": version, "task_type": c["task_type"]})
        if tier != old_tier:
            t = store.read_json(p.run, log, "tier.json")
            store.write_json(p.run, log, "tier.json", dict(t, tier=tier, prev_tier=old_tier), "TIER", {"tier": tier})
        for uid in removed:
            log.append("DESCOPE", {"unit": uid, "turn_id": turn["turn_id"], "version": len(led._scope_events()) + 1})
        led._write_scope(rows, Turns(ctx.data_root), removed=removed)
        b = store.read_json(p.run, log, "budget.json")
        cap = budget_of(ctx, tier, c["units"])["calls_cap"]
        store.write_json(p.run, log, "budget.json", dict(b, tier=tier, calls_cap=max(int(b.get("calls_cap") or 0), cap)),
                         "BUDGET", {"tier": tier, "reunderstand": True})
        log.append("RE_UNDERSTAND", {"turn_id": turn["turn_id"], "version": version, "changed": changed,
                                     "total": len(rows), "removed": removed, "tier": tier})
    labels = [r["label"] for r in rows if r["id"] not in kept] + [u["label"] for u in current if u["id"] in removed]
    if any(lbl and _names(turn["text_norm"], lbl) for lbl in labels):
        cs = contacts.state(ctx)
        store.write_json(p.run, log, "contacts.json", dict(cs, start_used=False), "CONTACT_UPDATE",
                         {"start_reset": True})
    sessionstate.set_job(ctx, job, tier=tier, task_type=c["task_type"])
    notice = f"This changes {changed} of {max(len(rows), len(current))} items; applying now."
    return {"job_id": job, "state": "EXECUTING", "tier": tier, "reunderstood": True, "version": version,
            "notice": notice, "removed": removed}
