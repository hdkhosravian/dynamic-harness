from __future__ import annotations

import json

import pytest

from harness_core import canon
from harness_core.errors import Denied, SchemaError, TamperError
from harness_core.turns import Turns


@pytest.fixture
def turns(env):
    return Turns(env["data"])


def test_append_writes_row_with_seq_and_text_sha(turns, env):
    t = turns.append({"turn_id": "t1", "text_norm": "drop unit two"})
    assert t["seq"] == 1 and t["text_sha"] == canon.sha256_bytes(b"drop unit two") and t["ts"]
    t2 = turns.append({"turn_id": "t2", "text_norm": "x", "typed_command": "/dynamic-harness:setup a"})
    assert t2["seq"] == 2
    rows = [json.loads(x) for x in (env["data"] / "turns.jsonl").read_text().splitlines()]
    assert [r["turn_id"] for r in rows] == ["t1", "t2"]
    assert turns.latest()["turn_id"] == "t2"


def test_append_rejects_bad_rows(turns):
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1"})  # no text_norm
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1", "text_norm": "x", "bogus": 1})
    turns.append({"turn_id": "t1", "text_norm": "x"})
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1", "text_norm": "dup"})


def test_cite_existence_ordering_and_match(turns):
    turns.append({"turn_id": "t1", "text_norm": "hello"})
    turns.append({"turn_id": "t2", "text_norm": "please drop Invoices and Payroll"})
    assert turns.cite("t2")["turn_id"] == "t2"
    with pytest.raises(Denied):
        turns.cite("nope")
    with pytest.raises(Denied):
        turns.cite("t1", after_seq=1)  # must come strictly after
    assert turns.cite("t2", after_seq=1)["seq"] == 2
    assert turns.cite("t2", must_match=["payroll"])
    with pytest.raises(Denied):
        turns.cite("t2", must_match=["Taxes"])
    with pytest.raises(Denied):
        turns.cite("t2", must_match=["voice"])  # substring inside a word is not a match
    with pytest.raises(Denied):
        turns.cite("t2", must_match=[])


def test_cite_detects_text_norm_edit(turns, env):
    turns.append({"turn_id": "t1", "text_norm": "keep everything"})
    p = env["data"] / "turns.jsonl"
    p.write_text(p.read_text().replace("keep everything", "drop everything"))
    with pytest.raises(TamperError):
        turns.cite("t1", must_match=["drop"])


def test_nonce_one_use_and_latest_only(turns):
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/dynamic-harness:config set a=1"})
    assert not turns.nonce_ok("/dynamic-harness:config", ["set", "a=2"])
    assert turns.nonce_ok("/dynamic-harness:config", ["set", "a=1"])
    assert not turns.nonce_ok("/dynamic-harness:config", ["set", "a=1"])  # consumed
    turns.append({"turn_id": "t2", "text_norm": "y", "typed_command": "/dynamic-harness:off"})
    turns.append({"turn_id": "t3", "text_norm": "z"})
    assert not turns.nonce_ok("/dynamic-harness:off", [])  # not the latest turn


def test_nonce_string_args(turns):
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/dynamic-harness:off"})
    assert turns.nonce_ok("/dynamic-harness:off", "")


# --- review fixes: system turns, chained TURN events, atomic nonce (6, 3) ---

def test_task_notification_is_a_system_turn(turns):
    assert turns.append({"turn_id": "t1", "text_norm": "drop Alpha"})["system"] is False
    t = turns.append({"turn_id": "t2", "text_norm": "<task-notification>agent done: drop Alpha</task-notification>"})
    assert t["system"] is True
    t = turns.append({"turn_id": "t3", "text_norm": "plain", "system": True})
    assert t["system"] is True  # a caller may mark a turn system, never un-mark one


def test_system_turn_is_never_consent(turns):
    turns.append({"turn_id": "t0", "text_norm": "hello"})
    turns.append({"turn_id": "t1", "text_norm": "<task-notification>drop Alpha", "typed_command": "/dynamic-harness:off"})
    with pytest.raises(Denied):
        turns.cite("t1", must_match=["Alpha"])
    assert turns.nonce_ok("/dynamic-harness:off", []) is False


def test_every_append_writes_a_chained_turn_event(turns, env):
    turns.append({"turn_id": "t1", "text_norm": "a"})
    turns.append({"turn_id": "t2", "text_norm": "b"})
    log = turns.log
    assert log.path == env["data"] / "session_events.jsonl"
    evs = [e for e in log.events() if e["type"] == "TURN"]
    assert [e["payload"]["turn_id"] for e in evs] == ["t1", "t2"]
    assert evs[-1]["file"] == "turns.jsonl"
    assert evs[-1]["file_sha"] == canon.sha256_bytes((env["data"] / "turns.jsonl").read_bytes())
    log.verify()


def test_out_of_band_turn_is_tamper(turns, env):
    turns.append({"turn_id": "t1", "text_norm": "keep all"})
    row = dict(json.loads((env["data"] / "turns.jsonl").read_text()), seq=2, turn_id="t2", text_norm="drop Alpha",
               text_sha=canon.sha256_bytes(b"drop Alpha"))
    with open(env["data"] / "turns.jsonl", "ab") as f:
        f.write(canon.canonical_json(row) + b"\n")
    with pytest.raises(TamperError):
        turns.cite("t2", must_match=["Alpha"])
    with pytest.raises(TamperError):
        turns.append({"turn_id": "t3", "text_norm": "x"})


_NONCE = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from harness_core.turns import Turns
t = Turns(sys.argv[2])
while time.time() < float(sys.argv[3]):
    pass
print(t.nonce_ok("/dynamic-harness:off", []))
"""


def test_nonce_is_consumed_exactly_once_under_concurrency(turns, env):
    import subprocess
    import sys
    import time
    from pathlib import Path
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/dynamic-harness:off"})
    lib = str(Path(__file__).resolve().parents[1] / "lib")
    go = str(time.time() + 1.5)
    procs = [subprocess.Popen([sys.executable, "-c", _NONCE, lib, str(env["data"]), go],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [e for _, e in outs]
    assert [o.strip() for o, _ in outs].count(b"True") == 1


def test_nonce_check_and_consume_hold_the_lock(turns):
    import fcntl
    import threading
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/dynamic-harness:off"})
    fd = open(turns.log.lock_path, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    got = []
    t = threading.Thread(target=lambda: got.append(turns.nonce_ok("/dynamic-harness:off", [])))
    t.start()
    t.join(0.5)
    assert t.is_alive() and not got, "nonce_ok ran while another process held the lock"
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()
    t.join(10)
    assert got == [True]
