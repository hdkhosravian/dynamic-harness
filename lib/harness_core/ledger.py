"""Ledger rows, recorded outputs and scope versions — the only writers of
run/{ledger,verdicts,decisions,contradictions,milestones}.jsonl,
run/outputs/outputs.jsonl and run/scope.v<n>.jsonl.

Every write appends the file, then an event carrying row_sha and the file's
new file_sha. Each write first checks the file against its last file_sha, and
holds the event log's lock from that check to the event. A crash between the
row file and its event fails closed (TamperError on the next check); operator
repair is the recovery path.

Scope versions (R-3) live here: freeze_scope() writes v1; descope() writes
v(n+1) and needs a cited turn after the latest freeze that names every unit it
removes, plus one DESCOPE event per unit. sanctioned_scope() replays the SCOPE
events and raises HarnessError on any unit that vanished without DESCOPE."""
from __future__ import annotations

import json

from . import canon, schema
from .errors import Denied, HarnessError, TamperError
from .safeio import append_bytes, atomic_write, safe_open

FILES = {
    "unit": "ledger.jsonl",
    "verdict": "verdicts.jsonl",
    "decision": "decisions.jsonl",
    "contradiction": "contradictions.jsonl",
    "milestone": "milestones.jsonl",
}
ROW_SCHEMA = {"unit": "ledger_row", "verdict": "verdict_row"}
MILESTONES = ("outline", "draft", "final")
OUTPUTS = "outputs/outputs.jsonl"


def _append_row(paths, log, name: str, row: dict, type: str, payload: dict) -> dict:
    path = paths.run / name
    with log.lock():
        log.check_file(path, name)
        append_bytes(path, canon.canonical_json(row) + b"\n")
        file_sha = canon.sha256_bytes(safe_open(path, [paths.run]))
        return log.append(type, payload, row_sha=canon.sha256_json(row), file=name, file_sha=file_sha)


class Outputs:
    def __init__(self, paths, log):
        self.paths, self.log = paths, log

    def record(self, check_id, unit, attempt, exit_code, result, values, data: bytes, extra=None, blobs=()) -> dict:
        """``extra``: provenance fields (inputs, stdin_sha, …); ``blobs``: more bytes to keep by sha (the
        check's stdin and input snapshots, so the gate oracle can replay the check)."""
        row = {"check_id": check_id, "unit": unit, "attempt": attempt, "exit_code": exit_code,
               "result": result, "values": values, "sha256": canon.sha256_bytes(data), **(extra or {})}
        schema.check(row, "outputs_row")
        for b in blobs:
            atomic_write(self.paths.run / "outputs" / "blobs" / canon.sha256_bytes(b), b)
        atomic_write(self.paths.run / "outputs" / "blobs" / row["sha256"], data)
        _append_row(self.paths, self.log, OUTPUTS, row, "OUTPUT", {"row": row})
        return row

    def rows(self) -> list:
        return [e["payload"]["row"] for e in self.log.events() if e["type"] == "OUTPUT"]


class Ledger:
    def __init__(self, paths, log):
        self.paths, self.log = paths, log

    # --- scope versions --------------------------------------------------------
    def _scope_events(self) -> list:
        return [e for e in self.log.events() if e["type"] == "SCOPE"]

    def _write_scope(self, rows: list, turns, removed=()) -> dict:
        ids = [r.get("id") for r in rows]
        for r in rows:
            schema.check(r, "scope_row")
        if len(set(ids)) != len(ids):
            raise HarnessError("duplicate unit id in scope")
        version = len(self._scope_events()) + 1
        name = f"scope.v{version}.jsonl"
        data = canon.scope_bytes(rows)
        atomic_write(self.paths.run / name, data)
        latest = turns.latest() if turns is not None else None
        payload = {"version": version, "count": len(rows), "scope_sha": canon.sha256_bytes(data),
                   "turn_seq": latest["seq"] if latest else 0, "removed": list(removed)}
        self.log.append("SCOPE", payload, file=name, file_sha=payload["scope_sha"])
        return payload

    def freeze_scope(self, rows: list, turns=None) -> dict:
        with self.log.lock():
            if self._scope_events():
                raise HarnessError("scope already frozen; change it only through descope")
            return self._write_scope(list(rows), turns)

    def _read_scope(self, event) -> list:
        name = event["file"]
        path = self.paths.run / name
        self.log.check_file(path, name)
        if canon.sha256_bytes(safe_open(path, [self.paths.run])) != event["payload"]["scope_sha"]:
            raise TamperError(f"{name}: bytes differ from its SCOPE event")
        return [json.loads(x) for x in safe_open(path, [self.paths.run]).splitlines()]

    def scope(self) -> list:
        evs = self._scope_events()
        return self._read_scope(evs[-1]) if evs else []

    def descope(self, unit_ids, turn_id: str, turns) -> dict:
        with self.log.lock():
            return self._descope(unit_ids, turn_id, turns)

    def _descope(self, unit_ids, turn_id: str, turns) -> dict:
        evs = self._scope_events()
        if not evs:
            raise HarnessError("no frozen scope")
        current = self._read_scope(evs[-1])
        by_id = {r["id"]: r for r in current}
        after = evs[-1]["payload"]["turn_seq"]
        if not unit_ids:
            raise Denied("descope names no units")
        for uid in unit_ids:
            if uid not in by_id:
                raise Denied(f"{uid} is not in the current scope")
            turns.cite(turn_id, after_seq=after, must_match=[by_id[uid]["label"], uid])
        version = len(evs) + 1
        for uid in unit_ids:
            self.log.append("DESCOPE", {"unit": uid, "turn_id": turn_id, "version": version})
        return self._write_scope([r for r in current if r["id"] not in unit_ids], turns, removed=unit_ids)

    def sanctioned_scope(self) -> list:
        """Latest scope, after checking every version's bytes and that each removal has a DESCOPE."""
        events = self.log.events()
        descoped = {(e["payload"]["unit"], e["payload"]["version"]) for e in events if e["type"] == "DESCOPE"}
        prev, rows = None, []
        for e in (x for x in events if x["type"] == "SCOPE"):
            rows = self._read_scope(e)
            ids = {r["id"] for r in rows}
            if prev is not None:
                silent = sorted(u for u in prev - ids if (u, e["payload"]["version"]) not in descoped)
                if silent:
                    raise HarnessError(f"scope v{e['payload']['version']} drops {silent} without DESCOPE")
            prev = ids
        return rows

    # --- rows --------------------------------------------------------------------
    def rows(self) -> list:
        """[(kind, row)] in append order, from the verified event log."""
        return [(e["payload"]["kind"], e["payload"]["row"]) for e in self.log.events() if e["type"] == "ROW"]

    def state(self, unit_id: str) -> str:
        if unit_id not in {r["id"] for r in self.scope()} and any(
            e["type"] == "DESCOPE" and e["payload"]["unit"] == unit_id for e in self.log.events()
        ):
            return "DESCOPED_BY_USER"
        states = [r["verdict"] for k, r in self.rows() if k in ROW_SCHEMA and r["id"] == unit_id]
        return states[-1] if states else "OPEN"

    def _cited(self, row: dict) -> list:
        want = set(row.get("outputs") or [])
        if not want:
            raise Denied(f"{row['id']}: {row['verdict']} cites no recorded output")
        got = [o for o in Outputs(self.paths, self.log).rows() if o["sha256"] in want and o["unit"] == row["id"]]
        missing = want - {o["sha256"] for o in got}
        if missing:
            raise Denied(f"{row['id']}: cites unrecorded outputs {sorted(missing)}")
        return got

    def _deliverable_present(self, unit: dict) -> bool:
        if not unit.get("deliverable"):
            return False
        try:
            safe_open(self.paths.work / unit["deliverable"], [self.paths.work])
            return True
        except TamperError:
            return False

    def append_row(self, kind: str, row: dict) -> dict:
        with self.log.lock():  # the rule reads earlier rows; nothing may land in between
            return self._append(kind, row)

    def _append(self, kind: str, row: dict) -> dict:
        if kind not in FILES:
            raise HarnessError(f"unknown row kind {kind!r}")
        row = dict(row)
        if kind == "milestone":
            schema.validate(row, {"type": "object", "required": ["milestone", "file"],
                                  "properties": {"milestone": {"enum": list(MILESTONES)}, "file": {"type": "string"}},
                                  "additionalProperties": False})
            try:
                row["sha256"] = canon.sha256_bytes(safe_open(self.paths.work / row["file"], [self.paths.work]))
            except TamperError as e:
                raise Denied(f"milestone needs a hashed work/ file: {e}") from e
        elif kind in ROW_SCHEMA:
            schema.check(row, ROW_SCHEMA[kind])
            row = self._unit_rule(kind, row)
        else:
            schema.validate(row, {"type": "object"})
        _append_row(self.paths, self.log, FILES[kind], row, "ROW", {"kind": kind, "row": row})
        return row

    def _unit_rule(self, kind: str, row: dict) -> dict:
        unit = next((u for u in self.scope() if u["id"] == row["id"]), None)
        if unit is None:
            raise Denied(f"{row['id']} is not in the current scope")
        v = row["verdict"]
        if v == "PASS_FLAGGED":
            raise Denied("PASS_FLAGGED is written only by the 2nd-FAIL rule")
        if v in ("BLOCKED", "NOT_INVESTIGATED") and not row.get("reason"):
            raise Denied(f"{v} needs a reason")
        if v == "PASS":
            for o in self._cited(row):
                if o["attempt"] != row["attempt"]:
                    raise Denied(f"{row['id']}: cited output {o['sha256'][:12]} is from attempt {o['attempt']}, "
                                 f"not attempt {row['attempt']}")
                if o["exit_code"] != 0 or o["result"] != "pass" or o["check_id"] != unit.get("check_id"):
                    raise Denied(f"{row['id']}: cited output {o['sha256'][:12]} is not a clean pass "
                                 f"of registered check {unit.get('check_id')!r}")
        if v in ("SUPPORTED", "REFUTED"):
            if not any(o["check_id"] == "research.check" and o["exit_code"] == 0 for o in self._cited(row)):
                raise Denied(f"{row['id']}: {v} needs a research.check output with exit 0")
        if v == "FAIL":
            fails = sum(1 for k, r in self.rows() if k in ROW_SCHEMA and r["id"] == row["id"] and r["verdict"] == "FAIL")
            if fails >= 1:  # this is the 2nd FAIL
                _append_row(self.paths, self.log, FILES[kind], row, "ROW", {"kind": kind, "row": row})
                cited = [o for o in Outputs(self.paths, self.log).rows()
                         if o["sha256"] in set(row.get("outputs") or []) and o["unit"] == row["id"]]
                from_check = any(o["check_id"] == unit.get("check_id") for o in cited)
                sig = row.get("failure_sig") or row.get("reason") or f"{unit.get('check_id')} failed twice"
                out = {"id": row["id"], "attempt": row["attempt"]}
                if from_check and self._deliverable_present(unit):
                    out.update(verdict="PASS_FLAGGED", failure_sig=sig, outputs=row.get("outputs", []))
                else:
                    out.update(verdict="BLOCKED", reason=f"failed twice: {sig}")
                return out
        return row
