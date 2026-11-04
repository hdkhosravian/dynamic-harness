"""User turns (data_root/turns.jsonl) and one-use typed-command nonces.

Turn rows carry `seq` (1-based ordinal in turns.jsonl). `after_seq` in cite()
is that ordinal: a writer that needs "a turn after X" records the latest turn
seq when X happens (e.g. the scope freeze stores `turn_seq`)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import canon, schema
from .errors import Denied, SchemaError, TamperError
from .events import now
from .safeio import append_bytes, safe_open


def _read_jsonl(path: Path, root: Path) -> list:
    if not path.exists() and not os.path.islink(path):
        return []
    try:
        return [json.loads(x) for x in safe_open(path, [root]).splitlines() if x.strip()]
    except ValueError as e:
        raise TamperError(f"unparseable {path.name}: {e}") from e


def _names(text: str, label: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(label.lower()) + r"(?!\w)", text.lower()))


class Turns:
    def __init__(self, data_root):
        self.root = Path(data_root)
        self.path = self.root / "turns.jsonl"
        self.consumed = self.root / "nonces_consumed.jsonl"

    def _rows(self) -> list:
        return _read_jsonl(self.path, self.root)

    def append(self, turn: dict) -> dict:
        rows = self._rows()
        if not isinstance(turn.get("text_norm"), str):
            raise SchemaError("turn needs text_norm")
        row = dict(turn)
        row.setdefault("ts", now())
        row["seq"] = len(rows) + 1
        row["text_sha"] = canon.sha256_bytes(row["text_norm"].encode("utf-8"))
        schema.check(row, "turns")
        if any(r.get("turn_id") == row["turn_id"] for r in rows):
            raise SchemaError(f"duplicate turn_id {row['turn_id']}")
        append_bytes(self.path, canon.canonical_json(row) + b"\n")
        return row

    def latest(self):
        rows = self._rows()
        return rows[-1] if rows else None

    def cite(self, turn_id: str, after_seq=None, must_match=None) -> dict:
        turn = next((r for r in self._rows() if r.get("turn_id") == turn_id), None)
        if turn is None:
            raise Denied(f"unknown turn {turn_id}")
        if canon.sha256_bytes(turn["text_norm"].encode("utf-8")) != turn.get("text_sha"):
            raise TamperError(f"turn {turn_id}: text_norm does not match text_sha")
        if after_seq is not None and turn["seq"] <= after_seq:
            raise Denied(f"turn {turn_id} does not come after seq {after_seq}")
        if must_match is not None and not any(_names(turn["text_norm"], m) for m in must_match):
            raise Denied(f"turn {turn_id} does not name any of {must_match}")
        return turn

    def nonce_ok(self, command: str, args) -> bool:
        turn = self.latest()
        if not turn or not turn.get("typed_command"):
            return False
        parts = [command] + (list(args) if isinstance(args, (list, tuple)) else [args])
        if turn["typed_command"].split() != " ".join(parts).split():
            return False
        if any(r.get("turn_id") == turn["turn_id"] for r in _read_jsonl(self.consumed, self.root)):
            return False
        append_bytes(self.consumed, canon.canonical_json({"turn_id": turn["turn_id"], "ts": now()}) + b"\n")
        return True
