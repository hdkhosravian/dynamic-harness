"""PostToolUse, in order (spec §Hooks: writers, tripwire; build phase P3).

1. Written files (``written.on_post``) for Write / Edit / MultiEdit / NotebookEdit.
2. The spawn link for ``Agent`` (``lanes.link_spawn``).
3. The writers (``writers.on_post``): harness-compile, harness-contact, AskUserQuestion.
4. The tripwire, on the session state as the writers left it. The harness CLIs themselves never trip it.
A writer's refusal wins over the tripwire.
"""
from __future__ import annotations

from . import lanes, sessionstate, tripwire, writers, written

PRINT_ONLY = ("card", "compile", "contact", "probe", "ledger", "research", "critic", "gate")


def on_post(evt, ctx) -> dict:
    tool = evt.get("tool_name")
    if tool in written.WRITE_TOOLS:
        written.on_post(evt, ctx)
    lanes.link_spawn(evt, ctx)
    if getattr(ctx, "job", None) and evt.get("session_id"):
        from . import lease
        lease.heartbeat(ctx, ctx.job, evt["session_id"])
    res = writers.on_post(evt, ctx) or {}
    sid = evt.get("session_id")
    from . import budget
    charged = budget.charge(evt, ctx)  # a refused writer call is charged too: refusals cannot loop for free
    if charged.get("decision") == "block":
        return charged
    if res.get("decision") == "block":
        return res
    lone = writers._lone_harness((evt.get("tool_input") or {}).get("command")) if tool == "Bash" else None
    if sid and not (lone and lone[0] in PRINT_ONLY):  # harness-sandbox-exec runs code: it counts
        trip = tripwire.on_post(evt, ctx, sessionstate.load(ctx, sid))
        if trip:
            return trip
    return res
