"""PreToolUse safety (spec §Hooks, "PreToolUse safety" row; §Irreversible-action control)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import classes, egress, query_guard
from .egress import RESEARCHER
from .errors import Denied


@dataclass
class Ctx:
    """Everything a safety decision may depend on. Built by hookmain from hook input + env."""

    workspace: Path
    data_root: Optional[Path] = None
    plugin_root: Optional[Path] = None
    home: Optional[Path] = None
    loop_contract_root: Optional[Path] = None
    job: Optional[str] = None
    agent_type: Optional[str] = None
    permission_mode: Optional[str] = None
    connector_registry: dict = field(default_factory=dict)
    written_this_task: set = field(default_factory=set)
    task_started: Optional[float] = None
    grant: Optional[dict] = None
    allowlist: Optional[list] = None
    protected_terms: list = field(default_factory=list)
    owner_tokens: list = field(default_factory=list)
    nonce_ok: Optional[Callable] = None
    temp_roots: Optional[list] = None  # None => /tmp, /private/tmp, tempfile.gettempdir()
    settings_baseline: Optional[dict] = None
    plugin_id: str = "dynamic-harness"
    log: Any = None


def _research_exception(tool, c, ctx) -> bool:
    """v1 exception: researcher-lane GETs/queries under an active per-job grant that pass the guard."""
    grant = ctx.grant
    if ctx.agent_type != RESEARCHER or not grant or grant.get("active", True) is False:
        return False
    if c.cls not in ("egress_offlist", "egress_with_data"):
        return False
    try:
        if c.url and tool in ("WebFetch", "Bash"):  # Bash only for a lone `harness-claims fetch <url>`
            if not egress.grant_covers(c.url, ctx):
                return False
            query_guard.check(c.url, ctx.protected_terms, ctx.owner_tokens)
            return True
        if tool == "WebSearch" and c.query is not None:
            query_guard.check(c.query, ctx.protected_terms, ctx.owner_tokens)
            return True
    except Denied:
        return False
    return False


def on_pre(evt, ctx: Ctx) -> dict:
    """``{}`` to allow, ``{"decision": "deny", "reason": ...}`` otherwise. Never raises on bad input."""
    if not isinstance(evt, dict) or not isinstance(evt.get("tool_name"), str):
        return {"decision": "deny", "reason": "malformed PreToolUse event"}
    tool = evt["tool_name"]
    c = classes.classify(tool, evt.get("tool_input"), ctx.connector_registry, ctx)
    if not c.irreversible or _research_exception(tool, c, ctx):
        return {}
    reason = f"{c.cls}: {c.reason} (v1 denies irreversible actions)"
    if ctx.log is not None:
        try:
            ctx.log.append("SAFETY_DENY", {"tool": tool, "cls": c.cls, "reason": c.reason})
        except Exception:
            pass  # a logging failure must not turn a deny into an allow
    return {"decision": "deny", "reason": reason}
