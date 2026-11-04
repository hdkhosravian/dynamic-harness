"""v1 learning: user corrections, logged verbatim as evidence only (spec §Learning (phased); build phase P3).

``log(ctx, job, turn_id)`` appends ``{turn_id, seq, ts, text_norm, text_sha}`` to the protected
``runs/<job>/corrections.jsonl`` (a ``CORRECTION`` event in the job log). The turn must be a real owner
turn: ``Turns.cite`` refuses an unknown turn, a system (background-subagent) turn, or a text that no longer
matches its sha. ``text_norm`` is already redacted (``pii.redact``) when the turn is written. Nothing reads
these rows to change behaviour in v1.
"""
from __future__ import annotations

from . import store
from .events import EventLog
from .paths import job_paths
from .turns import Turns

FILE = "corrections.jsonl"


def _log(ctx, job):
    p = job_paths(ctx.data_root, ctx.workspace, job)
    return p, EventLog(p.run, p.heads)


def log(ctx, job, turn_id) -> dict:
    turn = Turns(ctx.data_root).cite(turn_id)  # Denied / TamperError propagate: no evidence without a turn
    p, ev = _log(ctx, job)
    row = {"schema": "harness/correction@1", "turn_id": turn["turn_id"], "seq": turn["seq"], "ts": turn["ts"],
           "text_norm": turn["text_norm"], "text_sha": turn["text_sha"]}
    store.append_jsonl(p.run, ev, FILE, row, "CORRECTION", {"turn_id": turn["turn_id"]})
    return row


def rows(ctx, job) -> list:
    p, ev = _log(ctx, job)
    return store.read_jsonl(p.run, ev, FILE)
