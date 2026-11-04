"""The protocol between ``harness-run`` (and the gate oracle) and a pack check entry (build phase P5).

A check runs as ``/usr/bin/python3 -I <entry>`` in a sandbox (no network, writes only in its temp cwd), with
its cwd laid out as::

    inputs/<n>-<name>   copies of the files named with --input (read-only snapshots)
    work/               the task's deliverables (at harness-run time: work/; at gate time: the submitted copy)
    run/                the run-state files the manifest's run_files names

and one JSON object on stdin: ``{check_id, unit, attempt, args, inputs: [{name, path, sha256}], work, run,
business, card, today}``. It prints one JSON line last: ``{"result": "pass" | "fail", "values": {...},
"notes": [...]}`` and exits 0; any other exit is an error. ``values`` holds numbers the report may cite
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
    print(json.dumps({"result": result, "values": _plain(values), "notes": [str(n) for n in notes]},
                     ensure_ascii=False, sort_keys=True))


def main(fn) -> None:
    """Run ``fn(request) -> (result, values, notes)``; an exception is an error exit (never a pass)."""
    try:
        result, values, notes = fn(read_request())
    except Exception as e:  # the runner records exit 3 as an error, never as a check result
        print(f"check error: {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(3)
    emit(result, values, notes)
