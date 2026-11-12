#!/usr/bin/env python3
"""Run the tests in parallel shards: python3 tools/suite.py [pytest options] [paths]

No path means the whole of tests/. Each shard is one pytest process; tests/conftest.py reads HARNESS_SHARD and
keeps the tests whose node id falls in that shard. Two rules keep a parallel run as strict as a serial one:

- the shards must together run every selected test, counted against one collection of the whole selection, or
  the run fails;
- a file with a failure runs again alone in one process, and that run decides. A test that fails only because
  the machine is busy does not fail the run; a test that is wrong fails both times.

HARNESS_JOBS sets the number of shards (default: one per core, at most 10).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)")
COLLECTED = re.compile(r"(\d+)(?:/\d+)? tests? collected")
FAILED = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+\.py)", re.M)
TAIL = 40  # lines of a failing log shown; the whole log stays on disk


def start(base: Path, name: str, args: list, env: dict) -> subprocess.Popen:
    """One pytest process; its output goes to <base>/<name>.log."""
    with open(base / f"{name}.log", "w") as log:
        return subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 f"--basetemp={base / name}", *args], cwd=ROOT, env=env, stdout=log,
                                stderr=subprocess.STDOUT)


def lines(base: Path, name: str) -> list:
    return (base / f"{name}.log").read_text("utf-8", "replace").strip().splitlines() or [""]


def main(argv: list) -> int:
    paths = [a for a in argv if (ROOT / a.split("::")[0]).exists()]
    options = [a for a in argv if a not in paths]
    args = options + (paths or ["tests"])
    jobs = int(os.environ.get("HARNESS_JOBS") or min(os.cpu_count() or 2, 10))
    base = Path(tempfile.mkdtemp(prefix="dh"))
    # one hash seed for every process: a test list built from a set has the same order and ids in each
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    began = time.time()

    whole = start(base, "collect", ["--collect-only", *args], env)
    shards = [start(base, f"s{k}", args, dict(env, HARNESS_SHARD=f"{k}/{jobs}")) for k in range(jobs)]
    ran, failed, broken = {}, set(), []
    for k, proc in enumerate(shards):
        code = proc.wait()
        text = lines(base, f"s{k}")
        for n, name in COUNT.findall(text[-1]):
            name = "error" if name == "errors" else name
            ran[name] = ran.get(name, 0) + int(n)
        if code == 1:
            failed.update(FAILED.findall("\n".join(text)))
        if code not in (0, 1, 5) or (code == 1 and not FAILED.search("\n".join(text))):  # 5: shard drew no test
            broken.append(f"s{k}")
    if whole.wait() != 0:
        broken.append("collect")
    found = COLLECTED.search(lines(base, "collect")[-1])
    expected = int(found.group(1)) if found else -1

    print(f"shards: {', '.join(f'{n} {name}' for name, n in sorted(ran.items())) or 'no test ran'} "
          f"in {time.time() - began:.0f}s ({jobs} shards, {expected} tests selected)")
    # a test that passes and then fails in teardown counts twice, so with errors the sum may exceed the selection
    count = sum(ran.values())
    if broken or not count - ran.get("error", 0) <= expected <= count or not count:
        for name in broken:
            print(f"--- {name} did not finish; last lines of {base}/{name}.log")
            print("\n".join(lines(base, name)[-TAIL:]))
        print(f"FAIL: the shards do not add up to one whole run ({count} ran, {expected} selected). Logs: {base}")
        return 2
    if not failed:
        shutil.rmtree(base, ignore_errors=True)
        print("PASS")
        return 0

    # shortcut: the second run takes whole files, so it also runs tests of those files that the caller's
    # node ids left out; narrow it when a red-first step needs an exact list
    files = sorted(failed)
    print(f"failures in {len(files)} file(s); each file runs again alone: {' '.join(files)}")
    code = start(base, "again", options + files, env).wait()
    text = lines(base, "again")
    if code == 0:
        shutil.rmtree(base, ignore_errors=True)
        print(f"PASS: the failures did not repeat when the files ran alone ({text[-1]})")
        return 0
    print("\n".join(text[-TAIL:]))
    print(f"FAIL. Whole log: {base}/again.log")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
