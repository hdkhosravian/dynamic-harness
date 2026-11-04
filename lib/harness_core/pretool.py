"""PreToolUse, in order (spec §Hooks: safety, lanes, contacts; build phase P3). The first deny wins.

1. Pending card: while ``pending_card`` is set, only reads, harness-internal tools (TodoWrite, Skill,
   AskUserQuestion…), a spawn of the shadow drafter, a lone ``harness-card`` / ``harness-compile`` run, and
   a ``Write`` of the card draft itself (a ``.json`` file directly in ``.claude/loops/drafts/``).
2. Lanes (``lanes.on_pre``), for T1/T2 tasks.
3. Contacts: ``AskUserQuestion`` and ``harness-contact <type>`` against the cap and the 50% rule.
4. Safety (``safety.on_pre``): the class table, egress, protected paths, the session gate. It runs last,
   so a call denied above is never recorded as a Bash write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import classes, contacts, lanes, safety, writers

CARD_DRAFTER = "dynamic-harness:card-drafter-2"
DRAFTS = (".claude", "loops", "drafts")


def _card_draft(tool, ti, ctx) -> bool:
    """The card file the skill tells the model to write; no symlink anywhere on the way."""
    f = ti.get("file_path")
    if tool != "Write" or not isinstance(f, str) or not f.endswith(".json") or not getattr(ctx, "workspace", None):
        return False
    want = os.path.join(os.path.realpath(ctx.workspace), *DRAFTS)  # drafts components themselves unresolved
    f = f if os.path.isabs(f) else os.path.join(os.fspath(ctx.workspace), f)
    return os.path.realpath(os.path.dirname(f)) == want and not os.path.islink(f)
SPAWN = ("Agent", "Task")  # Task is the older name of the same tool


def _template(key):
    from . import templates
    return templates.render(key, lang="en")  # a hint to the model, not owner text


def neutral(tool, c) -> bool:
    """A read, or a tool named in ``classes.NEUTRAL_TOOLS`` (no side effect of its own)."""
    return c.cls == "read" or tool in classes.NEUTRAL_TOOLS


def _deny(reason):
    return {"decision": "deny", "reason": reason}


def on_pre(evt, ctx) -> dict:
    if not isinstance(evt, dict) or not isinstance(evt.get("tool_name"), str):
        return safety.on_pre(evt, ctx)
    tool, ti = evt["tool_name"], evt.get("tool_input") if isinstance(evt.get("tool_input"), dict) else {}
    lone = writers._lone_harness(ti.get("command")) if tool == "Bash" else None
    if getattr(ctx, "pending_card", None):
        c = classes.classify(tool, ti, ctx.connector_registry, ctx)
        ok = (neutral(tool, c)
              or (tool in SPAWN and ti.get("subagent_type") == CARD_DRAFTER)
              or (lone is not None and lone[0] in ("card", "compile"))
              or _card_draft(tool, ti, ctx))
        if not ok:
            return _deny(f"harness: {_template('model.pending_card')} ({ctx.pending_card.get('reason', 'card needed')})")
    if tool == "Skill" and (getattr(ctx, "pending_card", None) or contacts.tier(ctx) in ("T1", "T2")):
        from .session import approved_skill
        if not approved_skill(ctx, ti.get("skill")):
            return _deny(f"harness: only approved dynamic-harness skills load in a task (R-5), not {ti.get('skill')!r}")
    if getattr(ctx, "job", None):
        from . import budget
        from .events import EventLog
        from .paths import job_paths
        jp = job_paths(ctx.data_root, ctx.workspace, ctx.job)
        stopped = budget.latest_stop(EventLog(jp.run, jp.heads)) if jp.run.is_dir() else None
        if stopped == "PAUSED" and not neutral(tool, classes.classify(tool, ti, ctx.connector_registry, ctx)):
            return _deny(f"harness: task {ctx.job} is paused until the owner replies; only reads run")
        if stopped in ("STOPPED_BUDGET", "STOPPED_COST", "STOPPED_STALL"):
            c = classes.classify(tool, ti, ctx.connector_registry, ctx)
            if not (neutral(tool, c) or (lone is not None and lone[0] == "contact" and lone[1][:2] == ["cost", "--accept"])):
                return _deny(f"harness: task {ctx.job} is {stopped}: only reads and harness-contact cost --accept "
                             "until it resumes")
        from . import lease
        if lease.status(ctx, ctx.job, evt.get("session_id")) in ("other", "stale"):
            c = classes.classify(tool, ti, ctx.connector_registry, ctx)
            if not neutral(tool, c):
                return _deny(f"harness: task {ctx.job} is held by another session; this session may only read it")
    r = lanes.on_pre(evt, ctx)
    if r:
        return r
    why = None
    if tool == "AskUserQuestion":
        qs = ti.get("questions")
        why = contacts.check("ask", ctx, questions=len(qs) if isinstance(qs, list) else 99)
    elif lone is not None and lone[0] == "contact" and lone[1] and "--accept" not in lone[1]:
        why = contacts.check(lone[1][0], ctx, handoff="--handoff" in lone[1])
    if why:
        return _deny(f"harness: {why}")
    return safety.on_pre(evt, ctx)
