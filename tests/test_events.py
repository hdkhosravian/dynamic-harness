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
