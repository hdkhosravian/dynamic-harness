"""Lease (spec §State machine "Lease"; build phase P4).

``lease.json`` in the run directory: ``{session_id, epoch, heartbeat, ttl}`` (TTL 15 minutes).

- ``acquire`` at compile (epoch 1). ``heartbeat`` refreshes it at most once a minute (``LEASE_HEARTBEAT``).
- ``status(ctx, job, sid)``: ``owner``, ``other`` (another session holds a live lease), ``stale`` or ``none``.
- ``require(ctx, job, sid)``: every job-scoped writer calls it; a non-owner, including an old owner after a
  takeover (a stale epoch), is refused.
- ``takeover``: only when stale; raises the epoch. The remaining units are recomputed in-process from the
  ledger (the same projection ``fold_ledger.py --remaining`` reports) and returned for the new session.
A second session on a live lease gets read-only access (``pretool``).
"""
from __future__ import annotations

import time

from . import store
from .errors import Denied
from .events import EventLog
from .ledger import ROW_SCHEMA, Ledger
from .paths import job_paths

TTL = 15 * 60
BEAT = 60
FILE = "lease.json"
TERMINAL = ("PASS", "PASS_FLAGGED", "BLOCKED", "DESCOPED_BY_USER", "SUPPORTED", "REFUTED", "NOT_INVESTIGATED")


def _p(ctx, job):
    p = job_paths(ctx.data_root, ctx.workspace, job)
    return p, EventLog(p.run, p.heads)


def read(ctx, job):
    p, log = _p(ctx, job)
    v = store.read_json(p.run, log, FILE, None)
    return v if isinstance(v, dict) else None


def acquire(ctx, job, sid) -> dict:
    p, log = _p(ctx, job)
    lease = {"schema": "harness/lease@1", "session_id": sid, "epoch": 1, "heartbeat": time.time(), "ttl": TTL}
    store.write_json(p.run, log, FILE, lease, "LEASE", {"session_id": sid, "epoch": 1})
    return lease


GATE_MAX = 1860  # the gate hook's 1800 s timeout plus slack


def _gate_running(ctx, job) -> bool:
    p, log = _p(ctx, job)
    start = None
    for e in log.events():
        if e["type"] == "GATE_STARTED":
            start = e["payload"].get("at")
        elif e["type"] in ("RECEIPT", "GATE_FAILED", "TAMPER", "GATE_ENDED"):
            start = None
    return start is not None and time.time() - float(start) < GATE_MAX


def status(ctx, job, sid) -> str:
    lease = read(ctx, job)
    if not lease:
        return "none"
    if lease.get("session_id") == sid:
        return "owner"
    stale = time.time() - float(lease.get("heartbeat", 0)) > float(lease.get("ttl", TTL))
    return "stale" if stale and not _gate_running(ctx, job) else "other"


def _indexed_owner(ctx, job, sid) -> bool:
    from .sessionstate import jobs
    return jobs(ctx).get(job, {}).get("session_id") == sid


def require(ctx, job, sid) -> None:
    """Fails closed: with no lease, only the session that compiled the task (jobs index) may claim one."""
    if status(ctx, job, sid) != "owner":
        claim(ctx, job, sid, allow_stale=False)


def claim(ctx, job, sid, allow_stale=True) -> dict:
    """Owner, or obtain the lease under the log lock: ``none`` → acquire (the indexed owner only),
    ``stale`` → takeover. Anything else is refused."""
    p, log = _p(ctx, job)
    with log.lock():
        st = status(ctx, job, sid)
        if st == "owner":
            return dict(read(ctx, job), taken=False)
        if st == "none" and _indexed_owner(ctx, job, sid):
            return dict(acquire(ctx, job, sid), taken=False)
        if st != "stale" or not allow_stale:
            raise Denied(f"another session holds task {job} (lease {st}); this session may only read it")
    return dict(takeover(ctx, job, sid), taken=True)


def heartbeat(ctx, job, sid) -> None:
    p, log = _p(ctx, job)
    with log.lock():
        lease = read(ctx, job)
        if not lease or lease.get("session_id") != sid or time.time() - float(lease.get("heartbeat", 0)) < BEAT:
            return
        store.write_json(p.run, log, FILE, dict(lease, heartbeat=time.time()), "LEASE_HEARTBEAT",
                         {"epoch": lease["epoch"]})


def remaining(ctx, job) -> list:
    p, log = _p(ctx, job)
    led = Ledger(p, log)
    latest = {}
    for kind, row in led.rows():
        if kind in ROW_SCHEMA:
            latest[row["id"]] = row.get("verdict")
    return [u["id"] for u in led.sanctioned_scope() if latest.get(u["id"]) not in TERMINAL]


def takeover(ctx, job, sid) -> dict:
    p, log = _p(ctx, job)
    with log.lock():
        if status(ctx, job, sid) != "stale":
            raise Denied(f"the lease on {job} is not stale; it cannot be taken over")
        lease = read(ctx, job)
        new = dict(lease, session_id=sid, epoch=int(lease.get("epoch", 1)) + 1, heartbeat=time.time())
        store.write_json(p.run, log, FILE, new, "LEASE", {"session_id": sid, "epoch": new["epoch"],
                                                          "from": lease.get("session_id")})
    return dict(new, remaining=remaining(ctx, job))
