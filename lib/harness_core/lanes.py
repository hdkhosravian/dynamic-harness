"""PreToolUse lanes (spec §Hooks "PreToolUse lanes"; build phase P3). Active for tasks at T1 and T2.

- Web and browser tools only for ``dynamic-harness:web-researcher`` in an ``investigation`` task.
- The researcher may run exactly one Bash form, ``harness-claims fetch <url>``, plus WebSearch / WebFetch
  (egress and the grant are the safety hook's); everything else is denied to it, including spawns.
- Spawns (``Agent``): only ``dynamic-harness:<agent>`` types. A researcher or verifier spawn needs a prompt
  whose sha a hook recorded from ``harness-research render`` / ``harness-critic render``
  (``renders.jsonl``); those renderers arrive with P4/P5, so until then such spawns are denied. Every
  allowed spawn is recorded with its ``prompt_sha`` (``spawns.jsonl``); PostToolUse links its agent id.
- ``SendMessage`` to a running researcher is denied.
"""
from __future__ import annotations

import os
import re
import shlex

from . import canon, contacts, store
from .events import EventLog, now
from .paths import job_paths

RESEARCHER = "dynamic-harness:web-researcher"
VERIFIER = "dynamic-harness:verifier"
WEB = ("WebSearch", "WebFetch")
_BROWSER = re.compile(r"(?i)browser|playwright|chrome|puppeteer")
_PREFIX = "dynamic-harness:"
DATA_READ = ("Read", "Grep", "Glob", "NotebookRead", "LSP")


def _deny(reason):
    return {"decision": "deny", "reason": f"lanes: {reason}"}


def _web(tool) -> bool:
    return tool in WEB or (tool.startswith("mcp__") and bool(_BROWSER.search(tool)))


def _paths(ctx):
    p = job_paths(ctx.data_root, ctx.workspace, ctx.job)
    return p, EventLog(p.run, p.heads)


def _task_type(ctx):
    p, log = _paths(ctx)
    c = store.read_json(p.run, log, "card.v1.json", None)
    return c.get("task_type") if isinstance(c, dict) else None


def _rendered(ctx, sha) -> bool:
    p, log = _paths(ctx)
    return any(r.get("prompt_sha") == sha for r in store.read_jsonl(p.run, log, "renders.jsonl"))


def _spawns(ctx) -> list:
    p, log = _paths(ctx)
    return store.read_jsonl(p.run, log, "spawns.jsonl")


def _lone_fetch(command) -> bool:
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\\]", command):
        return False
    try:
        w = shlex.split(command)
    except ValueError:
        return False
    return len(w) == 3 and os.path.basename(w[0]) == "harness-claims" and w[1] == "fetch"


def on_pre(evt, ctx) -> dict:
    if not getattr(ctx, "job", None) or contacts.tier(ctx) not in ("T1", "T2"):
        return {}
    tool, ti = evt.get("tool_name"), evt.get("tool_input") or {}
    agent = evt.get("agent_type")
    if agent == RESEARCHER:
        if _task_type(ctx) != "investigation":
            return _deny("the researcher works only in an investigation task")
        if tool in WEB:
            return {}
        if tool == "Bash" and _lone_fetch(ti.get("command")):
            return {}
        return _deny(f"the researcher may use only WebSearch, WebFetch and `harness-claims fetch <url>`, not {tool}")
    if _web(tool):
        return _deny(f"{tool} belongs to the researcher lane (dynamic-harness:web-researcher in an investigation)")
    if tool == "Agent":
        kind = ti.get("subagent_type")
        if not isinstance(kind, str) or not kind.startswith(_PREFIX):
            return _deny(f"only dynamic-harness agents may be spawned in a T1/T2 task, not {kind!r}")
        prompt = ti.get("prompt") if isinstance(ti.get("prompt"), str) else ""
        sha = canon.sha256_bytes(prompt.encode("utf-8"))
        if kind in (RESEARCHER, VERIFIER) and not _rendered(ctx, sha):
            src = "harness-research render" if kind == RESEARCHER else "harness-critic render"
            return _deny(f"a {kind} spawn needs the exact prompt printed by {src}")
        p, log = _paths(ctx)
        store.append_jsonl(p.run, log, "spawns.jsonl", {"ts": now(), "subagent_type": kind, "prompt_sha": sha,
                                                        "tool_use_id": evt.get("tool_use_id")},
                           "SPAWN", {"subagent_type": kind, "prompt_sha": sha})
        return {}
    if tool == "SendMessage":
        to = ti.get("to")
        if any(s.get("subagent_type") == RESEARCHER and to in (s.get("agent_id"), s.get("tool_use_id"))
               for s in _spawns(ctx)):
            return _deny("a running researcher may not be continued or messaged")
    return {}


def link_spawn(evt, ctx) -> None:
    """PostToolUse on Agent: record which agent id the recorded spawn got."""
    if not getattr(ctx, "job", None) or evt.get("tool_name") != "Agent":
        return
    resp = evt.get("tool_response")
    agent_id = resp.get("agentId") if isinstance(resp, dict) else None  # spike: {"agentId": "a5cf…", …}
    if not isinstance(agent_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", agent_id) or not evt.get("tool_use_id"):
        return
    p, log = _paths(ctx)
    store.append_jsonl(p.run, log, "spawns.jsonl", {"ts": now(), "tool_use_id": evt["tool_use_id"],
                                                    "agent_id": agent_id,
                                                    "subagent_type": (evt.get("tool_input") or {}).get("subagent_type")},
                       "SPAWN_LINK", {"agent_id": agent_id})
