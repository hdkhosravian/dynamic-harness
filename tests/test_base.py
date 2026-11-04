from __future__ import annotations

import os

import pytest

from harness_core import canon, safeio
from harness_core.errors import TamperError
from harness_core.paths import job_paths


def test_canonical_json_sorted_compact_utf8():
    assert canon.canonical_json({"b": 1, "a": "ی"}) == '{"a":"ی","b":1}'.encode("utf-8")


def test_sha256_json_stable_across_key_order():
    assert canon.sha256_json({"a": 1, "b": 2}) == canon.sha256_json({"b": 2, "a": 1})


def test_scope_bytes_lines_newline_terminated():
    b = canon.scope_bytes([{"id": "u1"}, {"id": "u2"}])
    assert b == b'{"id":"u1"}\n{"id":"u2"}\n'


def test_hmac_depends_on_key_and_fields():
    f = {"x": 1}
    assert canon.hmac_receipt(b"k1", f) != canon.hmac_receipt(b"k2", f)
    assert canon.hmac_receipt(b"k1", f) != canon.hmac_receipt(b"k1", {"x": 2})


def test_tree_sha_changes_when_content_changes(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("1")
    s1 = canon.tree_sha([p], tmp_path)
    p.write_text("2")
    assert canon.tree_sha([p], tmp_path) != s1


def test_job_paths_layout(env):
    jp = job_paths(env["data"], env["ws"], "j1")
    assert jp.run == env["data"] / "runs" / "j1"
    assert jp.work == env["ws"] / ".claude" / "loops" / "j1" / "work"
    assert jp.requests == env["ws"] / ".claude" / "loops" / "j1" / "requests"
    assert jp.heads == env["data"] / "heads" / "j1"


def test_safe_open_rejects_symlink(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("k")
    work = tmp_path / "work"
    work.mkdir()
    (work / "x").symlink_to(secret)
    with pytest.raises(TamperError):
        safeio.safe_open(work / "x", [work])


def test_safe_open_rejects_hardlink(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("k")
    work = tmp_path / "work"
    work.mkdir()
    os.link(secret, work / "x")
    with pytest.raises(TamperError):
        safeio.safe_open(work / "x", [work])


def test_safe_open_rejects_outside_roots(tmp_path):
    f = tmp_path / "f"
    f.write_text("1")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(TamperError):
        safeio.safe_open(f, [other])


def test_safe_open_reads_regular_file(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "a").write_bytes(b"hi")
    assert safeio.safe_open(work / "a", [work]) == b"hi"


def test_atomic_write(tmp_path):
    p = tmp_path / "d" / "f.json"
    safeio.atomic_write(p, b"x")
    assert p.read_bytes() == b"x"


def test_safe_open_does_not_follow_a_symlinked_root(tmp_path):
    """A root that is itself an attacker symlink must not widen what is readable (`ln -s keys work`)."""
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "k").write_bytes(b"secret")
    work = tmp_path / "work"
    work.symlink_to(keys)
    with pytest.raises(TamperError):
        safeio.safe_open(work / "k", [work])
