"""Protected JSON / JSONL files with a chained event per write (spec §Trust boundaries: "Every writer event
carries file_sha"; "Every hook that reads a protected file first checks that sha against the latest
event for that file").

``append_jsonl`` and ``write_json`` hold the log's lock from the check to the event. ``read_json`` and
``read_jsonl`` check the file against its latest event first, so an out-of-band edit is ``TamperError``.
A file with no event yet must not exist (``check_file`` raises on an unrecorded file).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import canon
from .events import EventLog
from .safeio import append_bytes, atomic_write, safe_open


def session_log(data_root) -> EventLog:
    """The session-level log that turns, memory and session files share."""
    root = Path(data_root)
    return EventLog(root, root / "session_events.head", name="session_events.jsonl")


def append_jsonl(root, log: EventLog, name: str, row: dict, type: str, payload: dict) -> dict:
    root = Path(root)
    path = root / name
    with log.lock():
        log.check_file(path, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        append_bytes(path, canon.canonical_json(row) + b"\n")
        return log.append(type, payload, row_sha=canon.sha256_json(row), file=name,
                          file_sha=canon.sha256_bytes(safe_open(path, [root])))


def write_json(root, log: EventLog, name: str, obj, type: str, payload: dict) -> dict:
    root = Path(root)
    path = root / name
    data = canon.canonical_json(obj)
    with log.lock():
        log.check_file(path, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data)
        return log.append(type, payload, file=name, file_sha=canon.sha256_bytes(data))


def read_jsonl(root, log: EventLog, name: str) -> list:
    root = Path(root)
    path = root / name
    log.check_file(path, name)
    if not path.exists():
        return []
    return [json.loads(x) for x in safe_open(path, [root]).splitlines() if x.strip()]


def read_json(root, log: EventLog, name: str, default=None):
    root = Path(root)
    path = root / name
    log.check_file(path, name)
    if not path.exists():
        return default
    return json.loads(safe_open(path, [root]))
