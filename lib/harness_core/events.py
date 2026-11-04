"""Hash-chained harness event log: run_dir/events.jsonl plus a head file."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import canon, schema
from .errors import HarnessError, TamperError
from .safeio import append_bytes, atomic_write, safe_open


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class EventLog:
    def __init__(self, run_dir, head_file):
        self.run_dir = Path(run_dir)
        self.head_file = Path(head_file)
        self.path = self.run_dir / "events.jsonl"

    def _read(self) -> list:
        if not self.path.exists() and not os.path.islink(self.path):
            return []
        raw = safe_open(self.path, [self.run_dir])
        try:
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        except ValueError as e:
            raise TamperError(f"unparseable events.jsonl: {e}") from e

    def events(self) -> list:
        return self._read()

    def _head(self):
        if not self.head_file.exists() and not os.path.islink(self.head_file):
            return None
        return safe_open(self.head_file, [self.head_file.parent]).decode("utf-8").strip()

    def _verified(self) -> list:
        evs = self._read()
        prev = ""
        for i, e in enumerate(evs, 1):
            if not isinstance(e, dict) or e.get("seq") != i or e.get("prev_sha") != prev:
                raise TamperError(f"event chain broken at seq {i}")
            body = {k: v for k, v in e.items() if k != "sha"}
            if canon.sha256_json(body) != e.get("sha"):
                raise TamperError(f"event sha mismatch at seq {i}")
            prev = e["sha"]
        head = self._head()
        if (head or "") != prev or (head is None and evs):
            raise TamperError("events head mismatch")
        return evs

    def verify(self) -> str:
        evs = self._verified()
        return evs[-1]["sha"] if evs else ""

    def append(self, type: str, payload: dict, row_sha=None, file=None, file_sha=None) -> dict:
        evs = self._verified()  # never extend a tampered chain
        e = {
            "seq": len(evs) + 1,
            "ts": now(),
            "type": type,
            "payload": payload,
            "prev_sha": evs[-1]["sha"] if evs else "",
        }
        for k, v in (("row_sha", row_sha), ("file", file), ("file_sha", file_sha)):
            if v is not None:
                e[k] = v
        e["sha"] = canon.sha256_json(e)
        schema.check(e, "event")
        append_bytes(self.path, canon.canonical_json(e) + b"\n")
        atomic_write(self.head_file, e["sha"].encode("utf-8"))
        if self.verify() != e["sha"]:
            raise HarnessError("chain verify failed after append")
        return e

    def latest_file_sha(self, file: str):
        for e in reversed(self._read()):
            if e.get("file") == file and "file_sha" in e:
                return e["file_sha"]
        return None

    def check_file(self, path, file: str) -> None:
        path = Path(path)
        want = self.latest_file_sha(file)
        present = path.exists() or os.path.islink(path)
        if not want and not present:  # file_sha "" = the writer recorded a deletion
            return
        if not want:
            raise TamperError(f"{file}: present but no writer event recorded")
        if not present:
            raise TamperError(f"{file}: recorded but missing on disk")
        if canon.sha256_bytes(safe_open(path, [path.parent])) != want:
            raise TamperError(f"{file}: bytes differ from recorded file_sha")
