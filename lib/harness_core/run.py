"""``harness-run <check_id> --unit <id> --attempt <n> [--input FILE]... [--args FILE | --arg k=v]...``: the writer
that runs a registered pack check and records its output (spec §Trust boundaries "recorded outputs"; §Gate
adapter "a hook-recorded output of a registered entrypoint"; build phase P5).

Its own PostToolUse entry (matcher ``Bash``, 300 s, watchdog 295 s); the main PostToolUse entry treats
``harness-run`` as print-only. The hook, never the agent, runs the check:

- the task is T1/T2, owned by this session (lease) and not stopped; the unit is in the frozen scope; the check
  is registered and its entry matches the manifest hash (``packs.entry``);
- each ``--input`` is a regular file inside the workspace, outside every protected path, at most 20 MB;
- the check runs under ``sandbox_exec`` (no network; writes only in its temp cwd; the data root and every
  plugin's secrets unreadable) with the layout ``checklib`` describes;
- the output row (``Outputs.record``) keeps the check's stdout, its stdin and a snapshot of every input as
  blobs, so the gate oracle can replay the check on the submitted deliverable (``oracle.py``).

A check that exits non-zero, prints no result line, or runs over its timeout is recorded as ``error`` and can
never back a PASS.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import business, canon, packs, protected, sandbox_exec
from .errors import Denied, HarnessError, TamperError
from .events import EventLog
from .ledger import Ledger, Outputs
from .paths import job_paths
from .safeio import safe_open

MAX_INPUT = 20 * 1024 * 1024
MAX_INPUTS = 20
MAX_ARGS = 64 * 1024
MAX_STDOUT = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 120
PYTHON = "/usr/bin/python3"


def _written(ctx, sid) -> set:
    """Files this session wrote (Write/Edit and Bash targets) plus the task folders: never an owner's source."""
    from . import written
    if not sid:
        return set()
    return {os.path.realpath(x) for x in written.self_written(ctx.data_root, sid, ctx.workspace)}


def _input(ctx, raw, agent_written=frozenset()) -> tuple:
    """(source path relative to the workspace, bytes) of one --input, or Denied."""
    import stat as _stat
    if not isinstance(raw, str) or not raw:
        raise Denied("an --input must be a file path")
    ws = Path(os.path.realpath(ctx.workspace))
    path = Path(raw) if os.path.isabs(raw) else Path(ctx.workspace) / raw
    real = Path(os.path.realpath(path))
    if (protected.outside_workspace(str(real), ctx) or protected.is_protected(str(real), ctx)
            or protected.is_read_protected(str(real), ctx)):
        raise Denied(f"input {raw!r} is outside the workspace or protected")
    if any(str(real) == w or str(real).startswith(w.rstrip("/") + "/") for w in agent_written):
        raise Denied(f"input {raw!r} was written in this session: a check reads the owner's own files, never a copy "
                     "the assistant made")
    try:
        st = os.lstat(path)
        if not _stat.S_ISREG(st.st_mode):  # a FIFO or device would block the hook until its watchdog
            raise Denied(f"input {raw!r} is not a regular file")
        if real.stat().st_size > MAX_INPUT:
            raise Denied(f"input {raw!r} is larger than {MAX_INPUT // (1024 * 1024)} MB")
        data = safe_open(path, [ws])
    except (OSError, TamperError) as e:
        raise Denied(f"input {raw!r} cannot be read safely: {e}") from e
    return real.relative_to(ws).as_posix(), data


def _copy_tree(src: Path, dst: Path, root: Path) -> None:
    from .gate import _files
    for f in _files(src) if src.is_dir() else []:
        (dst / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
        (dst / f.relative_to(src)).write_bytes(safe_open(f, [root]))


def _run_files(p, log, names, box: Path) -> None:
    """Copies of the run-state files a check reads, each checked against its event first."""
    run = box / "run"
    run.mkdir()
    for n in names:
        if n == "snapshots":
            _copy_tree(p.run / "snapshots", run / "snapshots", p.run)
        elif n == "card":
            from .record import _cards
            cards = _cards(p, log)
            (run / "card.json").write_bytes(canon.canonical_json(cards[-1] if cards else {}))
        elif n == "scope":
            (run / "scope.json").write_bytes(canon.canonical_json(Ledger(p, log).sanctioned_scope()))
        elif (p.run / n).exists():
            log.check_file(p.run / n, n)
            (run / n).write_bytes(safe_open(p.run / n, [p.run]))


def layout(box: Path, inputs, work_src: Path, work_root: Path) -> list:
    """Writes inputs/ and work/ under ``box``; returns the stdin ``inputs`` list."""
    (box / "inputs").mkdir(parents=True)
    rows = []
    for i, (src, data) in enumerate(inputs, 1):
        name = f"{i}-{os.path.basename(src)}"
        (box / "inputs" / name).write_bytes(data)
        rows.append({"name": os.path.basename(src), "path": f"inputs/{name}", "sha256": canon.sha256_bytes(data),
                     "source": src})
    (box / "work").mkdir()
    _copy_tree(work_src, box / "work", work_root)
    return rows


def parse(code, out: bytes):
    """(result, values, notes) from a check's stdout: only an exit-0 run with a result line counts."""
    lines = [x for x in out.decode("utf-8", "replace").splitlines() if x.strip()]
    try:
        last = json.loads(lines[-1]) if lines else None
    except ValueError:
        last = None
    if code != 0 or not isinstance(last, dict) or last.get("result") not in ("pass", "fail") \
            or not isinstance(last.get("values"), dict):
        return "error", {}, [f"the check exited {code} without a result line"]
    notes = [str(n)[:500] for n in last.get("notes") or [] if isinstance(n, (str, int, float))][:50]
    return last["result"], last["values"], notes


def execute(entry: Path, box: Path, stdin: bytes, timeout: int, ctx, runner=None):
    runner = runner or sandbox_exec.run
    data = os.path.realpath(ctx.data_root)
    deny_read = [data] + [os.path.realpath(d) for d in sandbox_exec.secret_dirs(data, Path.home())]
    deny_write = [os.path.realpath(ctx.workspace), data, os.path.realpath(ctx.plugin_root)]
    return runner([PYTHON, "-I", str(entry)], str(box), stdin, timeout=timeout, deny_read=deny_read,
                  deny_write=deny_write)


def apply(req, ctx, evt, runner=None) -> dict:
    from . import budget, contacts, lease
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("harness-run belongs to a task: there is no open task")
    lease.require(ctx, job, evt.get("session_id"))
    if contacts.tier(ctx) not in ("T1", "T2"):
        raise Denied("only T1/T2 tasks run pack checks")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    stopped = budget.latest_stop(log)
    if stopped:
        raise Denied(f"task {job} is {stopped}; resume it first")
    check_id, unit_id, attempt = req.get("check_id"), req.get("unit"), req.get("attempt")
    if type(attempt) is not int or attempt < 1:
        raise Denied("--attempt must be a whole number >= 1")
    entry = packs.entry(ctx, check_id)
    s = packs.spec(ctx, check_id)
    unit = next((u for u in Ledger(p, log).sanctioned_scope() if u["id"] == unit_id), None)
    if unit is None:
        raise Denied(f"unit {unit_id!r} is not in the frozen scope")
    args = req.get("args") or {}
    if not isinstance(args, dict) or len(canon.canonical_json(args)) > MAX_ARGS:
        raise Denied("--args must be a JSON object of at most 64 KB")
    raw_inputs = req.get("inputs") or []
    if not isinstance(raw_inputs, list) or len(raw_inputs) > MAX_INPUTS:
        raise Denied(f"at most {MAX_INPUTS} inputs")
    mine = _written(ctx, evt.get("session_id"))
    inputs = [_input(ctx, x, mine) for x in raw_inputs]
    box = Path(os.path.realpath(tempfile.mkdtemp(prefix="harness-run-")))
    try:
        from .gate import _literal_dir
        work = _literal_dir(p, p.work)  # a symlink on the way to work/ is tamper, as at submission
        rows = layout(box, inputs, work or p.work, Path(os.path.realpath(ctx.workspace)))
        _run_files(p, log, s.get("run_files") or [], box)
        from .record import _cards
        card = (_cards(p, log) or [{}])[-1]
        stdin = canon.canonical_json({
            "check_id": check_id, "unit": unit, "attempt": attempt, "args": args, "work": "work", "run": "run",
            "inputs": [{k: r[k] for k in ("name", "path", "sha256")} for r in rows],
            "business": business.snapshot(ctx), "today": time.strftime("%Y-%m-%d"),
            "card": {k: card.get(k) for k in ("task_type", "deliverable", "oracle", "decision_class", "requester")},
        })
        timeout = min(int(s.get("timeout") or DEFAULT_TIMEOUT), packs.TIMEOUT_MAX)
        code, out, err = execute(entry, box, stdin, timeout, ctx, runner)
        work_sha = canon.tree_sha(*_tree(box / "work"))
    finally:
        shutil.rmtree(box, ignore_errors=True)
    out = (out or b"")[:MAX_STDOUT]
    result, values, notes = parse(code, out)
    if result == "error" and err:
        notes.append(err.decode("utf-8", "replace")[-300:] if isinstance(err, bytes) else str(err)[-300:])
    extra = {"inputs": [{"source": r["source"], "sha256": r["sha256"]} for r in rows],
             "stdin_sha": canon.sha256_bytes(stdin), "work_sha": work_sha, "notes": notes,
             "pack": f"{s['pack']}@{s['pack_version']}", "entry_sha": s["sha256"]}
    row = Outputs(p, log).record(check_id, unit_id, attempt, code if isinstance(code, int) else -1, result, values,
                                 out, extra=extra, blobs=[stdin] + [d for _, d in inputs])
    return {"check_id": check_id, "unit": unit_id, "attempt": attempt, "result": result, "sha256": row["sha256"],
            "values": values, "notes": notes}


def _tree(root: Path):
    from .gate import _files
    return (_files(root) if root.is_dir() else []), root


def _say(res) -> str:
    vals = json.dumps(res["values"], ensure_ascii=False, sort_keys=True)
    notes = ("; notes: " + " | ".join(res["notes"])) if res["notes"] else ""
    if res["result"] == "error":
        return (f"harness: {res['check_id']} for {res['unit']} (attempt {res['attempt']}) did not run cleanly; "
                f"output {res['sha256']} is recorded as an error and cannot back a PASS{notes}")
    return (f"harness: {res['check_id']} for {res['unit']} (attempt {res['attempt']}): {res['result']}. Recorded "
            f"output {res['sha256']}; cite it in the unit's ledger row (outputs: [\"{res['sha256']}\"]). "
            f"Values: {vals[:1500]}{notes}")


def on_post(evt, ctx) -> dict:
    from .writers import _lone_harness, _request
    if evt.get("tool_name") != "Bash":
        return {}
    parsed = _lone_harness((evt.get("tool_input") or {}).get("command"))
    if parsed is None or parsed[0] != "run":
        return {}
    try:
        res = apply(_request(evt, "run"), ctx, evt)
    except HarnessError as e:
        return {"decision": "block", "reason": f"harness-run not recorded: {e}"}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": _say(res)}}
