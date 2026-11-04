"""The gate's done-mode oracle: replay every recorded check a passing row cites (spec §Gate adapter "--oracle-cmd
from card.oracle.type, checked against the pack manifest"; build phase P5).

fold_ledger runs it as ``/usr/bin/python3 -I <plugin>/lib/harness_core/oracle.py`` inside the gate sandbox,
with the gate's copy of ``run/`` as the working directory. For every output that a PASS gate row cites
(``outputs/blobs/<sha>`` in its evidence) it:

- finds the output row in ``outputs/outputs.jsonl``; the blob, the recorded stdin and every input snapshot
  must hash to their recorded shas;
- checks that the pack entry still matches both the manifest and the hash recorded when the check ran;
- re-runs the check with the same stdin, the same input snapshots and the *submitted* deliverables
  (``outputs/submitted/``), so a deliverable edited after its check fails here;
- requires the same result (``pass``) and the same ``values``.

Exit 0 only when every replay matches and at least one output was replayed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):  # run as a script by fold_ledger: python3 -I drops the script directory
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "harness_core"

from harness_core import canon, packs  # noqa: E402
from harness_core.run import PYTHON, parse  # noqa: E402

BUDGET = 1700  # seconds for all replays, under the gate's 1790 s oracle timeout
_BLOB = re.compile(r"^outputs/blobs/([0-9a-f]{64})$")


class _Ctx:
    plugin_root = Path(__file__).resolve().parents[2]


def _jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def _blob(run: Path, sha: str) -> bytes:
    data = (run / "outputs" / "blobs" / sha).read_bytes()
    if canon.sha256_bytes(data) != sha:
        raise ValueError(f"blob {sha[:12]} does not match its sha")
    return data


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=False)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _latest_card(run: Path) -> dict:
    cards = sorted(run.glob("card.v*.json"), key=lambda f: int(re.search(r"v(\d+)", f.name).group(1)))
    return json.loads(cards[-1].read_text("utf-8")) if cards else {}


def _layout(run: Path, box: Path, row: dict, req: dict, run_files) -> None:
    by_sha = {i["sha256"]: i for i in req.get("inputs") or []}
    for inp in row.get("inputs") or []:
        meta = by_sha.get(inp["sha256"])
        if meta is None:
            raise ValueError(f"input {inp['source']} is not in the recorded stdin")
        dst = box / meta["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_blob(run, inp["sha256"]))
    (box / "inputs").mkdir(exist_ok=True)
    _copy(run / "outputs" / "submitted", box / "work")
    (box / "work").mkdir(exist_ok=True)
    (box / "run").mkdir()
    for n in run_files:
        if n == "card":
            (box / "run" / "card.json").write_bytes(canon.canonical_json(_latest_card(run)))
        elif n == "scope":
            (box / "run" / "scope.json").write_bytes(canon.canonical_json(_jsonl(run / "gate" / "scope.jsonl")))
        else:
            _copy(run / n, box / "run" / n)


def replay(run: Path, row: dict, deadline: float):
    """None when the replay matches, else why not."""
    stdin = _blob(run, row["stdin_sha"]) if row.get("stdin_sha") else None
    if stdin is None:
        return f"{row['check_id']} {row['sha256'][:12]}: no recorded stdin to replay"
    s = packs.spec(_Ctx, row["check_id"])
    entry = packs.entry(_Ctx, row["check_id"])
    if row.get("entry_sha") and row["entry_sha"] != s["sha256"]:
        return f"{row['check_id']}: the pack entry changed since the check ran"
    box = Path(tempfile.mkdtemp(prefix="harness-replay-"))
    try:
        _layout(run, box, row, json.loads(stdin), s.get("run_files") or [])
        left = deadline - time.time()
        if left <= 1:
            return "oracle time budget used up"
        try:
            p = subprocess.run([PYTHON, "-I", str(entry)], cwd=box, input=stdin, capture_output=True,
                               timeout=min(left, packs.TIMEOUT_MAX))
        except subprocess.TimeoutExpired:
            return f"{row['check_id']} {row['sha256'][:12]}: replay timed out"
        result, values, _ = parse(p.returncode, p.stdout)
    finally:
        shutil.rmtree(box, ignore_errors=True)
    if result != row["result"] or canon.canonical_json(values) != canon.canonical_json(row["values"]):
        return (f"{row['check_id']} for {row['unit']}: replay on the submitted deliverables gave {result} "
                f"{json.dumps(values, sort_keys=True)[:200]}, recorded {row['result']}")
    return None


def main(run=Path(".")) -> int:
    run = Path(os.path.realpath(run))
    deadline = time.time() + BUDGET
    outputs = {r["sha256"]: r for r in _jsonl(run / "outputs" / "outputs.jsonl")}
    cited = []
    for g in _jsonl(run / "gate" / "ledger.jsonl"):
        if g.get("verdict") != "PASS":
            continue
        for ev in g.get("evidence") or []:
            m = _BLOB.match(str(ev))
            if m:
                cited.append(m.group(1))
    problems, done = [], 0
    for sha in dict.fromkeys(cited):
        row = outputs.get(sha)
        if row is None:
            problems.append(f"cited output {sha[:12]} is not in outputs.jsonl")
            continue
        try:
            why = replay(run, row, deadline)
        except Exception as e:  # a broken snapshot or manifest is a failed replay, never a pass
            why = f"{row.get('check_id')} {sha[:12]}: {type(e).__name__}: {e}"
        if why:
            problems.append(why)
        done += 1
    if not done:
        problems.append("no recorded check output is cited by a passing row: nothing to replay")
    for p in problems:
        print(f"oracle: {p}", file=sys.stderr)
    print(f"oracle: replayed {done} recorded check output(s); {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
