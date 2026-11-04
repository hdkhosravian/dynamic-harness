"""Blocking contacts: the cap (R-7) and late questions (R-8) (spec §Turns and contacts; §Communication
contract; build phase P3).

``check(kind, ctx, questions=0, handoff=False)`` returns a deny reason or None. It is used by the PreToolUse
contacts hook (for ``AskUserQuestion`` and ``harness-contact <type>``) and again by ``contact.apply``.

- Only tasks at T1/T2 are counted (no active task, or a T0 task: nothing to count).
- Cap: at most ``contact_cap`` (5) counted contacts per task. ``decision_ack`` and ``cost`` are exempt.
  At the cap, one ``--handoff`` per task is allowed, uncounted, for a before-spend type only.
- Start questions: while progress is 0, ``AskUserQuestion`` may be used once, with at most
  ``start_questions`` (3) questions.
- After 50% progress, ``AskUserQuestion`` and ``question`` contacts are denied; the agent records an
  assumption instead. Before-spend types, ``decision_ack`` and ``cost`` are exempt.
- Progress: terminal verdicts / units when the frozen scope has more than one unit; otherwise milestones
  (outline 25%, draft 50%, final 100%).

``record_ask(evt, ctx)`` (PostToolUse on ``AskUserQuestion``) counts the call.
"""
from __future__ import annotations

from . import protected, store
from .events import EventLog, now
from .ledger import Ledger
from .paths import job_paths

EXEMPT_CAP = ("decision_ack", "cost")
BEFORE_SPEND = ("plan", "assumption_sheet", "definition_confirm")
TYPES = ("question",) + BEFORE_SPEND + EXEMPT_CAP
TERMINAL = ("PASS", "PASS_FLAGGED", "BLOCKED", "DESCOPED_BY_USER")
MILESTONE = {"outline": 0.25, "draft": 0.5, "final": 1.0}


def _job(ctx):
    job = getattr(ctx, "job", None)
    if not job:
        return None, None, None
    p = job_paths(ctx.data_root, ctx.workspace, job)
    return job, p, EventLog(p.run, p.heads)


def tier(ctx):
    job, p, log = _job(ctx)
    if not job:
        return None
    t = store.read_json(p.run, log, "tier.json", None)
    return t.get("tier") if isinstance(t, dict) else None


def state(ctx) -> dict:
    job, p, log = _job(ctx)
    return store.read_json(p.run, log, "contacts.json", None) or {"count": 0, "start_used": False,
                                                                  "handoff_used": False, "rows": []}


def progress(ctx) -> float:
    job, p, log = _job(ctx)
    led = Ledger(p, log)
    units = led.scope()
    if len(units) > 1:
        return sum(led.state(u["id"]) in TERMINAL for u in units) / len(units)
    reached = [MILESTONE.get(r.get("milestone"), 0) for r in store.read_jsonl(p.run, log, "milestones.jsonl")]
    if units and led.state(units[0]["id"]) in TERMINAL:
        reached.append(1.0)
    return max(reached, default=0.0)


def check(kind, ctx, questions=0, handoff=False):
    t = tier(ctx)
    if t not in ("T1", "T2"):
        return None
    lim = protected.load_control("tiers.json", ctx)
    st, prog = state(ctx), progress(ctx)
    if kind == "ask":
        if prog >= 0.5:
            return ("progress is past 50%: questions are no longer asked; record an assumption with the suggested "
                    "option instead (R-8)")
        if prog == 0 and st.get("start_used"):
            return "start questions go in one message; record an assumption instead"
        if prog == 0 and questions > int(lim["start_questions"]):
            return f"at most {lim['start_questions']} start questions, in one message"
    elif kind not in TYPES:
        return f"unknown contact type {kind!r}"
    elif kind == "question" and prog >= 0.5:
        return "progress is past 50%: record an assumption instead of asking (R-8)"
    if kind in EXEMPT_CAP:
        return None
    if st.get("count", 0) >= int(lim["contact_cap"]):
        if handoff and kind in BEFORE_SPEND and not st.get("handoff_used"):
            return None
        return (f"the contact cap ({lim['contact_cap']} per task) is reached; record an assumption, or use one "
                "--handoff for a pending before-spend contact (R-7)")
    return None


def record(ctx, kind, text_sha, handoff=False, extra=None) -> dict:
    job, p, log = _job(ctx)
    with log.lock():
        st = state(ctx)
        counted = kind not in EXEMPT_CAP and not (handoff and st.get("count", 0) >= int(
            protected.load_control("tiers.json", ctx)["contact_cap"]))
        row = {"contact_id": f"c{len(st.get('rows', [])) + 1}", "type": kind, "ts": now(), "text_sha": text_sha,
               "counted": counted, "handoff": bool(handoff), **(extra or {})}
        st = dict(st, count=st.get("count", 0) + int(counted), rows=list(st.get("rows", [])) + [row])
        if kind == "question" and progress(ctx) == 0 and extra and extra.get("ask"):
            st["start_used"] = True
        if handoff:
            st["handoff_used"] = True
        store.write_json(p.run, log, "contacts.json", st, "CONTACT", {"contact_id": row["contact_id"], "type": kind,
                                                                      "counted": counted})
        return row


def record_ask(evt, ctx):
    if tier(ctx) not in ("T1", "T2"):
        return None
    from . import canon
    qs = (evt.get("tool_input") or {}).get("questions")
    return record(ctx, "question", canon.sha256_json(qs if isinstance(qs, list) else []),
                  extra={"ask": True, "questions": len(qs) if isinstance(qs, list) else 0})
