"""User memory (R-9; spec §Data contracts "User memory record"; build phase P3).

Written only by the PostToolUse hook on ``AskUserQuestion``, from structured answers, into
``${CLAUDE_PLUGIN_DATA}/memory.jsonl`` (a ``MEMORY`` event per row in the session log).

- Only a question whose header starts with ``[pref:<key>]`` (or ``[pref:<key>:task|project]``) is stored.
- Allowed keys and closed value enums: tone ∈ {formal, neutral, warm}; format ∈ {short, detailed};
  report_style ∈ {summary_first, table_first}; rounding ∈ {none, thousands, millions}. The chosen option
  label must name one of those values (case and spaces ignored). Values with URLs or emails, or longer
  than 40 characters, are rejected. Free text is never stored here.
- Within one person the newest record wins; task-scoped records apply only to their task.
  ``default_choices`` (question → option map) is not stored in v1.
"""
from __future__ import annotations

import re
import secrets

from . import canon, store
from .events import now

ALLOWED = {"tone": ("formal", "neutral", "warm"), "format": ("short", "detailed"),
           "report_style": ("summary_first", "table_first"), "rounding": ("none", "thousands", "millions")}
FILE = "memory.jsonl"
_TAG = re.compile(r"^\s*\[pref:([a-z_]+)(?::(task|project))?\]")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)|[a-z0-9-]+\.(?:com|net|org|io|ir|example)\b")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _value(key, answer):
    if not isinstance(answer, str) or len(answer) > 40 or _URL.search(answer) or _EMAIL.search(answer):
        return None
    v = re.sub(r"[\s-]+", "_", answer.strip().casefold())
    return v if v in ALLOWED.get(key, ()) else None


def _answers(evt) -> dict:
    out = {}
    for src in ((evt.get("tool_input") or {}), (evt.get("tool_response") or {})):
        a = src.get("answers") if isinstance(src, dict) else None
        if isinstance(a, dict):
            out.update({k: v for k, v in a.items() if isinstance(k, str)})
    return out


def apply(evt, ctx) -> list:
    """Store the tagged preference answers of one AskUserQuestion call; returns the stored rows."""
    evt = evt if isinstance(evt, dict) else {}
    ti = evt.get("tool_input")
    qs = ti.get("questions") if isinstance(ti, dict) else None
    if not isinstance(qs, list):
        return []
    answers, rows = _answers(evt), []
    log = store.session_log(ctx.data_root)
    for q in qs:
        if not isinstance(q, dict) or not isinstance(q.get("header"), str) or q.get("multiSelect") is True:
            continue
        m = _TAG.match(q["header"])
        if not m or m.group(1) not in ALLOWED:
            continue
        key, scope = m.group(1), m.group(2) or "global"
        value = _value(key, answers.get(q.get("question")))
        if value is None:
            continue
        row = {"schema": "harness/memory@1", "id": secrets.token_hex(8), "scope": scope, "person_id": "owner",
               "key": key, "value": value, "verbatim": answers[q["question"]], "ts": now(),
               "source_task": getattr(ctx, "job", None)}
        store.append_jsonl(ctx.data_root, log, FILE, row, "MEMORY", {"key": key, "scope": scope})
        rows.append(row)
    return rows


def effective(ctx, job=None) -> dict:
    """``{key: value}`` in effect: the newest record per key; task-scoped records only for ``job``."""
    out = {}
    for r in store.read_jsonl(ctx.data_root, store.session_log(ctx.data_root), FILE):
        if r.get("key") not in ALLOWED or r.get("value") not in ALLOWED[r["key"]]:
            continue
        if r.get("scope") == "task" and r.get("source_task") != job:
            continue
        out[r["key"]] = r["value"]  # file order is append order: the last one wins
    return out


def snapshot(ctx, job=None) -> str:
    """The card's ``versions.memory_snapshot``."""
    return canon.sha256_json(effective(ctx, job))
