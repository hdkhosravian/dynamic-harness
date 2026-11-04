"""PostToolUse tripwire (spec §Hooks "PostToolUse tripwire"; build phase P3).

Active while no T1/T2 task is in scope and no card is pending. Triggers:
- a side-effecting call (anything the class table does not classify as ``read``);
- more than ``t0_tripwire_calls`` (8) tool calls, counted per session (per turn for an inline T0);
- any call other than the named neutral tools when the probe saw money or a data class (a data read, with
  no tool-name or file-name list to get around).
Effect: ``pending_card`` with a floor of T1 (T2 when the probe saw an irreversible class) and a block reason,
so only reads are allowed until a card compiles. For an inline T0 with an open task, that task gets
``PAUSED{handoff}``.
"""
from __future__ import annotations


from . import classes, contacts, protected, sessionstate
from .events import EventLog
from .paths import job_paths

def _template(key):
    from . import templates
    return templates.render(key, lang="en")  # a hint to the model, not owner text


def on_post(evt, ctx, st) -> dict:
    """``st`` is the session state; returns a block result when the wire trips, else {}."""
    sid = evt.get("session_id")
    if not sid or st.get("pending_card"):
        return {}
    scope = st.get("prompt_scope")
    if scope == "active_task" and contacts.tier(ctx) in ("T1", "T2"):
        return {}
    tool, ti = evt.get("tool_name") or "", evt.get("tool_input") or {}
    key = (st.get("request_turn") or "session") if scope == "inline_t0" else "session"
    calls = dict(st.get("t0_calls") or {})
    calls[key] = calls.get(key, 0) + 1
    probe = st.get("probe") or {}
    limit = int(protected.load_control("tiers.json", ctx)["t0_tripwire_calls"])
    c = classes.classify(tool, ti, ctx.connector_registry, ctx)
    why = None
    if c.cls != "read" and tool not in classes.NEUTRAL_TOOLS:  # TodoWrite, Skill…: no side effect
        why = f"a side-effecting call ({c.cls})"
    elif calls[key] > limit:
        why = f"more than {limit} tool calls"
    elif (probe.get("money_tokens") or probe.get("data_classes")) and tool not in classes.NEUTRAL_TOOLS:
        why = "a data read on a request with money or personal data"  # any non-neutral call: no name list
    if why is None:
        sessionstate.update(ctx, sid, t0_calls=calls)
        return {}
    from . import enforcement
    if enforcement.shadowed("tripwire", ctx, why):  # /dynamic-harness:off: log, never block
        sessionstate.update(ctx, sid, t0_calls=calls)
        return {}
    floor = "T2" if probe.get("irreversible_classes") else "T1"
    sessionstate.update(ctx, sid, t0_calls=calls,
                        pending_card={"floor": floor, "reason": f"tripwire: {why}", "turn_id": st.get("request_turn")})
    open_job = st.get("active_task")
    if scope == "inline_t0" and open_job:
        p = job_paths(ctx.data_root, ctx.workspace, open_job)
        EventLog(p.run, p.heads).append("PAUSED", {"reason": "handoff", "by": "tripwire"})
        sessionstate.set_job(ctx, open_job, state="PAUSED")
    return {"decision": "block", "reason": f"{_template('model.tripwire')} (tripwire: {why}; floor {floor})"}
