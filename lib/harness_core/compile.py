"""``compile.apply``: the sole writer of card, tier, scope, opening line, budget, contact counter and the
session's ``active_task`` (spec §Trust boundaries writers row; §Data contracts compiler output; P3).

Called by the PostToolUse writer for ``harness-compile`` with the request the CLI printed. Everything the
model wrote is re-validated; everything code owns is recomputed here:

1. The session must have a passing SessionStart status and a recorded prompt (``sessions/<sid>.json``
   ``probe`` and ``request_turn``). The card's ``request`` is that owner turn's text, never the model's.
2. ``card.normalize`` applies the field rules; ``multi_session`` comes from ``card.multi_session``;
   ``resume_of`` must name a terminal job.
3. ``tier_select.select_tier`` with the floor = max(probe floor, pending-card floor). REFUSE writes the card
   and a ``STOPPED{REFUSED}`` event and nothing else.
4. A tier above T0 needs a gate profile (``gate.json``: ``<tier>.<task_type>`` or ``<tier>.default``).
5. Writes ``card.v1.json``, ``tier.json``, the frozen scope (``Ledger.freeze_scope``), ``opening.txt``,
   ``budget.json``, ``contacts.json``, ``state.json`` and the workspace's ``work/`` and ``requests/``;
   sets ``active_task``, ``prompt_scope = active_task`` and clears ``pending_card``; adds the job to the
   jobs index. A second draft is logged in shadow mode.

Re-compiling the open job (``reunderstand``: a new card version) is ``reunderstand.apply``.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from . import card as cardmod
from . import memory, protected, sessionstate, shadow, store, tier_select
from .card import CardError
from .errors import Denied
from .events import EventLog, now
from .ledger import Ledger
from .paths import job_paths
from .turns import Turns

__all__ = ["apply", "CardError"]


def _template(key, ctx, **kw) -> str:
    from . import templates
    return templates.render(key, ctx, **kw)


def _job_id(c) -> str:
    words = re.findall(r"[a-z0-9]+", c["deliverable"]["description"].lower())
    slug = "-".join(words)[:24].strip("-") or "task"
    return f"{time.strftime('%Y-%m-%d')}-{slug}-{secrets.token_hex(3)}"


def _core_version(ctx) -> str:
    try:
        return json.loads((Path(ctx.plugin_root) / ".claude-plugin" / "plugin.json").read_text("utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def _profile(ctx, tier, task_type):
    profiles = protected.load_control("gate.json", ctx).get("profiles", {})
    return profiles.get(f"{tier}.{task_type}") or profiles.get(f"{tier}.default")


def _budget(ctx, tier, units) -> dict:
    t = protected.load_control("tiers.json", ctx)
    cap = None if tier == "T0" else int(t["t1_budget_calls"]) if tier == "T1" else 60 * units
    return {"schema": "harness/budget@1", "tier": tier, "calls_cap": cap, "calls": 0}


def _opening(ctx, c, tier, job) -> str:
    line = _template(f"opening.{tier}", ctx, deliverable=c["deliverable"]["description"])
    from . import business, estimate
    est = estimate.estimate(ctx, tier) if tier in ("T1", "T2") else None
    if est:
        from . import templates
        cur = business.profile(ctx).get("currency")
        lang = templates.language(ctx)
        money = (templates.render("opening.estimate_money", ctx, lang, lo=est["money"][0], hi=est["money"][1],
                                  currency=templates.currency(cur, lang)) if est["money"] and cur else "")
        line += " " + _template("opening.estimate", ctx, lo=est["minutes"][0], hi=est["minutes"][1], money=money)
    prefs = memory.effective(ctx, job)
    if prefs:
        line += " " + _template("opening.prefs", ctx, prefs=", ".join(f"{k} {v}" for k, v in sorted(prefs.items())))
    return line


def apply(req, ctx, evt, scenario_parent=None, job_id=None) -> dict:
    """``scenario_parent`` / ``job_id``: only ``harness-model scenario`` passes them, in-process; a CLI request
    never can (the writers dispatcher calls ``apply(req, ctx, evt)``)."""
    from . import session

    sid = evt.get("session_id")
    if ctx.session_ok is not True:
        raise Denied("compile refuses: this session has no passing SessionStart status")
    if not sid:
        raise Denied("compile refuses: no session id")
    st = sessionstate.load(ctx, sid)
    p, turn_id = st.get("probe"), st.get("request_turn")
    if not isinstance(p, dict) or not turn_id:
        raise Denied("compile refuses: no owner prompt is recorded for this session")
    if req.get("reunderstand"):
        from . import reunderstand
        return reunderstand.apply(req, ctx, evt, _budget)
    if turn_id in (st.get("compiled_turns") or []):
        raise Denied("this prompt was already compiled into a job; one owner prompt makes one job")
    open_job = st.get("active_task")
    open_row = sessionstate.jobs(ctx).get(open_job, {}) if open_job else {}
    if open_job and not open_row.get("terminal") and st.get("prompt_scope") == "active_task":
        raise Denied(f"this prompt belongs to the open task {open_job}; changing that task's card is "
                     "re-understanding: harness-compile --card FILE --reunderstand <correction turn>")
    turn = Turns(ctx.data_root).cite(turn_id)
    draft = req.get("card")
    c = cardmod.normalize(draft, p, ctx, request=turn["text_norm"])
    if c.get("resume_of"):
        parent = sessionstate.jobs(ctx).get(c["resume_of"])
        if not parent or not parent.get("terminal"):
            raise CardError([f"resume_of {c['resume_of']!r} is not a terminal job; a non-terminal job is resumed, "
                             "not recompiled"])
    c["multi_session"] = cardmod.multi_session(c)
    from .stopgate import lexicon_hits
    words = lexicon_hits(ctx, c["deliverable"]["description"])
    if words:  # the opening line quotes the description, and the model prints it verbatim
        raise CardError([f"deliverable.description may not use state words {words}"])
    c.setdefault("follows", None)
    if c["follows"] and not sessionstate.jobs(ctx).get(c["follows"], {}).get("terminal"):
        raise CardError([f"follows {c['follows']!r} is not a finished task; an open task is continued, not followed"])
    c["scenario_of"] = scenario_parent  # set only by harness-model scenario, in-process
    pending = st.get("pending_card") or {}
    floor = tier_select.tier_max(p.get("floor_tier", "T2"), pending.get("floor", "T0"))
    tier = tier_select.select_tier(c, dict(p, floor_tier=floor), stored_tier=lambda j: sessionstate.tier_of(ctx, j),
                                   ctx=ctx)
    if tier != tier_select.REFUSE and tier != "T0" and not _profile(ctx, tier, c["task_type"]):
        raise CardError([f"no gate profile for {tier}.{c['task_type']} (or {tier}.default): the card is refused"])
    job = job_id or _job_id(c)
    paths = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(paths.run, paths.heads)
    hashes = session.current_hashes(ctx)
    c.update(job_id=job, version=1, business_hash=hashes.get("business", "absent"),
             versions={"core": _core_version(ctx), "control_hash": hashes.get("control", "absent"),
                       "memory_snapshot": memory.snapshot(ctx, job)})
    from . import schema
    schema.check(c, "card")
    paths.run.mkdir(parents=True, exist_ok=True)
    store.write_json(paths.run, log, "card.v1.json", c, "CARD", {"version": 1, "task_type": c["task_type"]})
    out = {"job_id": job}
    if tier == tier_select.REFUSE:
        log.append("STOPPED", {"state": "REFUSED", "reason": f"decision class {c['decision_class']} is refused"})
        from . import record
        record.write(ctx, job, "REFUSED")  # R-15: every terminal task
        sessionstate.set_job(ctx, job, state="REFUSED", tier=None, task_type=c["task_type"], session_id=sid)
        # a refusal never lifts the gate: the model may raise decision_class, so it could refuse on purpose
        keep = pending or {"floor": "T2", "reason": "the request was refused", "turn_id": turn_id}
        sessionstate.update(ctx, sid, pending_card=keep, active_task=open_job if not open_row.get("terminal") else None,
                            prompt_scope="new", compiled_turns=(list(st.get("compiled_turns") or []) + [turn_id])[-50:])
        return dict(out, state="REFUSED", tier=None, message=_template("refused", ctx))
    store.write_json(paths.run, log, "tier.json",
                     {"schema": "harness/tier@1", "job_id": job, "session_id": sid, "tier": tier,
                      "v_default": "V0" if tier == "T0" else "V2", "set_by": "compiler", "ts": now(), "prev_tier": None},
                     "TIER", {"tier": tier})
    from . import lease
    lease.acquire(ctx, job, sid)
    rows = [dict(u, id=f"u{i}") for i, u in enumerate(c["unit_list"], 1)]
    Ledger(paths, log).freeze_scope(rows, Turns(ctx.data_root))
    opening = _opening(ctx, c, tier, job)
    store.write_json(paths.run, log, "budget.json", _budget(ctx, tier, c["units"]), "BUDGET", {"tier": tier})
    store.write_json(paths.run, log, "contacts.json",
                     {"schema": "harness/contacts@1", "count": 0, "start_used": False, "handoff_used": False, "rows": []},
                     "CONTACTS", {"count": 0})
    store.write_json(paths.run, log, "state.json", {"schema": "harness/state@1", "job_id": job, "state": "EXECUTING",
                                                     "tier": tier}, "STATE", {"state": "EXECUTING"})
    from .safeio import atomic_write
    atomic_write(paths.run / "opening.txt", opening.encode("utf-8"))
    log.append("OPENING", {"len": len(opening)}, file="opening.txt",
               file_sha=__import__("hashlib").sha256(opening.encode("utf-8")).hexdigest())
    paths.work.mkdir(parents=True, exist_ok=True)
    paths.requests.mkdir(parents=True, exist_ok=True)
    if open_job and not open_row.get("terminal") and tier != "T0":  # a new task at T1+ pauses the open one
        op = job_paths(ctx.data_root, ctx.workspace, open_job)
        EventLog(op.run, op.heads).append("PAUSED", {"reason": "handoff", "by": job})
        sessionstate.set_job(ctx, open_job, state="PAUSED")
    sessionstate.set_job(ctx, job, state="EXECUTING", tier=tier, task_type=c["task_type"], session_id=sid)
    sessionstate.update(ctx, sid, active_task=job, prompt_scope="active_task", pending_card=None,
                        compiled_turns=(list(st.get("compiled_turns") or []) + [turn_id])[-50:])
    if isinstance(req.get("second_draft"), dict):
        out["shadow"] = shadow.log_second_draft(ctx, job, draft, req["second_draft"], p)
    return dict(out, state="EXECUTING", tier=tier, opening=opening)
