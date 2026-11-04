from __future__ import annotations

import json

import pytest

from harness_core import canon
from harness_core.errors import TamperError
from harness_core.events import EventLog


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / "run", tmp_path / "heads" / "j1")


def _lines(log):
    return (log.run_dir / "events.jsonl").read_bytes().splitlines()


def test_empty_log_verifies_to_empty_head(log):
    assert log.verify() == ""
    assert log.events() == []


def test_chain_fields_and_head(log):
    e1 = log.append("A", {"x": 1})
    e2 = log.append("B", {"y": 2}, row_sha="r", file="f", file_sha="s")
    assert (e1["seq"], e1["prev_sha"]) == (1, "")
    assert (e2["seq"], e2["prev_sha"]) == (2, e1["sha"])
    body = {k: v for k, v in e2.items() if k != "sha"}
    assert e2["sha"] == canon.sha256_json(body)
    assert "row_sha" not in e1 and e2["file_sha"] == "s"
    assert log.head_file.read_text() == e2["sha"]
    assert log.verify() == e2["sha"]
    assert [e["type"] for e in log.events()] == ["A", "B"]


def test_verify_detects_edit(log):
    log.append("A", {"x": 1})
    log.append("B", {"x": 2})
    p = log.run_dir / "events.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"x":1', b'"x":9'))
    with pytest.raises(TamperError):
        log.verify()


def test_verify_detects_resealed_edit(log):
    """Re-hashing an edited event still breaks the next event's prev_sha."""
    log.append("A", {"x": 1})
    log.append("B", {"x": 2})
    lines = _lines(log)
    e = json.loads(lines[0])
    e["payload"] = {"x": 9}
    e.pop("sha")
    e["sha"] = canon.sha256_json(e)
    lines[0] = canon.canonical_json(e)
    (log.run_dir / "events.jsonl").write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(TamperError):
        log.verify()


def test_verify_detects_truncation(log):
    log.append("A", {})
    log.append("B", {})
    lines = _lines(log)
    (log.run_dir / "events.jsonl").write_bytes(lines[0] + b"\n")
    with pytest.raises(TamperError):
        log.verify()


def test_verify_detects_reorder(log):
    log.append("A", {})
    log.append("B", {})
    a, b = _lines(log)
    (log.run_dir / "events.jsonl").write_bytes(b + b"\n" + a + b"\n")
    with pytest.raises(TamperError):
        log.verify()


def test_verify_detects_head_mismatch(log):
    log.append("A", {})
    log.head_file.write_text("0" * 64)
    with pytest.raises(TamperError):
        log.verify()


def test_missing_head_with_events_is_tamper(log):
    log.append("A", {})
    log.head_file.unlink()
    with pytest.raises(TamperError):
        log.verify()


def test_append_refuses_on_tampered_chain(log):
    log.append("A", {})
    log.head_file.write_text("bad")
    with pytest.raises(TamperError):
        log.append("B", {})


def test_check_file_tracks_latest_file_sha(log, tmp_path):
    f = log.run_dir / "x.json"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"1")
    log.append("W", {}, file="x.json", file_sha=canon.sha256_bytes(b"1"))
    log.check_file(f, "x.json")
    f.write_bytes(b"2")
    with pytest.raises(TamperError):
        log.check_file(f, "x.json")
    log.append("W", {}, file="x.json", file_sha=canon.sha256_bytes(b"2"))
    assert log.latest_file_sha("x.json") == canon.sha256_bytes(b"2")
    log.check_file(f, "x.json")


def test_check_file_unrecorded_file_is_tamper(log):
    f = log.run_dir / "y.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    log.check_file(f, "y.json")  # absent and never written: fine
    f.write_bytes(b"planted")
    with pytest.raises(TamperError):
        log.check_file(f, "y.json")


def test_check_file_deleted_file_is_tamper(log):
    f = log.run_dir / "z.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"1")
    log.append("W", {}, file="z.json", file_sha=canon.sha256_bytes(b"1"))
    f.unlink()
    with pytest.raises(TamperError):
        log.check_file(f, "z.json")


def test_check_file_recorded_deletion(log):
    """file_sha "" records a writer's deletion: absent is fine, a reappearing file is tamper."""
    f = log.run_dir / "r.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"1")
    log.append("W", {}, file="r.json", file_sha=canon.sha256_bytes(b"1"))
    f.unlink()
    log.append("DEL", {}, file="r.json", file_sha="")
    log.check_file(f, "r.json")
    f.write_bytes(b"1")
    with pytest.raises(TamperError):
        log.check_file(f, "r.json")


# --- review fixes: raw-line hashing (D), chain-started marker (11), locking (3), fsync (4) ---

def test_whitespace_variant_of_a_valid_line_is_tamper(log):
    """The parsed object is identical, but the bytes on disk are not the canonical bytes that were hashed."""
    log.append("A", {"x": 1})
    p = log.run_dir / "events.jsonl"
    e = json.loads(p.read_bytes())
    p.write_bytes(json.dumps(e, sort_keys=True).encode() + b"\n")  # default separators add spaces
    with pytest.raises(TamperError):
        log.verify()


def test_duplicate_key_line_is_tamper(log):
    log.append("A", {"x": 1})
    p = log.run_dir / "events.jsonl"
    p.write_bytes(b'{"type":"FORGED",' + p.read_bytes()[1:])  # json.loads keeps the last "type"
    with pytest.raises(TamperError):
        log.verify()


def test_events_verifies_the_chain(log):
    log.append("A", {"x": 1})
    p = log.run_dir / "events.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"x":1', b'"x":9'))
    with pytest.raises(TamperError):
        log.events()


def test_deleting_log_and_head_after_first_event_is_tamper(log):
    log.append("A", {})
    (log.run_dir / "events.jsonl").unlink()
    log.head_file.unlink()
    with pytest.raises(TamperError):
        log.verify()
    with pytest.raises(TamperError):
        log.events()
    with pytest.raises(TamperError):
        log.append("B", {})


def test_chain_started_marker_sits_next_to_head(log):
    assert not log.head_file.with_name("j1.started").exists()
    log.append("A", {})
    assert log.head_file.with_name("j1.started").exists()


def test_crash_between_event_line_and_head_fails_closed(log):
    """Crash window: the line was written, the head was not. Fail closed; operator repair recovers."""
    log.append("A", {})
    head = log.head_file.read_bytes()
    log.append("B", {})
    log.head_file.write_bytes(head)
    with pytest.raises(TamperError):
        log.verify()


def test_append_fsyncs_the_line_and_the_head(log, monkeypatch):
    import os as _os
    calls = []
    real = _os.fsync
    monkeypatch.setattr(_os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    log.append("A", {})
    assert len(calls) >= 2


_WORKER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from harness_core.events import EventLog
log = EventLog(sys.argv[2], sys.argv[3])
for i in range(int(sys.argv[4])):
    log.append("W", {"w": sys.argv[5], "i": i})
"""


def _spawn(code, *args):
    import subprocess
    import sys
    from pathlib import Path
    lib = str(Path(__file__).resolve().parents[1] / "lib")
    return subprocess.Popen([sys.executable, "-c", code, lib, *map(str, args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_concurrent_appends_keep_one_chain(log):
    log.append("START", {})
    procs = [_spawn(_WORKER, log.run_dir, log.head_file, 30, w) for w in ("a", "b")]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    log.verify()
    assert sum(1 for e in log.events() if e["type"] == "W") == 60


def test_append_waits_for_the_lock(log):
    import fcntl
    import threading
    log.append("A", {})
    fd = open(log.lock_path, "a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    t = threading.Thread(target=log.append, args=("B", {}))
    t.start()
    t.join(0.5)
    assert t.is_alive(), "append ran while another process held the lock"
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()
    t.join(10)
    assert [e["type"] for e in log.events()] == ["A", "B"]
