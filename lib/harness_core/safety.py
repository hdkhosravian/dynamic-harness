"""PreToolUse safety (spec §Hooks, "PreToolUse safety" row; §Irreversible-action control)."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import classes, egress, query_guard, written
from .egress import RESEARCHER
from .errors import Denied


@dataclass
class Ctx:
    """Everything a safety decision may depend on. Built by hookmain from hook input + env."""

    workspace: Path
    cwd: Optional[Path] = None  # the event's current directory; relative paths resolve here
    data_root: Optional[Path] = None
    plugin_root: Optional[Path] = None
    home: Optional[Path] = None
    loop_contract_root: Optional[Path] = None
    job: Optional[str] = None
    agent_type: Optional[str] = None
    permission_mode: Optional[str] = None
    connector_registry: dict = field(default_factory=dict)
    written_this_task: set = field(default_factory=set)
    untrusted_files: set = field(default_factory=set)  # realpaths created/changed since session start (ctime); hook layer fills it
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
    # set by hookmain from sessions/<sid>/status.json; None = no session gate (in-process callers)
    session_ok: Optional[bool] = None
    session_reason: Optional[str] = None


def _research_exception(tool, c, ctx) -> bool:
    """v1 exception: researcher-lane GETs/queries under an active per-job grant that pass the guard."""
    grant = ctx.grant
    if ctx.agent_type != RESEARCHER or not isinstance(grant, dict) or grant.get("active") is not True:
        return False
    if not _unexpired(grant.get("expires_at")):
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


def _unexpired(expires_at) -> bool:
    """Epoch seconds or a zone-aware ISO-8601 string in the future; anything else counts as expired."""
    if isinstance(expires_at, bool):
        return False
    if isinstance(expires_at, (int, float)):
        return expires_at > time.time()
    try:
        t = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return t.tzinfo is not None and t.timestamp() > time.time()


def _v1_allows(c, ctx) -> bool:
    """The class table's ``v1_action`` decides; a missing row or field, or an unreadable table, denies."""
    try:
        table = classes.load_table(ctx)["classes"]
        row = table.get(c.cls) or table["unknown"]
        return row.get("v1_action") == "allow" and not c.irreversible
    except Exception:
        return False


def _deny(tool, cls, why, reason, ctx) -> dict:
    if ctx.log is not None:
        try:
            ctx.log.append("SAFETY_DENY", {"tool": tool, "cls": cls, "reason": why})
        except Exception as e:  # a logging failure must not turn a deny into an allow
            print(f"harness: SAFETY_DENY not logged: {type(e).__name__}: {e}", file=sys.stderr)
    return {"decision": "deny", "reason": reason}


def on_pre(evt, ctx: Ctx) -> dict:
    """``{}`` to allow, ``{"decision": "deny", "reason": ...}`` otherwise. Never raises on bad input."""
    if not isinstance(evt, dict) or not isinstance(evt.get("tool_name"), str):
        return {"decision": "deny", "reason": "malformed PreToolUse event"}
    tool = evt["tool_name"]
    c = classes.classify(tool, evt.get("tool_input"), ctx.connector_registry, ctx)
    if ctx.session_ok is False and c.cls != "read":
        why = ctx.session_reason or "no passing SessionStart status for this session"
        return _deny(tool, "session", why, f"session checks have not passed ({why}): only reads are allowed", ctx)
    if _v1_allows(c, ctx) or _research_exception(tool, c, ctx):
        if tool == "Bash":  # recorded before the command runs: PostToolUse may never fire
            written.record_bash(evt, ctx)
        return {}
    return _deny(tool, c.cls, c.reason, f"{c.cls}: {c.reason} (v1 denies irreversible actions)", ctx)
