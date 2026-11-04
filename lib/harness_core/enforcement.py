"""``control/enforcement.json``: enforce or shadow, per component (spec §Detailed plan "enforcement.yaml"; P5).

Only ``contacts`` and ``tripwire`` can be put in shadow mode (``shadow_draft`` is always shadow). In shadow mode
a component logs ``SHADOW_WOULD_BLOCK`` in the session log and blocks nothing. Safety, budget, lanes, protected
paths, egress, the query guard, the trusted writers and Stop's receipt check are fixed ``enforce``. An unreadable
or unknown value counts as ``enforce``.
"""
from __future__ import annotations

from . import protected, store

COMPONENTS = ("contacts", "tripwire")


def mode(component, ctx) -> str:
    try:
        v = protected.load_control("enforcement.json", ctx).get(component)
    except Exception:  # an unreadable control file never turns a check off
        return "enforce"
    return "shadow" if component in COMPONENTS and v == "shadow" else "enforce"


def shadowed(component, ctx, reason) -> bool:
    """True (and the would-be block logged) when ``component`` runs in shadow mode."""
    if mode(component, ctx) != "shadow":
        return False
    log = store.session_log(ctx.data_root)
    log.append("SHADOW_WOULD_BLOCK", {"component": component, "reason": str(reason)[:300],
                                      "job": getattr(ctx, "job", None)})
    return True
