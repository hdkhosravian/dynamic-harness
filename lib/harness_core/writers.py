"""PostToolUse writers dispatcher (spec §Trust boundaries: "Every bin/harness-<tool> <sub> has exactly one
PostToolUse writer"; build phase P3).

- Bash: only a lone ``harness-<tool> …`` command (no shell operators) reaches a writer; the request is the
  last JSON line the CLI printed, and it must name the same tool. The PreToolUse classifier already made
  sure the program is the plugin's own CLI. Writers in P3: ``compile`` and ``contact``.
- ``AskUserQuestion``: ``memory.apply`` (R-9) and ``contacts.record_ask`` (R-7).

A writer's refusal comes back as a block reason, which Claude Code shows to the model; success comes back as
``additionalContext`` (the opening line to print, or the contact to show).
"""
from __future__ import annotations

import json
import os
import re
import shlex

from . import answer, claims
from . import compile as compile_
from . import contact, contacts, memory, renders
from .errors import HarnessError

WRITERS = {"compile": compile_.apply, "contact": contact.apply, "ledger": renders.ledger_apply,
           "research": renders.research_apply, "critic": renders.critic_apply, "answer": answer.apply,
           "claims": claims.apply}
JOB_SCOPED = ("contact", "ledger", "research", "critic", "answer", "claims")
_OPERATORS = re.compile(r"[;&|<>`$()\n\\]")


def _lone_harness(command):
    """``(tool, args)`` for a lone ``harness-<tool>`` invocation, else None."""
    if not isinstance(command, str) or _OPERATORS.search(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    name = os.path.basename(words[0])
    if not name.startswith("harness-"):
        return None
    return name[len("harness-"):], words[1:]


def _request(evt, tool):
    resp = evt.get("tool_response")
    out = resp.get("stdout") if isinstance(resp, dict) else resp if isinstance(resp, str) else None
    if not isinstance(out, str):
        raise HarnessError(f"harness-{tool} printed no request")
    lines = [x for x in out.strip().splitlines() if x.strip()]
    try:
        req = json.loads(lines[-1]) if lines else None
    except ValueError as e:
        raise HarnessError(f"harness-{tool}: the request is not JSON ({e})") from e
    if not isinstance(req, dict) or req.get("tool") != tool:
        raise HarnessError(f"harness-{tool}: the request does not name tool {tool!r}")
    return req


def _context(msg):
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}


def _say(tool, res):
    if tool == "compile":
        if res.get("reunderstood"):
            return (f"harness: {res['job_id']} re-understood (card v{res['version']}, {res['tier']}). Tell the owner "
                    f"exactly: {res['notice']}")
        if res.get("state") == "REFUSED":
            return f"harness: task refused ({res['job_id']}). Tell the owner exactly: {res['message']}"
        return (f"harness: compiled {res['job_id']} at {res['tier']}. Print this opening line verbatim, then work "
                f"in .claude/loops/{res['job_id']}/work/: {res['opening']}")
    if tool == "contact":
        if res.get("resumed"):
            return f"harness: the owner approved continuing; the task resumed ({res['calls'] or 0} more calls)."
        if res.get("defined"):
            restated = (f" It replaces version {res['restated']}: show the restated figure." if res.get("restated")
                        else "")
            return f"harness: the owner confirmed metric {res['defined']} (version {res['version']}).{restated}"
        if res.get("granted"):
            return f"harness: research plan {res['plan_sha'][:12]} accepted; grant for {', '.join(res['domains'])}."
        return (f"harness: contact {res['contact_id']} ({res['type']}) recorded. Show it to the owner and wait for "
                f"the reply: {res['text']}")
    if tool in ("research", "critic"):
        agent = "web-researcher" if tool == "research" else "verifier"
        return (f"harness: {tool} render recorded. Spawn dynamic-harness:{agent} with exactly this prompt (no "
                f"changes) as the Agent prompt. PROMPT:\n{res['prompt']}")
    if tool == "claims":
        return claims._say(res)
    if tool == "answer":
        return ("harness: answer header recorded for the gate. Put these three lines at the top of the answer, "
                f"exactly:\n{res['header']}")
    if tool == "ledger":
        return f"harness: {res['kind']} row recorded: {json.dumps(res['row'], ensure_ascii=False)[:200]}"
    return f"harness: {tool} recorded"


def on_post(evt, ctx) -> dict:
    tool = evt.get("tool_name")
    if tool == "AskUserQuestion":
        memory.apply(evt, ctx)
        contacts.record_ask(evt, ctx)
        return {}
    if tool != "Bash":
        return {}
    parsed = _lone_harness((evt.get("tool_input") or {}).get("command"))
    if parsed is None or parsed[0] not in WRITERS:
        return {}
    name = parsed[0]
    try:
        if name in JOB_SCOPED and getattr(ctx, "job", None):  # a stale epoch or another session's task
            from . import lease
            lease.require(ctx, ctx.job, evt.get("session_id"))
        res = WRITERS[name](_request(evt, name), ctx, evt)
    except HarnessError as e:  # Denied, CardError, SchemaError, TamperError: nothing was recorded
        return {"decision": "block", "reason": f"harness-{name} not recorded: {e}"}
    if not res:  # harness-claims fetch: its own hook entry does the work
        return {}
    out = _context(_say(name, res))
    if name == "contact" and res.get("type") in ("plan", "definition_confirm") and res.get("text") \
            and not res.get("granted") and not res.get("defined"):
        out["systemMessage"] = res["text"]  # the owner sees the rendered plan itself, not the model's retelling
    return out
