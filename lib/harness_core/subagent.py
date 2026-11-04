"""SubagentStop (spec §Hooks "SubagentStop"; build phase P3). Never blocks, never requires the gate.

- Every harness subagent's stop is recorded in ``subagents.jsonl``: ``{agent_type, agent_id, prompt_sha,
  verdict_sha}``. ``prompt_sha`` comes from the spawn the lanes hook recorded (SubagentStop carries no
  prompt); ``verdict_sha`` hashes ``last_assistant_message``.
- A researcher's output is accepted only as schema-valid claim rows ``{claim, quote ≤ 300 chars, date, url,
  snapshot_sha}`` (a JSON list, or one JSON object per line). Each row's ``(url, snapshot_sha)`` must be in
  the hook-written fetch log, and its quote must appear in that snapshot (whitespace and case folded).
  Accepted rows go to ``claims.jsonl``; everything else, free text included, is dropped and only counted.
"""
from __future__ import annotations

import json
import re

from . import canon, schema, store
from .events import EventLog, now
from .lanes import RESEARCHER
from .paths import job_paths
from .safeio import safe_open

CLAIM = {"type": "object", "additionalProperties": False, "required": ["claim", "quote", "date", "url", "snapshot_sha"],
         "properties": {"claim": {"type": "string"}, "quote": {"type": "string"}, "date": {"type": "string"},
                        "url": {"type": "string"}, "snapshot_sha": {"type": "string"}}}


def _fold(s) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def _rows(text) -> list:
    text = text.strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else [v]
    except ValueError:
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


def _accept(row, p, log, fetched) -> bool:
    try:
        schema.validate(row, CLAIM, "claim")
    except Exception:
        return False
    if len(row["quote"]) > 300 or not re.fullmatch(r"[0-9a-f]{64}", row["snapshot_sha"]):
        return False
    if (row["url"], row["snapshot_sha"]) not in fetched:
        return False
    snap = p.run / "snapshots" / row["snapshot_sha"]
    try:
        data = safe_open(snap, [p.run])
    except Exception:
        return False
    if canon.sha256_bytes(data) != row["snapshot_sha"]:
        return False
    return _fold(row["quote"]) in _fold(data.decode("utf-8", "replace"))


def on_subagent_stop(evt, ctx) -> dict:
    job = getattr(ctx, "job", None)
    agent_type = evt.get("agent_type")
    if not job or not isinstance(agent_type, str) or not agent_type.startswith("dynamic-harness:"):
        return {}
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    msg = evt.get("last_assistant_message") if isinstance(evt.get("last_assistant_message"), str) else ""
    spawns = store.read_jsonl(p.run, log, "spawns.jsonl")
    link = next((s for s in spawns if s.get("agent_id") == evt.get("agent_id")), {})
    spawn = next((s for s in spawns if s.get("tool_use_id") == link.get("tool_use_id") and s.get("prompt_sha")), {})
    rec = {"ts": now(), "agent_type": agent_type, "agent_id": evt.get("agent_id"), "prompt_sha": spawn.get("prompt_sha"),
           "verdict_sha": canon.sha256_bytes(msg.encode("utf-8"))}
    if agent_type == RESEARCHER:
        fetched = {(r.get("url"), r.get("snapshot_sha")) for r in store.read_jsonl(p.run, log, "fetch_log.jsonl")}
        rows = _rows(msg)
        accepted = [r for r in rows if isinstance(r, dict) and _accept(r, p, log, fetched)]
        for r in accepted:
            store.append_jsonl(p.run, log, "claims.jsonl", dict(r, agent_id=evt.get("agent_id")), "CLAIM",
                               {"url": r["url"], "snapshot_sha": r["snapshot_sha"]})
        rec.update(claims_accepted=len(accepted), claims_dropped=len(rows) - len(accepted))
    store.append_jsonl(p.run, log, "subagents.jsonl", rec, "SUBAGENT", {"agent_type": agent_type})
    return {}
