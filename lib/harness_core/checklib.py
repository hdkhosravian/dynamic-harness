"""The protocol between ``harness-run`` (and the gate oracle) and a pack check entry (build phase P5).

A check runs as ``/usr/bin/python3 -I <entry>`` in a sandbox (no network, writes only in its temp cwd), with
its cwd laid out as::

    inputs/<n>-<name>   copies of the files named with --input (read-only snapshots)
    work/               the task's deliverables (at harness-run time: work/; at gate time: the submitted copy)
    run/                the run-state files the manifest's run_files names

and one JSON object on stdin: ``{check_id, unit, attempt, args, inputs: [{name, path, sha256}], work, run,
business, card, today}``. It prints one JSON line last: ``{"result": "pass" | "fail", "values": {...},
"notes": [...], "note_codes": [...]}`` and exits 0; any other exit is an error. Earlier lines may carry
attachments (``{"attachment": "rows", "headers": [...], "rows": [...]}``), kept with the recorded output. ``values`` holds numbers the report may cite
(R-11: every aggregate is computed here, never by the model); ``notes`` holds plain-language findings
(left-out rows, disagreeing sources) that the report must show.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal


def safe_path(base, rel) -> "Path":
    """``rel`` under ``base`` (the box's work/, inputs/ or run/), or ValueError: no absolute path, no ``..``, no
    symlink out. Agent-given names never reach files outside the box (the sandbox may read more than the box)."""
    import os
    from pathlib import Path, PurePosixPath
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or "\\" in rel:
        raise ValueError(f"{rel!r} must be a file name inside {base}")
    if any(part in ("..", "") for part in PurePosixPath(rel).parts):
        raise ValueError(f"{rel!r} must not leave {base}")
    root = Path(os.path.realpath(base))
    full = Path(os.path.realpath(root / rel))
    if root != full and root not in full.parents:
        raise ValueError(f"{rel!r} must not leave {base}")
    return full


ATTACHMENT_MAX = 3 * 1024 * 1024  # harness-run keeps 5 MB of stdout; the result line comes after the attachments


class Note(str):
    """A note with a stable code and its parameters, so owner-facing text can render it in the owner's language
    (``templates`` key ``note.<code>``); the string itself is the English text."""

    def __new__(cls, code, text, **params):
        n = super().__new__(cls, text)
        n.code, n.params = code, params
        return n


def read_request() -> dict:
    req = json.loads(sys.stdin.read() or "{}")
    if not isinstance(req, dict):
        raise SystemExit("check request is not a JSON object")
    return req


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def emit(result: str, values: dict, notes=()) -> None:
    if result not in ("pass", "fail"):
        raise ValueError(f"result must be pass or fail, not {result!r}")
    codes = [{"code": n.code, "params": _plain(n.params)} if isinstance(n, Note) else None for n in notes]
    print(json.dumps({"result": result, "values": _plain(values), "notes": [str(n) for n in notes],
                      "note_codes": codes}, ensure_ascii=False, sort_keys=True, default=str))


def main(fn) -> None:
    """Run ``fn(request) -> (result, values, notes)``; an exception is an error exit (never a pass)."""
    try:
        out = fn(read_request())
    except Exception as e:  # the runner records exit 3 as an error, never as a check result
        print(f"check error: {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(3)
    result, values, notes = out[:3]
    for name, body in (out[3] if len(out) > 3 else {}).items():  # before the result line, which stays last
        body = _plain(body)
        line = json.dumps({"attachment": name, **body}, ensure_ascii=False, sort_keys=True, default=str)
        while len(line.encode("utf-8")) > ATTACHMENT_MAX and body.get("rows"):  # the result line must fit
            body = dict(body, rows=body["rows"][: len(body["rows"]) // 2], truncated=True)
            line = json.dumps({"attachment": name, **body}, ensure_ascii=False, sort_keys=True, default=str)
        print(line)
    emit(result, values, notes)


def attachment(out: bytes, name: str):
    """An attachment a check printed (``{"attachment": name, ...}``), or None."""
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith('{"attachment"') or '"attachment":' in line[:200]:
            try:
                v = json.loads(line)
            except ValueError:
                continue
            if isinstance(v, dict) and v.get("attachment") == name:
                return v
    return None
