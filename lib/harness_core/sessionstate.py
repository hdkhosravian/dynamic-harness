"""Session file and jobs index (spec §Hooks: prompt_scope, pending_card, active_task; §State machine: jobs
index; build phase P3).

- ``sessions/<sid>.json``: ``{session_id, active_task, prompt_scope, pending_card, pending_contact_id,
  probe, request_turn, t0_calls}``. Writers: UserPromptSubmit, ``compile.apply``, ``contact.apply`` and the
  tripwire. ``prompt_scope`` ∈ {active_task, inline_t0, new}; ``pending_card`` is ``{floor, reason,
  turn_id}`` or null.
- ``jobs/index.json``: ``{jobs: {job_id: {state, tier, task_type, session_id, ts, terminal, subject}}}``.
  Writers: ``compile.apply`` and Stop (P4).

Both are protected files with a chained event in the session log (``store``), so a hook that reads them
checks the bytes against the latest event first.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from . import canon, store
from .events import now
from .safeio import atomic_write
from .written import safe_id

DEFAULT = {"active_task": None, "prompt_scope": "new", "pending_card": None, "pending_contact_id": None,
           "probe": None, "request_turn": None, "t0_calls": {}}
TERMINAL = ("DONE", "ABANDONED", "REFUSED", "STOPPED_TAMPER")


def _name(sid) -> str:
    return f"sessions/{safe_id(sid)}.json"


def load(ctx, sid) -> dict:
    v = store.read_json(ctx.data_root, store.session_log(ctx.data_root), _name(sid), None)
    out = dict(DEFAULT, session_id=sid)
    if isinstance(v, dict):
        out.update(v)
    return out


def reset(ctx, sid, reason) -> dict:
    """The session file fails its check: move it aside and start again closed (a T2 card is needed)."""
    log = store.session_log(ctx.data_root)
    name = _name(sid)
    path = Path(ctx.data_root) / name
    with log.lock():
        if os.path.lexists(path):
            os.replace(path, path.with_name(f"{path.name}.tamper-{secrets.token_hex(4)}"))
        state = dict(DEFAULT, session_id=sid,
                     pending_card={"floor": "T2", "reason": f"session state was reset: {reason}", "turn_id": None})
        data = canon.canonical_json(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data)
        log.append("SESSION_RESET", {"session_id": sid, "reason": reason[:200]}, file=name,
                   file_sha=canon.sha256_bytes(data))
    return state


def update(ctx, sid, **changes) -> dict:
    log = store.session_log(ctx.data_root)
    with log.lock():
        cur = load(ctx, sid)
        cur.update(changes)
        store.write_json(ctx.data_root, log, _name(sid), cur, "SESSION",
                         {"session_id": sid, "keys": sorted(changes)})
        return cur


def jobs(ctx) -> dict:
    v = store.read_json(ctx.data_root, store.session_log(ctx.data_root), "jobs/index.json", None)
    return v.get("jobs", {}) if isinstance(v, dict) and isinstance(v.get("jobs"), dict) else {}


def set_job(ctx, job, **fields) -> dict:
    log = store.session_log(ctx.data_root)
    with log.lock():
        all_jobs = jobs(ctx)
        row = dict(all_jobs.get(job, {}))
        row.update(fields, ts=now())
        row["terminal"] = row.get("state") in TERMINAL
        all_jobs[job] = row
        store.write_json(ctx.data_root, log, "jobs/index.json", {"schema": "harness/jobs_index@1", "jobs": all_jobs},
                         "JOBS", {"job_id": job, "state": row.get("state")})
        return row


def tier_of(ctx, job):
    return jobs(ctx).get(job, {}).get("tier")
