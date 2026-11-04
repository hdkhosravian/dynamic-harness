"""Hash-chained harness event log: run_dir/events.jsonl plus a head file.

Every line must be the exact canonical bytes of its event (no whitespace or
duplicate-key variants). ``<head>.started`` is written on the first append, so
deleting both the log and the head afterwards is detected.

Crash windows (append writes the line, then the head; ledger writers append the
row file, then the event): both writes are fsynced, and a crash between them
leaves a chain that fails closed with ``TamperError``. There is no silent
tolerance: the recovery path is operator repair (``/harness:status --repair``)."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import canon, schema
from .errors import HarnessError, TamperError
from .safeio import append_bytes, atomic_write, safe_open


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


_HELD = {}  # lock path -> [fd, depth]: flock is per open file, so re-entry in one process reuses the fd


@contextmanager
def file_lock(path):
    """Exclusive, process-reentrant fcntl.flock on ``path`` (created if missing)."""
    key = os.path.abspath(os.fspath(path))
    held = _HELD.get(key)
    if held is None:
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        held = _HELD[key] = [fd, 0]
    held[1] += 1
    try:
        yield
    finally:
        held[1] -= 1
        if held[1] == 0:
            del _HELD[key]
            fcntl.flock(held[0], fcntl.LOCK_UN)
            os.close(held[0])


class EventLog:
    def __init__(self, run_dir, head_file, name: str = "events.jsonl"):
        self.run_dir = Path(run_dir)
        self.head_file = Path(head_file)
        self.path = self.run_dir / name
        self.lock_path = self.run_dir / (name + ".lock")
        self.started = self.head_file.with_name(self.head_file.name + ".started")

    def lock(self):
        """Hold across read-check-write sequences (verify + append + head; row file + event)."""
        return file_lock(self.lock_path)

    def _read(self) -> list:
        if not self.path.exists() and not os.path.islink(self.path):
            return []
        raw = safe_open(self.path, [self.run_dir])
        if raw and not raw.endswith(b"\n"):
            raise TamperError(f"{self.path.name}: last line is incomplete")
        evs = []
        for line in raw.split(b"\n")[:-1]:
            try:
                e = json.loads(line)
            except ValueError as err:
                raise TamperError(f"unparseable {self.path.name}: {err}") from err
            if canon.canonical_json(e) != line:  # hash what is on disk, not a re-serialisation of it
                raise TamperError(f"{self.path.name}: line {len(evs) + 1} is not canonical bytes")
            evs.append(e)
        return evs

    def events(self) -> list:
        return self._verified()

    def _head(self):
        if not self.head_file.exists() and not os.path.islink(self.head_file):
            return None
        return safe_open(self.head_file, [self.head_file.parent]).decode("utf-8").strip()

    def _verified(self) -> list:
        evs = self._read()
        if not evs and (self.started.exists() or os.path.islink(self.started)):
            raise TamperError(f"{self.path.name} is gone but the chain had started")
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
        with self.lock():
            return self._append(type, payload, row_sha, file, file_sha)

    def _append(self, type, payload, row_sha, file, file_sha) -> dict:
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
        if not evs:
            atomic_write(self.started, b"1")
        append_bytes(self.path, canon.canonical_json(e) + b"\n")
        atomic_write(self.head_file, e["sha"].encode("utf-8"))
        if self.verify() != e["sha"]:
            raise HarnessError("chain verify failed after append")
        return e

    def latest_file_sha(self, file: str):
        for e in reversed(self._verified()):
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
